import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const CLIENT = {
  id: "c1", id_number: "123456782", name: "David Cohen", name_translit_he: "דוד כהן", name_translit_lat: "david cohen",
  preferred_language: "ru", status: "registered", phone: "050-111-2222", email: "david@x.il",
  family: [{ head: "c1", member: "c2", relation: "child" }],
};
const doc = (id: string, over: object = {}) => ({
  id, doc_type: "policy", filename: `${id}.pdf`, media_type: "application/pdf", size: 10, restricted: false,
  scan_status: "clean", channel: "upload", received_at: "2026-11-06T10:00:00Z", retention_until: "2033-11-06", legal_hold: false, ...over,
});

const ALL = ["client.read", "policy.read", "document.read", "document.write", "consent.read", "consent.write", "process.read", "process.start", "inbox.read"];
const base = (caps = ALL, extra: Handler[] = [], roles = ["backoffice"]) => fakeServer(
  (_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined),
  (_m, p) => (p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined),
  (_m, p) => (p.startsWith("/api/clients?") ? { json: [{ ...CLIENT }] } : undefined),
  ...extra,
  (_m, p) => (p === "/api/clients/c1" ? { json: CLIENT } : undefined),
  (_m, p) => (p === "/api/clients/c1/policies" ? { json: [{ id: "p1", insurer: "Harel", policy_number: "PL-1", product: "car", premium_ils: 1450.5, renewal_date: "2027-03-01" }] } : undefined),
  (_m, p) => (p === "/api/clients/c1/documents" && _m === "GET" ? { json: [doc("a"), doc("b", { restricted: true, filename: "medical.pdf", doc_type: "medical_report" }), doc("c", { scan_status: "infected", filename: "bad.pdf" }), doc("d", { scan_status: "pending", filename: "wait.pdf" })] } : undefined),
  (_m, p) => (p === "/api/clients/c1/consent" && _m === "GET" ? { json: { current: { whatsapp: "opt_in", email: "opt_out" }, history: [] } } : undefined),
  (_m, p) => (p.startsWith("/api/processes?") ? { json: [{ id: "i1", key: "onb", title: "New client onboarding", status: "running", stages: [{ id: "id", status: "final", role: "back", name: { en: "Identify", he: "זיהוי" } }, { id: "kyc", status: "ready", role: "agent", name: { en: "KYC", he: "הכר את לקוחך" } }, { id: "reg", status: "locked", role: "back", name: { en: "Register", he: "רישום" } }] }] } : undefined),
  (_m, p) => (p === "/api/process-defs" ? { json: [{ key: "onb", name: { en: "Onboarding", he: "קליטת לקוח" } }, { key: "retire", name: { en: "Retirement", he: "פרישה" } }] } : undefined),
);

async function openFile(s: ReturnType<typeof base>) {
  mount(s.fetcher);
  await userEvent.click(await screen.findByRole("button", { name: "לקוחות" }));
  await userEvent.click(await screen.findByRole("button", { name: "David Cohen" }));
  await screen.findByRole("heading", { name: /David Cohen/ });
}

beforeEach(() => { localStorage.clear(); });

describe("client file", () => {
  it("opens from the list and shows the details, with the transliteration when it differs", async () => {
    await openFile(base());
    expect(screen.getByRole("heading", { name: /David Cohen/ })).toHaveTextContent("דוד כהן");
    expect(screen.getByText("123456782")).toBeInTheDocument();
    expect(screen.getByText("registered")).toBeInTheDocument();
    expect(screen.getByText("050-111-2222")).toBeInTheDocument();
    expect(screen.getByText("child")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "חזרה לרשימה" }));
    expect(await screen.findByRole("searchbox")).toBeInTheDocument();
  });

  it("lists policies", async () => {
    await openFile(base());
    const li = (await screen.findByText("Harel")).closest("li") as HTMLElement;
    expect(li).toHaveTextContent("PL-1");
    expect(li).toHaveTextContent("₪1450.5");
    expect(li).toHaveTextContent("2027-03-01");
  });

  it("shows each document's scan status, offers download only for clean ones, and marks restricted", async () => {
    await openFile(base());
    const rows = (await screen.findByText("a.pdf")).closest("ul") as HTMLElement;
    const row = (name: string) => within(rows).getByText(name).closest("li") as HTMLElement;
    expect(within(row("a.pdf")).getByRole("link", { name: "הורדה" })).toHaveAttribute("href", "/api/documents/a/content");
    expect(within(row("medical.pdf")).getByText("מוגבל")).toBeInTheDocument();
    expect(within(row("bad.pdf")).queryByRole("link")).toBeNull(); // a quarantined file is never offered
    expect(within(row("bad.pdf")).getByText("בהסגר: נגוע")).toBeInTheDocument();
    expect(within(row("wait.pdf")).queryByRole("link")).toBeNull();
    expect(within(row("wait.pdf")).getByText("ממתין לסריקת וירוסים")).toBeInTheDocument();
  });

  it("uploads a document with the chosen type", async () => {
    const s = base(ALL, [(m, p) => (m === "POST" && p === "/api/clients/c1/documents" ? { status: 201, json: doc("new") } : undefined)]);
    await openFile(s);
    await userEvent.selectOptions(await screen.findByDisplayValue("other"), "claim_form");
    await userEvent.upload(screen.getByLabelText("בחרו קובץ"), new File(["%PDF-1.4"], "claim.pdf", { type: "application/pdf" }));
    await waitFor(() => expect(s.calls.find((c) => c.method === "POST" && c.path.endsWith("/documents"))?.body).toEqual({ file: "claim.pdf", doc_type: "claim_form" }));
    expect(s.calls.find((c) => c.method === "POST" && c.path.endsWith("/documents"))?.csrf).toBe("tok");
  });

  it("hides what the role may not do: no upload, no consent buttons, no policies", async () => {
    await openFile(base(["client.read", "document.read", "consent.read", "process.read"]));
    await screen.findByText("a.pdf");
    expect(screen.queryByLabelText("בחרו קובץ")).toBeNull();
    expect(screen.queryByRole("button", { name: "הסכמה" })).toBeNull();
    expect(screen.queryByText("Harel")).toBeNull();
    expect(screen.queryByRole("heading", { name: "פוליסות" })).toBeNull();
  });

  it("shows consent per channel and records a change only with a source", async () => {
    const s = base(ALL, [(m, p) => (m === "POST" && p === "/api/clients/c1/consent" ? { status: 201, json: {} } : undefined)]);
    await openFile(s);
    const wa = (await screen.findByText("וואטסאפ")).closest("li") as HTMLElement;
    expect(wa).toHaveTextContent("הסכים/ה");
    expect((screen.getByText("דוא״ל", { selector: "strong" }).closest("li") as HTMLElement)).toHaveTextContent("הוסר/ה");
    expect((screen.getByText("הודעת SMS").closest("li") as HTMLElement)).toHaveTextContent("לא נרשמה הסכמה");
    await userEvent.click(within(wa).getByRole("button", { name: "הסרה" }));
    expect(s.calls.some((c) => c.method === "POST" && c.path.endsWith("/consent"))).toBe(false); // no source yet
    await userEvent.type(screen.getByLabelText("מקור ההסכמה (חובה)"), "STOP reply");
    await userEvent.click(within(wa).getByRole("button", { name: "הסרה" }));
    await waitFor(() => expect(s.calls.find((c) => c.method === "POST" && c.path.endsWith("/consent"))?.body).toEqual({ channel: "whatsapp", state: "opt_out", source: "STOP reply" }));
  });

  it("shows running processes as a pipeline and starts another", async () => {
    const s = base(ALL, [(m, p) => (m === "POST" && p === "/api/processes" ? { status: 201, json: {} } : undefined)]);
    await openFile(s);
    const proc = (await screen.findByText("New client onboarding")).closest("li") as HTMLElement;
    const stages = within(proc).getAllByRole("listitem");
    expect(stages.map((x) => x.textContent)).toEqual(["זיהוי", "הכר את לקוחך", "רישום"]);
    expect(stages.map((x) => x.className)).toEqual(["stage final", "stage ready", "stage locked"]);
    await userEvent.selectOptions(screen.getByLabelText("פתיחת תהליך"), "retire");
    await userEvent.click(screen.getByRole("button", { name: "פתיחה" }));
    await waitFor(() => expect(s.calls.find((c) => c.method === "POST" && c.path === "/api/processes")?.body).toEqual({ key: "retire", client_id: "c1" }));
  });

  it("shows an error instead of a blank page when the client cannot be loaded", async () => {
    const s = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: ME(["agent"]) } : undefined),
      (_m, p) => (p.startsWith("/api/inbox") ? { json: { items: [], next: null, read_only: false, open: 0, urgent: 0, done_today: 0 } } : undefined),
      (_m, p) => (p.startsWith("/api/clients?") ? { json: [CLIENT] } : undefined),
      (_m, p) => (p === "/api/clients/c1" ? { status: 404, json: { detail: "not_found" } } : undefined),
    );
    mount(s.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "לקוחות" }));
    await userEvent.click(await screen.findByRole("button", { name: "David Cohen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("משהו השתבש");
  });

  describe("working a process", () => {
    const stage = (id: string, role: string, status: string, checks: { done?: boolean }[], deps: string[] = []) => ({
      id, role, status, depends_on: deps, name: { en: id, he: `שלב-${id}` },
      checks: [{ idx: 0, kind: "heading", text: { en: "Basics", he: "בסיס" } }, ...checks.map((c, i) => ({ idx: i + 1, kind: "check", text: { en: `item ${id}${i}`, he: `פריט ${id}${i}` }, done: !!c.done, done_by: c.done ? "u9" : null }))],
    });
    const view = (stages: ReturnType<typeof stage>[]) => ({
      id: "i1", title: "New client onboarding", status: "running", stages,
      progress: { checks_done: 0, checks_total: 1, stages_final: 0, stages_total: stages.length },
    });
    let state = view([]);
    const serve = (roles: string[], put?: (path: string) => { status?: number; json?: unknown }) => base(ALL, [
      (m, p) => (m === "GET" && p === "/api/processes/i1" ? { json: state } : undefined),
      (m, p) => (m === "PUT" && p.startsWith("/api/processes/i1/stages/") ? put?.(p) : undefined),
      (m, p) => (m === "POST" && p === "/api/processes/i1/stages/kyc/finalize" ? { json: state } : undefined),
    ], roles);
    const open = async () => { await userEvent.click(await screen.findByRole("button", { name: "פתיחת רשימות הבדיקה" })); };

    it("the stage's role ticks an item and the server's view replaces the screen", async () => {
      state = view([stage("kyc", "agent", "ready", [{}, {}]), stage("reg", "backoffice", "locked", [{}], ["kyc"])]);
      const s = serve(["agent"], () => { state = view([stage("kyc", "agent", "in_progress", [{ done: true }, {}]), stage("reg", "backoffice", "locked", [{}], ["kyc"])]); return { json: state }; });
      await openFile(s);
      await open();
      const box = await screen.findByRole("checkbox", { name: "פריט kyc0" });
      expect(box).toBeEnabled();
      expect(screen.getByRole("checkbox", { name: "פריט reg0" })).toBeDisabled(); // locked
      expect(screen.getByRole("button", { name: "סיום השלב" })).toBeDisabled(); // one item still open
      await userEvent.click(box);
      await waitFor(() => expect(s.calls.find((c) => c.method === "PUT")?.path).toBe("/api/processes/i1/stages/kyc/checks/1"));
      expect(s.calls.find((c) => c.method === "PUT")?.body).toEqual({ done: true });
      await waitFor(() => expect(screen.getByRole("checkbox", { name: "פריט kyc0" })).toBeChecked());
    });

    it("another role sees the checklist but cannot tick or finish", async () => {
      state = view([stage("kyc", "agent", "ready", [{}])]);
      await openFile(serve(["backoffice"]));
      await open();
      expect(await screen.findByRole("checkbox", { name: "פריט kyc0" })).toBeDisabled();
      expect(screen.getByText("השלב הזה שייך לתפקיד אחר.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "סיום השלב" })).toBeNull();
    });

    it("finishing is offered when every item is ticked, and calls finalize", async () => {
      state = view([stage("kyc", "agent", "in_progress", [{ done: true }])]);
      const s = serve(["agent"]);
      await openFile(s);
      await open();
      await userEvent.click(await screen.findByRole("button", { name: "סיום השלב" }));
      await waitFor(() => expect(s.calls.some((c) => c.method === "POST" && c.path === "/api/processes/i1/stages/kyc/finalize")).toBe(true));
    });

    it("a refusal from the server is explained, not shown as a generic error", async () => {
      state = view([stage("kyc", "agent", "in_progress", [{}])]);
      await openFile(serve(["agent"], () => ({ status: 409, json: { detail: "four_eyes_violation" } })));
      await open();
      await userEvent.click(await screen.findByRole("checkbox", { name: "פריט kyc0" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("אדם נוסף צריך לסיים");
    });
  });
});
