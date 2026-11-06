import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { Portal } from "./pages/Portal";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 2, urgent: 1, done_today: 0 } } : undefined;
const server = (roles: string[], caps: string[], ...extra: Handler[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined), quiet, ...extra);

beforeEach(() => { localStorage.clear(); });

describe("Today (agent)", () => {
  it("lists renewals with their stage, marks the urgent ones and opens the client", async () => {
    const m = server(["agent"], ["client.read", "policy.read"],
      (_x, p) => (p === "/api/renewals?within=60" ? { json: [
        { policy_id: "p1", client_id: "c1", client: "David Cohen", product: "car", renewal_date: "2026-11-12", days: 5, stage: "t10" },
        { policy_id: "p2", client_id: "c2", client: "Dana Levi", product: "home", renewal_date: "2026-12-20", days: 43, stage: "t60" }] } : undefined),
      (_x, p) => (p === "/api/clients/c1" ? { json: { id: "c1", name: "David Cohen", id_number: "1", name_translit_he: "", name_translit_lat: "", preferred_language: "he", status: "active", phone: null, email: null, family: [] } } : p.startsWith("/api/clients/c1/") ? { json: p.endsWith("consent") ? { current: {}, history: [] } : [] } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "היום" }));
    const li = (await screen.findByText("David Cohen")).closest("li") as HTMLElement;
    expect(li.className).toBe("attention");
    expect((screen.getByText("Dana Levi").closest("li") as HTMLElement).className).toBe("");
    expect(li).toHaveTextContent("10 ימים (5 ימים)");
    await userEvent.click(screen.getByRole("button", { name: "David Cohen" }));
    await waitFor(() => expect(m.calls.some((c) => c.path === "/api/clients/c1")).toBe(true));
  });
});

describe("Complaints", () => {
  const row = (over: object = {}) => ({ id: "k1", kind: "complaint", text: "slow claim", status: "open", overdue: false, due_at: "2026-11-21T00:00:00Z", response: "", ...over });

  it("logs a complaint, shows overdue ones in red and records a response", async () => {
    let list = [row({ overdue: true })];
    const m = server(["agent"], ["complaint.write"],
      (mt, p) => (mt === "GET" && p === "/api/complaints" ? { json: list } : undefined),
      (mt, p) => { if (mt === "POST" && p === "/api/complaints") { list = [...list, row({ id: "k2", text: "late letter" })]; return { status: 201, json: {} }; } return undefined; },
      (mt, p) => { if (mt === "POST" && p === "/api/complaints/k1/respond") { list = [row({ status: "answered" })]; return { json: {} }; } return undefined; });
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "תלונות" }));
    expect((await screen.findByText("slow claim")).closest("li")?.className).toBe("attention");
    expect(screen.queryByRole("link", { name: /CSV/ })).toBeNull();  // export is for the manager
    await userEvent.type(screen.getByLabelText(/מה קרה/), "late letter");
    await userEvent.click(screen.getByRole("button", { name: "יצירה" }));
    await waitFor(() => expect(m.calls.find((c) => c.method === "POST" && c.path === "/api/complaints")?.body).toEqual({ kind: "complaint", text: "late letter" }));
    await screen.findByText("late letter");
    await userEvent.click(screen.getAllByRole("button", { name: "רישום תשובה" })[0] as HTMLElement);
    await userEvent.type(screen.getByLabelText(/^תשובה/), "we are sorry");
    await userEvent.click(screen.getByRole("button", { name: "רישום" }));
    await waitFor(() => expect(m.calls.find((c) => c.path === "/api/complaints/k1/respond")?.body).toEqual({ response: "we are sorry" }));
  });

  it("offers the export to a manager", async () => {
    mount(server(["manager"], ["complaint.write", "flow.approve"], (_x, p) => (p === "/api/complaints" ? { json: [] } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "תלונות" }));
    expect(await screen.findByRole("link", { name: "ייצוא (CSV)" })).toHaveAttribute("href", "/api/complaints/export.csv");
  });
});

describe("Compliance on the Overview", () => {
  it("shows licence states and counts", async () => {
    mount(server(["manager"], ["flow.approve"],
      (_x, p) => (p === "/api/overview" ? { json: { inbox_urgent: 0, renewals_overdue: 0, renewals_next_30: 0, claims_open: 0, intake_waiting_review: 0, kyc_waiting_approval: 0, campaigns_sending: 0, clients: 0, complaints_overdue: 0, licences_attention: 1 } } : undefined),
      (_x, p) => (p === "/api/compliance" ? { json: { licences: [{ user: "Ann", licence_no: "L-1", expires_on: "2026-11-01", state: "expired", cpd_missing_hours: 8 }, { user: "Bo", licence_no: "L-2", expires_on: "2027-11-01", state: "valid", cpd_missing_hours: 0 }], kyc_expiring: 3, complaints_unanswered: 2, complaints_overdue: 1, privacy_requests_open: 1 } } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "סקירה" }));
    expect((await screen.findByText(/L-1/)).closest("tr")?.className).toBe("attention");
    expect(screen.getByText(/L-2/).closest("tr")?.className).toBe("");
    expect(screen.getByText(/תיקי הכר את לקוחך שפג תוקפם: 3/)).toBeInTheDocument();
    expect(screen.getByText("פג תוקף")).toBeInTheDocument();
  });
});

describe("Commissions", () => {
  const stmt = (status = "open") => ({ id: "s1", period: "2026-10", status, declared_total: "150.00", lines_total: "150.00", ties: true, counts: { matched: 1, wrong_amount: 1 },
    lines: [{ policy_number: "P1", amount: "100.00", expected: "100.00", result: "matched" }, { policy_number: "P2", amount: "50.00", expected: "60.00", result: "wrong_amount" }] });

  it("imports a statement, shows the differences, and a manager approves and sees the payout", async () => {
    let status = "open";
    const m = server(["manager"], ["commission.manage", "flow.approve"],
      (mt, p) => (mt === "POST" && p === "/api/commissions/statements" ? { status: 201, json: stmt() } : undefined),
      (mt, p) => { if (mt === "POST" && p === "/api/commissions/statements/s1/approve") { status = "approved"; return { json: stmt("approved") }; } return undefined; },
      (_x, p) => (p === "/api/commissions/statements/s1/payout" && status === "approved" ? { json: { agents: { Ann: "150.00" }, total: "150.00" } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "עמלות" }));
    await userEvent.type(await screen.findByLabelText("מבטח"), "Harel");
    await userEvent.type(screen.getByLabelText(/תקופה/), "2026-10");
    await userEvent.type(screen.getByLabelText(/סכום בדוח/), "150.00");
    await userEvent.type(screen.getByLabelText(/שורות/), "P1,100.00{enter}P2;50.00");
    await userEvent.click(screen.getByRole("button", { name: "ייבוא דוח עמלות" }));
    await waitFor(() => expect(m.calls.find((c) => c.path === "/api/commissions/statements")?.body).toEqual({ insurer: "Harel", period: "2026-10", declared_total: "150.00", lines: [{ policy_number: "P1", amount: "100.00" }, { policy_number: "P2", amount: "50.00" }] }));
    expect(await screen.findByRole("status")).toHaveTextContent("הסכום תואם לדוח: כן (150.00 / 150.00)");
    expect(screen.getByText(/wrong_amount 1/)).toBeInTheDocument();
    expect(screen.getByText("P2").closest("tr")?.className).toBe("attention");
    await userEvent.click(screen.getByRole("button", { name: "אישור הדוח" }));
    await userEvent.click(await screen.findByRole("button", { name: "תשלום לכל סוכן" }));
    await waitFor(() => expect(document.querySelector("li bdi")?.parentElement?.textContent).toBe("Ann: 150.00"));
  });

  it("shows the server's reason when the totals do not tie, and hides approval from back-office", async () => {
    const m = server(["backoffice"], ["commission.manage"],
      (mt, p) => (mt === "POST" && p === "/api/commissions/statements" ? { status: 422, json: { detail: { error: "totals_do_not_tie", detail: { difference: "0.01" } } } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "עמלות" }));
    await userEvent.type(await screen.findByLabelText("מבטח"), "Harel");
    await userEvent.type(screen.getByLabelText(/תקופה/), "2026-10");
    await userEvent.type(screen.getByLabelText(/סכום בדוח/), "1");
    await userEvent.type(screen.getByLabelText(/שורות/), "P1,1.01");
    await userEvent.click(screen.getByRole("button", { name: "ייבוא דוח עמלות" }));
    expect(await screen.findByRole("alert")).toHaveTextContent('totals_do_not_tie: {"difference":"0.01"}');
    expect(screen.queryByRole("button", { name: "אישור הדוח" })).toBeNull();
  });
});

describe("Tax", () => {
  it("shows the three marks per certificate and runs the bulk actions only for those who may", async () => {
    const m = server(["backoffice"], ["tax.read", "tax.write"],
      (mt, p) => (mt === "GET" && p.startsWith("/api/tax/certificates?tax_year=") ? { json: [{ id: "t1", client: "David Cohen", product: "pension", policy_number: "P1", status: "requested", requested: true, received: false, sent: false }] } : undefined),
      (mt, p) => (mt === "POST" && p === "/api/tax/certificates/request-all" ? { json: { requested: 1 } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "מס" }));
    const row = (await screen.findByText("David Cohen")).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("כןלאלא");
    await userEvent.click(screen.getByRole("button", { name: "בקשת כל החסרים" }));
    await waitFor(() => expect((m.calls.find((c) => c.path === "/api/tax/certificates/request-all")?.body as { tax_year: number }).tax_year).toBeGreaterThan(2000));
  });

  it("is read-only for the accountant", async () => {
    mount(server(["accountant"], ["tax.read"], (_x, p) => (p.startsWith("/api/tax/certificates") ? { json: [] } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "מס" }));
    await screen.findByText("אישורי הפקדות");
    expect(screen.queryByRole("button", { name: "בקשת כל החסרים" })).toBeNull();
    expect(screen.queryByRole("button", { name: "תיבה" })).not.toBeNull();
  });
});

describe("the client's page", () => {
  const summary = { name: "David Cohen", language: "en", policies: [{ insurer: "Harel", product: "car", policy_number: "P1", status: "active", renewal_date: "2027-01-01" }], documents: [{ type: "policy", filename: "p.pdf", received_at: "2026-11-01" }], claims: [] };

  it("reads the token from the fragment, sends it as a header and shows only the client's data", async () => {
    window.history.replaceState(null, "", "/portal#secret-token");
    const seen: { path: string; token: string }[] = [];
    const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push({ path: String(input), token: (init?.headers as Record<string, string>)["x-portal-token"] ?? "" });
      return new Response(JSON.stringify(summary), { status: 200 });
    }) as typeof fetch;
    render(<Portal fetcher={fetcher} />);
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Your insurance file: David Cohen");
    expect(seen).toEqual([{ path: "/api/portal/summary", token: "secret-token" }]);
    expect(screen.getByText(/Harel · car · P1 · Renews 2027-01-01/)).toBeInTheDocument();
    expect(document.querySelector("main")?.getAttribute("dir")).toBe("ltr");
  });

  it("says the link is not valid, without saying why", async () => {
    window.history.replaceState(null, "", "/portal#old");
    const fetcher = (async () => new Response("{}", { status: 404 })) as typeof fetch;
    render(<Portal fetcher={fetcher} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("הקישור אינו תקף או שפג תוקפו");
  });
});
