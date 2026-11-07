import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { layers } from "./pages/Processes";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const stage = (id: string, depends_on: string[], role = "agent", checks: { text: { en: string; he: string }; kind: string }[] = []) => ({ id, name: { en: `Stage ${id}`, he: `שלב ${id}` }, role, depends_on, checks });
const DEF = {
  key: "onb", name: { en: "Onboarding", he: "קליטה" }, owner_role: "agent",
  stages: [
    stage("a", [], "backoffice", [{ text: { en: "Photo of the ID", he: "צילום תעודה" }, kind: "fill" }]),
    stage("b", ["a"], "agent", [{ text: { en: "Identity", he: "זהות" }, kind: "heading" }, { text: { en: "Questionnaire", he: "שאלון" }, kind: "do" }]),
    stage("c", ["a"], "manager"),
    stage("d", ["b", "c"], "backoffice"),
  ],
};
const MERMAID = "flowchart LR\n  a --> b";
const server = (caps = ["process.read", "inbox.read"]) =>
  fakeServer(
    (_m, p) => (p === "/api/auth/me" ? { json: ME(["manager"], caps) } : undefined),
    quiet,
    (_m, p) => (p === "/api/process-defs" ? { json: [{ key: "onb", name: DEF.name, source_flow: "F1", stages: 4, checks: 3 }] } : undefined),
    (_m, p) => (p === "/api/process-defs/onb" ? { json: { key: "onb", version: 1, definition: DEF } } : undefined),
  );

beforeEach(() => { localStorage.clear(); localStorage.setItem("lang", "en"); });

describe("process layers", () => {
  it("puts a stage one column right of its deepest dependency and parallel stages in one column", () => {
    const l = layers(DEF.stages);
    expect([l.get("a"), l.get("b"), l.get("c"), l.get("d")]).toEqual([0, 1, 1, 2]);
  });
  it("does not loop on a cycle", () => {
    expect(layers([stage("x", ["y"]), stage("y", ["x"])]).size).toBe(2);
  });
});

describe("Processes screen", () => {
  it("draws the pipeline with every stage and dependency, lists the checklists and the source", async () => {
    const base = server();
    const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).startsWith("/api/process-defs/onb/mermaid") ? new Response(MERMAID, { status: 200 }) : base.fetcher(input, init)) as typeof fetch;
    mount(fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Processes" }));
    const svg = await screen.findByRole("img", { name: "Onboarding" });
    expect(svg.querySelectorAll("[data-stage]")).toHaveLength(4);
    expect(Array.from(svg.querySelectorAll("[data-edge]")).map((e) => e.getAttribute("data-edge")).sort()).toEqual(["a>b", "a>c", "b>d", "c>d"]);
    const x = (id: string) => svg.querySelector(`[data-stage="${id}"]`)?.getAttribute("transform");
    expect(x("b")?.split(" ")[0]).toBe(x("c")?.split(" ")[0]); // parallel stages share a column
    expect(x("a")).not.toBe(x("b"));
    expect(screen.getByText("Photo of the ID")).toBeInTheDocument();
    expect(screen.getByText("Identity").tagName).toBe("EM"); // a heading line inside a checklist
    expect(await screen.findByLabelText("Diagram source (Mermaid)")).toHaveValue(MERMAID);
    const link = screen.getByRole("link", { name: "Edit in Mermaid Live" });
    expect(link.getAttribute("href")).toMatch(/^https:\/\/mermaid\.live\/edit#base64:/);
    expect(link.getAttribute("rel")).toContain("noreferrer");
    expect(screen.getByText(/no client data/)).toBeInTheDocument();
  });

  it("shows the Hebrew names when the language is Hebrew", async () => {
    localStorage.setItem("lang", "he");
    mount(server().fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "תהליכים" }));
    const svg = await screen.findByRole("img", { name: "קליטה" });
    expect(svg.querySelector('[data-stage="a"] text')?.textContent).toBe("שלב a"); // drawn in the diagram
    expect(screen.getAllByText("שלב a").length).toBeGreaterThan(1); // and named in the checklist
  });

  it("is not offered without process.read", async () => {
    mount(server(["inbox.read"]).fetcher);
    await screen.findByRole("button", { name: "Inbox" });
    expect(screen.queryByRole("button", { name: "Processes" })).toBeNull();
  });
});
