import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const OVERVIEW = {
  inbox_urgent: 3, renewals_overdue: 0, renewals_next_30: 7, claims_open: 2, intake_waiting_review: 1,
  kyc_waiting_approval: 1, campaigns_sending: 0, clients: 120, complaints_overdue: 2, licences_attention: 0,
};
const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const server = (roles: string[], caps: string[], ...extra: Handler[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined), quiet, ...extra);

beforeEach(() => { localStorage.clear(); });

describe("tabs follow the capabilities", () => {
  it("shows Overview, My work, Clients and Campaigns to a manager and none of them to a plain role", async () => {
    const m = server(["manager"], ["flow.approve", "process.read", "client.read", "campaign.write", "audit.read"], (_x, p) => (p === "/api/overview" ? { json: OVERVIEW } : undefined));
    mount(m.fetcher);
    for (const name of ["סקירה", "תיבה", "העבודה שלי", "לקוחות", "קמפיינים"]) expect(await screen.findByRole("button", { name })).toBeInTheDocument();
  });

  it("a back-office user gets no Overview or Campaigns tab", async () => {
    mount(server(["backoffice"], ["client.read", "process.read"]).fetcher);
    await screen.findByRole("button", { name: "לקוחות" });
    expect(screen.queryByRole("button", { name: "סקירה" })).toBeNull();
    expect(screen.queryByRole("button", { name: "קמפיינים" })).toBeNull();
  });
});

describe("Overview and Trust", () => {
  it("shows the counts, marks what needs attention and verifies the audit chain", async () => {
    const m = server(["manager"], ["flow.approve", "audit.read"], (_x, p) => (p === "/api/overview" ? { json: OVERVIEW } : undefined),
      (_x, p) => (p === "/api/audit/verify" ? { json: { ok: true, entries: 40, seals: 1, sealed_entries: 100, problems: [] } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "סקירה" }));
    const urgent = (await screen.findByText("דחופים בתיבה")).closest("li") as HTMLElement;
    expect(urgent).toHaveTextContent("3");
    expect(urgent.className).toBe("attention");
    expect((screen.getByText("חידושים שעבר מועדם").closest("li") as HTMLElement).className).toBe(""); // zero: no alarm
    expect((screen.getByText("תלונות שעבר מועדן").closest("li") as HTMLElement).className).toBe("attention");
    await userEvent.click(screen.getByRole("button", { name: "אימות שרשרת הביקורת" }));
    expect(await screen.findByRole("status")).toHaveTextContent("שרשרת הביקורת תקינה (40 + 100)");
  });

  it("shows the problems when the chain does not verify, and hides Trust without the audit right", async () => {
    const m = server(["manager"], ["flow.approve", "audit.read"], (_x, p) => (p === "/api/overview" ? { json: OVERVIEW } : undefined),
      (_x, p) => (p === "/api/audit/verify" ? { json: { ok: false, entries: 5, seals: 0, sealed_entries: 0, problems: ["hash mismatch at 4"] } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "סקירה" }));
    await userEvent.click(await screen.findByRole("button", { name: "אימות שרשרת הביקורת" }));
    expect(await screen.findByRole("status")).toHaveTextContent("hash mismatch at 4");
  });

  it("shows an error when the numbers cannot be loaded", async () => {
    mount(server(["manager"], ["flow.approve"]).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "סקירה" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("משהו השתבש");
    expect(screen.queryByRole("button", { name: "אימות שרשרת הביקורת" })).toBeNull();
  });
});

describe("My work", () => {
  it("lists the stages waiting for the role and opens the client", async () => {
    const m = server(["backoffice"], ["process.read", "client.read"],
      (_x, p) => (p === "/api/my-work" ? { json: { count: 1, items: [{ instance_id: "i1", key: "onb", title: "New client onboarding", stage: "kyc", stage_status: "ready", role: "backoffice", client_id: "c9" }] } } : undefined),
      (_x, p) => (p === "/api/clients/c9" ? { json: { id: "c9", id_number: "1", name: "Dana", name_translit_he: "", name_translit_lat: "", preferred_language: "he", status: "active", phone: null, email: null, family: [] } } : undefined),
      (_x, p) => (p.startsWith("/api/clients/c9/") ? { json: p.endsWith("consent") ? { current: {}, history: [] } : [] } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "העבודה שלי" }));
    expect(await screen.findByText("New client onboarding")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "פתיחת תיק לקוח" }));
    await waitFor(() => expect(m.calls.some((c) => c.path === "/api/clients/c9")).toBe(true));
  });

  it("says so when nothing is waiting", async () => {
    mount(server(["agent"], ["process.read"], (_x, p) => (p === "/api/my-work" ? { json: { count: 0, items: [] } } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "העבודה שלי" }));
    expect(await screen.findByText("אין עבודה הממתינה לתפקיד שלך.")).toBeInTheDocument();
  });
});

describe("Campaigns", () => {
  const camp = (over: object = {}) => ({ id: "k1", name: "Pension notice", type: "offer", purpose: "marketing", status: "draft", benefit: "cheaper", body: "x", base_language: "he", requires_approval: false, approved: true, ...over });

  it("creates a campaign with the benefit and a product rule, then previews the audience with the exclusions", async () => {
    const m = server(["marketing"], ["campaign.write"],
      (mt, p) => (p === "/api/campaigns" && mt === "GET" ? { json: [camp()] } : undefined),
      (mt, p) => (p === "/api/campaigns" && mt === "POST" ? { status: 201, json: camp() } : undefined),
      (_x, p) => (p === "/api/campaigns/k1/preview" ? { json: { matching: 4, selected: 2, excluded: { no_consent: 1, opted_out: 1 }, languages: { he: 2 }, sample: [] } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "קמפיינים" }));
    await userEvent.type(await screen.findByLabelText(/שם/), "Pension notice");
    await userEvent.type(screen.getByLabelText(/התועלת ללקוח/), "a cheaper fund");
    await userEvent.type(screen.getByLabelText(/נוסח ההודעה/), "hello there");
    await userEvent.type(screen.getByLabelText(/לקוחות שיש להם את המוצר/), "pension");
    await userEvent.click(screen.getByRole("button", { name: "יצירה" }));
    await waitFor(() => expect(m.calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Pension notice", type: "offer", benefit: "a cheaper fund", body: "hello there", base_language: "he", segment: { has_product: ["pension"] } }));
    expect(m.calls.find((c) => c.method === "POST")?.csrf).toBe("tok");
    await userEvent.click(await screen.findByRole("button", { name: "תצוגה מקדימה של הקהל" }));
    expect(await screen.findByRole("status")).toHaveTextContent("מתאימים: 4 · יקבלו: 2 · הוחרגו: no_consent 1, opted_out 1");
  });

  it("only a manager can approve, and the server's reason is shown when sending is refused", async () => {
    const pending = camp({ status: "pending_approval", requires_approval: true, approved: false });
    const asMarketing = server(["marketing"], ["campaign.write"], (mt, p) => (p === "/api/campaigns" && mt === "GET" ? { json: [pending] } : undefined));
    const view = mount(asMarketing.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "קמפיינים" }));
    expect(await screen.findByText(/ממתין למנהל/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "אישור" })).toBeNull();
    expect(screen.queryByRole("button", { name: "התחלת שליחה" })).toBeNull();
    view.unmount();

    const asManager = server(["manager"], ["campaign.write", "flow.approve"],
      (mt, p) => (p === "/api/campaigns" && mt === "GET" ? { json: [camp()] } : undefined),
      (mt, p) => (p === "/api/campaigns/k1/start" && mt === "POST" ? { status: 409, json: { detail: { error: "translation_review_missing", detail: ["ru"] } } } : undefined));
    mount(asManager.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "קמפיינים" }));
    await userEvent.click(await screen.findByRole("button", { name: "התחלת שליחה" }));
    expect(await screen.findByRole("alert")).toHaveTextContent('translation_review_missing: ["ru"]');
  });
});
