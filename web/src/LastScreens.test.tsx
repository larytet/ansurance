import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const server = (roles: string[], caps: string[], ...extra: Handler[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined), quiet, ...extra);
beforeEach(() => { localStorage.clear(); });

describe("complaint owner", () => {
  const row = { id: "k1", kind: "complaint", text: "slow claim", status: "open", overdue: false, due_at: "2026-11-21T00:00:00Z", response: "", owner_id: null };
  const people = [{ id: "u1", name: "Ann", roles: ["agent"] }, { id: "u2", name: "Bo", roles: ["backoffice"] }];

  it("lets the manager choose an owner from the users", async () => {
    const m = server(["manager"], ["complaint.write", "flow.approve"],
      (mt, p) => (mt === "GET" && p === "/api/complaints" ? { json: [row] } : undefined),
      (mt, p) => (mt === "GET" && p === "/api/users" ? { json: people } : undefined),
      (mt, p) => (mt === "PUT" && p === "/api/complaints/k1/owner" ? { json: {} } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "תלונות" }));
    const pick = await screen.findByRole("combobox", { name: /אחראי k1/ });
    await screen.findByRole("option", { name: "Bo" });
    await userEvent.selectOptions(pick, "u2");
    await waitFor(() => expect(m.calls.find((c) => c.method === "PUT")?.body).toEqual({ owner_id: "u2" }));
    expect(m.calls.find((c) => c.method === "PUT")?.csrf).toBe("tok");
  });

  it("offers no owner choice to an agent, and never asks for the user list", async () => {
    const m = server(["agent"], ["complaint.write"], (mt, p) => (mt === "GET" && p === "/api/complaints" ? { json: [row] } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "תלונות" }));
    await screen.findByText("slow claim");
    expect(screen.queryByRole("combobox", { name: /אחראי/ })).toBeNull();
    expect(m.calls.some((c) => c.path === "/api/users")).toBe(false);
  });
});

describe("statement file upload", () => {
  it("is disabled until the file and the three fields are given, then posts them and shows the result", async () => {
    mount(server(["backoffice"], ["commission.manage"]).fetcher);
    const seen: { url: string; csrf: string; fields: Record<string, string> }[] = [];
    vi.stubGlobal("fetch", (async (url: string, init: RequestInit) => {
      const f = init.body as FormData;
      seen.push({ url, csrf: (init.headers as Record<string, string>)["x-csrf-token"] ?? "", fields: { insurer: String(f.get("insurer")), period: String(f.get("period")), declared_total: String(f.get("declared_total")), file: (f.get("file") as File).name } });
      return new Response(JSON.stringify({ id: "s1", period: "2026-10", status: "open", declared_total: "112.50", lines_total: "112.50", ties: true, counts: { matched: 1, unknown_policy: 1 }, lines: [] }), { status: 201 });
    }) as unknown as typeof fetch);
    await userEvent.click(await screen.findByRole("button", { name: "עמלות" }));
    const go = screen.getByRole("button", { name: "ייבוא הקובץ" });
    await userEvent.upload(screen.getByLabelText(/או העלאת הדוח/), new File(["x"], "october.xlsx"));
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("מבטח"), "Harel");
    await userEvent.type(screen.getByLabelText(/תקופה/), "2026-10");
    await userEvent.type(screen.getByLabelText(/סכום בדוח/), "112.50");
    expect(go).toBeEnabled();
    await userEvent.click(go);
    expect(await screen.findByRole("status")).toHaveTextContent("הסכום תואם לדוח: כן (112.50 / 112.50)");
    expect(seen).toEqual([{ url: "/api/commissions/statements/xlsx", csrf: "tok", fields: { insurer: "Harel", period: "2026-10", declared_total: "112.50", file: "october.xlsx" } }]);
    vi.unstubAllGlobals();
  });

  it("shows the server's reason when the file cannot be read", async () => {
    mount(server(["backoffice"], ["commission.manage"]).fetcher);
    vi.stubGlobal("fetch", (async () => new Response(JSON.stringify({ detail: "policy_or_amount_column_not_found" }), { status: 422 })) as unknown as typeof fetch);
    await userEvent.click(await screen.findByRole("button", { name: "עמלות" }));
    await userEvent.upload(screen.getByLabelText(/או העלאת הדוח/), new File(["x"], "bad.xlsx"));
    await userEvent.type(screen.getByLabelText("מבטח"), "Harel");
    await userEvent.type(screen.getByLabelText(/תקופה/), "2026-10");
    await userEvent.type(screen.getByLabelText(/סכום בדוח/), "1");
    await userEvent.click(screen.getByRole("button", { name: "ייבוא הקובץ" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("policy_or_amount_column_not_found");
    vi.unstubAllGlobals();
  });
});
