import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ME, fakeServer, mount, type Handler } from "../testutil";

// the canvas library needs a real layout engine; the browser test covers it. Here it is a plain list.
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes, onNodeClick }: { nodes: { id: string; data: { label: string } }[]; onNodeClick: (e: unknown, n: { id: string }) => void }) => (
    <div data-testid="rf">{nodes.map((n) => <button key={n.id} data-node={n.id} onClick={(e) => onNodeClick(e, n)}>{n.data.label}</button>)}</div>
  ),
  Background: () => null, Controls: () => null, applyNodeChanges: (_c: unknown, n: unknown) => n,
}));

const t = (s: string) => ({ en: s, he: `${s}-he` });
const chk = [{ text: t("item"), kind: "do" }];
const DEF = {
  id: "onb", owner_role: "agent", name: { en: "Onboarding", he: "קליטה" },
  stages: [
    { id: "a", name: t("First"), role: "backoffice", depends_on: [], checks: chk },
    { id: "b", name: t("Second"), role: "agent", depends_on: ["a"], checks: chk },
    { id: "c", name: t("Third"), role: "manager", depends_on: [], checks: chk },
  ],
};
const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;

let verdict: { ok: boolean; errors: object[]; current_version: number | null; running_cases: number };
const server = (caps = ["process.read", "process.define", "inbox.read"], extra: Handler[] = []) => fakeServer(
  (_m, p) => (p === "/api/auth/me" ? { json: ME(["manager"], caps) } : undefined),
  quiet,
  (m, p) => (m === "GET" && p === "/api/process-defs" ? { json: [{ key: "onb", name: DEF.name, source_flow: "F1", stages: 3, checks: 3 }] } : undefined),
  (m, p) => (m === "GET" && p === "/api/process-defs/onb" ? { json: { key: "onb", version: 1, definition: DEF } } : undefined),
  (m, p) => (m === "GET" && p.startsWith("/api/process-defs/onb/mermaid") ? { json: {} } : undefined),
  (m, p) => (m === "POST" && p === "/api/process-defs/validate" ? { json: verdict } : undefined),
  ...extra,
);
async function openEditor(s: ReturnType<typeof server>) {
  mount(s.fetcher);
  await userEvent.click(await screen.findByRole("button", { name: "Processes" }));
  await userEvent.click(await screen.findByRole("button", { name: "Edit this definition" }));
  await screen.findByRole("region", { name: "Edit this definition" });
}
const stages = () => within(screen.getByRole("navigation", { name: "Stages" }));

beforeEach(() => {
  localStorage.clear(); localStorage.setItem("lang", "en");
  verdict = { ok: true, errors: [], current_version: 1, running_cases: 0 };
});

describe("process editor", () => {
  it("is offered only with the define capability, and loads on demand", async () => {
    mount(server(["process.read", "inbox.read"]).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Processes" }));
    await screen.findByRole("heading", { name: "Checklists" });
    expect(screen.queryByRole("button", { name: "Edit this definition" })).toBeNull();
  });

  it("shows the stages, the server's verdict and the canvas, with nothing to publish yet", async () => {
    await openEditor(server());
    expect(stages().getAllByRole("button").map((b) => b.textContent)).toEqual(["First", "Second", "Third"]);
    expect(await screen.findByText(/the server accepts this draft/)).toBeInTheDocument();
    expect(screen.getByTestId("rf")).toBeInTheDocument();
    expect(screen.getByText("No changes.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish a new version" })).toBeDisabled();
  });

  it("locks the id of a published stage but not of a new one", async () => {
    await openEditor(server());
    expect(screen.getByLabelText(/Stage id/)).toHaveAttribute("readonly");
    await userEvent.click(screen.getByRole("button", { name: "Add a stage" }));
    expect(screen.getByLabelText(/Stage id/)).not.toHaveAttribute("readonly");
    expect(stages().getAllByRole("button")).toHaveLength(4);
  });

  it("refuses a dependency that would make a loop, with the reason, and accepts a valid one by keyboard alone", async () => {
    await openEditor(server());
    await userEvent.click(stages().getByRole("button", { name: "First" })); // a: b already depends on a
    await userEvent.click(within(screen.getByRole("group", { name: /Depends on/ })).getByRole("checkbox", { name: "Second" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That would make a loop");
    await userEvent.click(stages().getByRole("button", { name: "Third" }));
    const dep = within(screen.getByRole("group", { name: /Depends on/ })).getByRole("checkbox", { name: "Second" });
    await userEvent.click(dep);
    expect(dep).toBeChecked();
    expect(screen.getByText(/added: c · dependency b/)).toBeInTheDocument();
  });

  it("shows the server's errors against the stage and blocks publishing until there are none", async () => {
    verdict = { ok: false, current_version: 1, running_cases: 0, errors: [{ code: "no_tickable_check", message: "stage a has no tickable checks", stage: "a", check: null, edge: null }] };
    await openEditor(server());
    expect(await screen.findByText(/stage a has no tickable checks/)).toBeInTheDocument();
    expect(stages().getByRole("button", { name: "First" })).toHaveClass("bad");
    await userEvent.click(screen.getByRole("button", { name: "Add a stage" }));
    expect(screen.getByRole("button", { name: "Publish a new version" })).toBeDisabled();
  });

  it("publishes the draft with the stages in order, says which version, and reports running cases", async () => {
    verdict = { ok: true, errors: [], current_version: 1, running_cases: 3 };
    const s = server(undefined, [(m, p) => (m === "POST" && p === "/api/process-defs" ? { status: 201, json: { key: "onb", version: 2 } } : undefined)]);
    await openEditor(s);
    await userEvent.click(screen.getByRole("button", { name: "Add a stage" }));
    expect(await screen.findByText(/Running now: 3/)).toBeInTheDocument();
    const publish = screen.getByRole("button", { name: "Publish a new version" });
    await waitFor(() => expect(publish).toBeEnabled());
    await userEvent.click(publish);
    expect(await screen.findByText(/Published as version 2/)).toBeInTheDocument();
    const post = s.calls.find((c) => c.method === "POST" && c.path === "/api/process-defs");
    const sent = post?.body as { id: string; stages: { id: string }[] };
    expect(sent.id).toBe("onb");
    expect(sent.stages.map((x) => x.id)).toEqual(["a", "b", "c", "stage4"]);
    expect(post?.csrf).toBe("tok");
  });

  it("edits a checklist: a kind, the verify note, a new item, reordering, removal", async () => {
    await openEditor(server());
    await userEvent.click(screen.getByRole("button", { name: "Add an item" }));
    const items = () => within(document.querySelector(".pe-items") as HTMLElement).getAllByRole("listitem");
    expect(items()).toHaveLength(2);
    await userEvent.selectOptions(within(items()[1] as HTMLElement).getByLabelText("Kind"), "verify");
    await userEvent.click(within(items()[1] as HTMLElement).getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Move up 2" }));
    expect(within(items()[0] as HTMLElement).getByLabelText("Kind")).toHaveValue("verify");
    expect(within(items()[0] as HTMLElement).getByRole("checkbox")).toBeChecked();
    expect(screen.getByText(/changed: a · item order|added: a · item New item/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove the item 1" }));
    expect(items()).toHaveLength(1);
  });

  it("undo and redo step back and forth through the edits", async () => {
    await openEditor(server());
    const undoBtn = screen.getByRole("button", { name: "Undo" });
    expect(undoBtn).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Add a stage" }));
    expect(stages().getAllByRole("button")).toHaveLength(4);
    await userEvent.click(undoBtn);
    expect(stages().getAllByRole("button")).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Redo" }));
    expect(stages().getAllByRole("button")).toHaveLength(4);
  });

  it("an imported file is checked by the server first: an invalid one is refused and nothing changes", async () => {
    const s = server();
    await openEditor(s);
    verdict = { ok: false, current_version: 1, running_cases: 0, errors: [{ code: "unknown_stage", message: "stage x depends on unknown stage y", stage: "x", check: null, edge: ["y", "x"] }] };
    const file = new File(["{}"], "bad.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => JSON.stringify({ ...DEF, stages: [{ ...DEF.stages[0], id: "x", depends_on: ["y"] }] }) });
    await userEvent.upload(screen.getByLabelText("Import JSON", { selector: "input" }), file);
    expect(await screen.findByText(/The file was not loaded: stage x depends on unknown stage y/)).toBeInTheDocument();
    expect(stages().getAllByRole("button").map((b) => b.textContent)).toEqual(["First", "Second", "Third"]);
  });

  it("a valid import becomes the draft and can be undone", async () => {
    await openEditor(server());
    const file = new File(["{}"], "ok.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => JSON.stringify({ ...DEF, stages: [DEF.stages[0]] }) });
    await userEvent.upload(screen.getByLabelText("Import JSON", { selector: "input" }), file);
    await waitFor(() => expect(stages().getAllByRole("button")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(stages().getAllByRole("button")).toHaveLength(3);
  });
});
