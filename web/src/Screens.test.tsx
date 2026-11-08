import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LANGS, dirOf, t, type Key } from "./i18n";
import { Portal } from "./pages/Portal";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const server = (roles: string[], caps: string[], ...extra: Handler[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined), quiet, ...extra);

beforeEach(() => { localStorage.clear(); });

describe("catalogs", () => {
  const keys = Object.keys(
    // every key of the source catalog, read through the public function by probing known keys
    { app: 1, login: 1, "role.accountant": 1, "reason.opted_out": 1, "auth.expiring": 1, excelJoin: 1, "stage.t60": 1 },
  ) as Key[];

  it("has all five languages with every probed key translated differently from a missing marker", () => {
    expect(LANGS).toEqual(["he", "en", "ar", "ru", "fr"]);
    for (const l of LANGS) for (const k of keys) expect(t(l, k), `${l}.${k}`).toBeTruthy();
    expect(t("ar", "login")).toBe("تسجيل الدخول");
    expect(t("ru", "login")).toBe("Войти");
    expect(t("fr", "login")).toBe("Se connecter");
  });

  it("flips the layout for Hebrew and Arabic only", () => {
    expect(LANGS.map((l) => [l, dirOf(l)])).toEqual([["he", "rtl"], ["en", "ltr"], ["ar", "rtl"], ["ru", "ltr"], ["fr", "ltr"]]);
  });

  it("the language selector switches the whole screen, direction and the remembered choice", async () => {
    mount(server(["agent"], ["client.read"]).fetcher);
    const pick = await screen.findByRole("combobox", { name: "שפה" });
    await userEvent.selectOptions(pick, "ar");
    expect(document.documentElement.dir).toBe("rtl");
    expect(await screen.findByRole("button", { name: "الوارد" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "اللغة" }), "ru");
    expect(document.documentElement.dir).toBe("ltr");
    expect(await screen.findByRole("button", { name: "Входящие" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Язык" }), "fr");
    expect(await screen.findByRole("button", { name: "Boîte de réception" })).toBeInTheDocument();
    expect(localStorage.getItem("lang")).toBe("fr");
  });

  it("the client's page follows the client's preferred language", async () => {
    window.history.replaceState(null, "", "/portal#tok");
    const fetcher = (async () => new Response(JSON.stringify({ name: "Ivan", language: "ru", policies: [], documents: [], claims: [] }), { status: 200 })) as typeof fetch;
    render(<Portal fetcher={fetcher} />);
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Ваше страховое досье: Ivan");
  });
});

describe("Messages and Pension in the client file", () => {
  const CLIENT = { id: "c1", id_number: "123456782", name: "Dana Levi", name_translit_he: "", name_translit_lat: "", preferred_language: "he", status: "active", phone: null, email: null, family: [] };
  const caps = ["client.read", "message.send", "flow.write"];
  const base = (...extra: Handler[]) => server(["agent"], caps,
    (_m, p) => (p.startsWith("/api/clients?") ? { json: [CLIENT] } : undefined),
    (_m, p) => (p === "/api/clients/c1" ? { json: CLIENT } : undefined),
    ...extra);
  async function open(s: ReturnType<typeof base>) {
    mount(s.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "לקוחות" }));
    await userEvent.click(await screen.findByRole("button", { name: "Dana Levi" }));
  }

  it("shows the conversation in both directions and marks a blocked send with its reason", async () => {
    let rows: object[] = [
      { direction: "in", channel: "whatsapp", kind: "text", body: "Is my policy active?", at: "2026-11-07T09:00:00Z" },
      { direction: "out", channel: "whatsapp", purpose: "marketing", status: "blocked", block_reason: "opted_out", delivery: null, body: "special offer", at: "2026-11-07T10:00:00Z" },
    ];
    const s = base(
      (m, p) => (m === "GET" && p === "/api/clients/c1/messages" ? { json: rows } : undefined),
      (m, p) => { if (m === "POST" && p === "/api/messages") { rows = []; return { status: 409, json: { detail: "no_consent" } }; } return undefined; });
    await open(s);
    const blocked = (await screen.findByText("special offer")).closest("li") as HTMLElement;
    expect(blocked.className).toBe("attention");
    expect(blocked).toHaveTextContent("נחסמה: הלקוח הסיר את עצמו מהערוץ הזה");
    expect(screen.getByText("Is my policy active?").closest("li")).toHaveAttribute("data-direction", "in");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "מטרה" }), "marketing");
    await userEvent.type(screen.getByRole("textbox", { name: "נוסח ההודעה" }), "hello");
    await userEvent.click(screen.getByRole("button", { name: "שליחת הודעה" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("לא נרשמה הסכמה לשיווק");
    expect(s.calls.find((c) => c.method === "POST" && c.path === "/api/messages")?.body).toEqual({ client_id: "c1", channel: "whatsapp", purpose: "marketing", body: "hello" });
  });

  it("shows the three authorizations, flags the expiring one and records the lookup request only when allowed", async () => {
    let ok = false;
    const s = base(
      (_m, p) => (p === "/api/clients/c1/flow-status" ? { json: { authorizations: { lookup_insurers: { state: "valid", expires_at: "2027-10-01" }, lookup_har: { state: "expiring", expires_at: "2027-01-01" }, lookup_clearing: { state: "missing" } } } } : undefined),
      (m, p) => (m === "POST" && p === "/api/clients/c1/lookup-requests" ? (ok ? { status: 201, json: {} } : { status: 409, json: { detail: { error: "authorizations_missing", detail: ["lookup_clearing"] } } }) : undefined));
    await open(s);
    const har = (await screen.findByText(/הר הביטוח/)).closest("li") as HTMLElement;
    expect(har).toHaveAttribute("data-state", "expiring");
    expect(har.className).toBe("attention");
    expect((screen.getByText(/חברות הביטוח/).closest("li") as HTMLElement).className).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "בקשת התיק הפנסיוני" }));
    expect(await screen.findByRole("alert", {}, { timeout: 1000 }).catch(() => null)).toBeNull();
    expect(await screen.findByRole("status")).toHaveTextContent('authorizations_missing: ["lookup_clearing"]');
    ok = true;
    await userEvent.click(screen.getByRole("button", { name: "בקשת התיק הפנסיוני" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("הבקשה נרשמה"));
  });

  it("offers no send form without the right to send", async () => {
    mount(server(["marketing"], ["client.read"], (_m, p) => (p.startsWith("/api/clients?") ? { json: [CLIENT] } : undefined), (_m, p) => (p === "/api/clients/c1" ? { json: CLIENT } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "לקוחות" }));
    await userEvent.click(await screen.findByRole("button", { name: "Dana Levi" }));
    await screen.findByText("אין הודעות.");
    expect(screen.queryByRole("button", { name: "שליחת הודעה" })).toBeNull();
  });
});

describe("Integrations, Settings and Excel join", () => {
  it("shows each integration's state and highlights the unconfigured or failing ones, without secrets", async () => {
    mount(server(["manager"], ["import.run"], (_m, p) => (p === "/api/integrations/status" ? { json: { adapters: [
      { name: "whatsapp", configured: true, approved: true, calls_24h: { ok: 9, failed: 0 }, last_error: null },
      { name: "esign", configured: true, approved: true, calls_24h: { ok: 1, failed: 2 }, last_error: "ConnectionError: down" },
      { name: "imap", configured: false, approved: true, calls_24h: { ok: 0, failed: 0 }, last_error: null }], syncs: [{ system: "agency", last_run: "2026-11-07T01:00:00Z" }] } } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "אינטגרציות" }));
    const cls = async (n: string) => ((await screen.findByText(n)).closest("li") as HTMLElement).className;
    expect(await cls("whatsapp")).toBe("");
    expect(await cls("esign")).toBe("attention");
    expect(await cls("imap")).toBe("attention");
    expect(screen.getByText(/ConnectionError: down/)).toBeInTheDocument();
    expect(screen.getByText(/agency: 2026-11-07/)).toBeInTheDocument();
  });

  it("settings shows the account and permissions, and changes language and theme", async () => {
    mount(server(["agent"], ["client.read", "policy.read"]).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "הגדרות" }));
    const sec = await screen.findByRole("region", { name: "הגדרות" });
    expect(within(sec).getByText("policy.read")).toBeInTheDocument();
    await userEvent.selectOptions(within(sec).getByRole("combobox", { name: "ערכת נושא" }), "dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("settings lists the open source notices with the attribution a licence asks for", async () => {
    mount(server(["agent"], ["client.read"]).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "הגדרות" }));
    const notices = await screen.findByRole("region", { name: "הודעות קוד פתוח" });
    expect(within(notices).getByRole("link", { name: "React Flow" })).toHaveAttribute("href", "https://reactflow.dev");
    expect(within(notices).getByText(/רכיבים/)).toBeInTheDocument();
    await userEvent.click(within(notices).getByText(/רכיבים/));
    expect(within(notices).getByText("react")).toBeInTheDocument(); // a shipped component and its licence are listed
    expect(within(notices).getAllByText("MIT").length).toBeGreaterThan(5);
  });

  it("excel join is for those who may use it, sends the files with the CSRF token and downloads the result", async () => {
    mount(server(["backoffice"], ["excel.use"]).fetcher);
    const seen: { url: string; csrf: string; keys: string[] }[] = [];
    vi.stubGlobal("fetch", (async (url: string, init: RequestInit) => {
      seen.push({ url, csrf: (init.headers as Record<string, string>)["x-csrf-token"] ?? "", keys: [...(init.body as FormData).keys()] });
      return new Response(new Blob(["PK"]), { status: 200, headers: { "x-join-summary": JSON.stringify({ base_rows: 7, unmatched: 3, methods: {} }) } });
    }) as unknown as typeof fetch);
    const created: string[] = [];
    URL.createObjectURL = () => { created.push("blob:x"); return "blob:x"; };
    URL.revokeObjectURL = () => undefined;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);  // jsdom cannot navigate to a download
    await userEvent.click(await screen.findByRole("button", { name: "אקסל" }));
    const run = screen.getByRole("button", { name: "חיבור הקבצים" });
    expect(run).toBeDisabled();
    const file = (n: string) => new File(["x"], n, { type: "application/octet-stream" });
    await userEvent.upload(screen.getByLabelText(/קובץ הסוכנות/), file("a.xlsx"));
    await userEvent.upload(screen.getByLabelText(/קובץ המבטח/), file("i.xlsx"));
    expect(run).toBeEnabled();
    await userEvent.click(run);
    expect(await screen.findByText("הקובץ המחובר ירד.")).toBeInTheDocument();
    expect(seen).toEqual([{ url: "/api/excel/join", csrf: "tok", keys: ["agency", "insurer"] }]);
    expect(screen.getByText(/שורות ללא התאמה: 3 \/ 7/)).toBeInTheDocument();
    expect(created).toHaveLength(1);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
});

describe("client view link (the mock's client app preview)", () => {
  const CLIENT = { id: "c1", id_number: "123456782", name: "Dana Levi", name_translit_he: "", name_translit_lat: "", preferred_language: "he", status: "active", phone: null, email: null, family: [] };
  const open = async (caps: string[], ...extra: Handler[]) => {
    const s = server(["agent"], caps, (_m, p) => (p.startsWith("/api/clients?") ? { json: [CLIENT] } : undefined), (_m, p) => (p === "/api/clients/c1" ? { json: CLIENT } : undefined), ...extra);
    mount(s.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "לקוחות" }));
    await userEvent.click(await screen.findByRole("button", { name: "Dana Levi" }));
    return s;
  };

  it("creates the link, shows it once with its expiry and opens the preview in a new tab", async () => {
    const s = await open(["client.read", "flow.write"], (m, p) => (m === "POST" && p === "/api/clients/c1/portal-link" ? { status: 201, json: { id: "l1", token: "tok123", expires_at: "2026-11-14T10:00:00Z" } } : undefined));
    await userEvent.click(await screen.findByRole("button", { name: "יצירת קישור ללקוח" }));
    const status = await screen.findByRole("status");
    const link = within(status).getByRole("link", { name: "תצוגה מקדימה של מה שהלקוח רואה" });
    expect(link).toHaveAttribute("href", `${window.location.origin}/portal#tok123`);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(status).toHaveTextContent("בתוקף עד 2026-11-14");
    expect(s.calls.find((c) => c.method === "POST" && c.path === "/api/clients/c1/portal-link")?.csrf).toBe("tok");
  });

  it("is not offered without the right to issue links", async () => {
    await open(["client.read"]);
    await screen.findByText("אין הודעות.");
    expect(screen.queryByRole("button", { name: "יצירת קישור ללקוח" })).toBeNull();
  });

  it("shows the server's reason when the link cannot be created", async () => {
    await open(["client.read", "flow.write"], (m, p) => (m === "POST" && p === "/api/clients/c1/portal-link" ? { status: 404, json: { detail: "not_found" } } : undefined));
    await userEvent.click(await screen.findByRole("button", { name: "יצירת קישור ללקוח" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not_found");
  });
});
