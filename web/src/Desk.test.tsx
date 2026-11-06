import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const server = (caps: string[], ...extra: Handler[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(["backoffice"], caps) } : undefined), quiet, ...extra);

const row = (over: object = {}) => ({
  id: "s1", state: "review", channel: "fax", sender: "", filename: "fax-17.pdf", doc_type: "policy", type_conf: 0.91, type_reason: "",
  route: "review", route_reason: "two clients share this policy number", client_id: null, received_at: "2026-11-07T10:00:00Z",
  candidates: [
    { client_id: "c1", score: 0.62, evidence: ["policy number"], sender_match: false, weak: true },
    { client_id: "c2", score: 0.58, evidence: ["name"], sender_match: true, weak: false },
  ], ...over,
});

beforeEach(() => { localStorage.clear(); });

describe("Desk", () => {
  it("shows the work areas and marks the ones with urgent items", async () => {
    mount(server(["desk.read"], (_m, p) => (p === "/api/desk" ? { json: { total_open: 6, total_urgent: 1, areas: [
      { area: "id_registration", open: 4, urgent: 1 }, { area: "tax", open: 2, urgent: 0 }, { area: "intake", open: 0, urgent: 0 }] } } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "שולחן עבודה" }));
    expect(await screen.findByText("זהות ורישום")).toBeInTheDocument();
    expect((screen.getByText("זהות ורישום").closest("li") as HTMLElement).className).toBe("attention");
    expect((screen.getByText("מס").closest("li") as HTMLElement).className).toBe("");
    expect(screen.getByText(/פתוחים: 6/)).toBeInTheDocument();
  });

  it("is not offered without the desk right", async () => {
    mount(server(["client.read"]).fetcher);
    await screen.findByRole("button", { name: "לקוחות" });
    expect(screen.queryByRole("button", { name: "שולחן עבודה" })).toBeNull();
  });
});

describe("Scans waiting for a person", () => {
  const clients = (_m: string, p: string) => (p === "/api/clients/c1" ? { json: { name: "David Cohen" } } : p === "/api/clients/c2" ? { json: { name: "Dana Levi" } } : undefined);

  it("lists each scan with its candidates, evidence and a weak-match warning, and files only on confirmation", async () => {
    let queue = [row()];
    const m = server(["intake.review"], clients,
      (_m, p) => (p === "/api/intake?state=review" ? { json: queue } : p === "/api/intake?state=unassigned" ? { json: [row({ id: "s2", state: "unassigned", filename: "mail.pdf", candidates: [], route_reason: "no identifier found" })] } : undefined),
      (mt, p) => { if (mt === "POST" && p === "/api/intake/s1/confirm") { queue = []; return { json: row({ state: "filed" }) }; } return undefined; });
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "סריקות" }));
    expect(await screen.findByText("fax-17.pdf")).toBeInTheDocument();
    expect(await screen.findByText("David Cohen")).toBeInTheDocument();
    expect(screen.getByText(/התאמה חלשה: הצעה בלבד/)).toBeInTheDocument();
    expect(screen.getByText("mail.pdf")).toBeInTheDocument();
    expect(m.calls.some((c) => c.method === "POST")).toBe(false);  // nothing is filed until a person decides
    await userEvent.click(screen.getAllByRole("button", { name: "תייק ללקוח זה" })[1] as HTMLElement);
    await waitFor(() => expect(m.calls.find((c) => c.method === "POST")?.body).toEqual({ client_id: "c2" }));
    expect(m.calls.find((c) => c.method === "POST")?.csrf).toBe("tok");
    await waitFor(() => expect(screen.queryByText("fax-17.pdf")).toBeNull());
  });

  it("shows the server's reason when filing is refused", async () => {
    const m = server(["intake.review"], clients,
      (_m, p) => (p === "/api/intake?state=review" ? { json: [row()] } : p === "/api/intake?state=unassigned" ? { json: [] } : undefined),
      (mt, p) => (mt === "POST" && p === "/api/intake/s1/confirm" ? { status: 409, json: { detail: "already_filed" } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "סריקות" }));
    await userEvent.click((await screen.findAllByRole("button", { name: "תייק ללקוח זה" }))[0] as HTMLElement);
    expect(await screen.findByRole("alert")).toHaveTextContent("already_filed");
  });

  it("says so when nothing is waiting", async () => {
    mount(server(["intake.review"], (_m, p) => (p.startsWith("/api/intake?state=") ? { json: [] } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "סריקות" }));
    expect(await screen.findByText("אין סריקות ממתינות.")).toBeInTheDocument();
  });
});
