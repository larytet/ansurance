import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const server = (roles: string[], caps: string[], ...extra: Handler[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined), quiet, ...extra);
beforeEach(() => { localStorage.clear(); });

const camp = (over: object = {}) => ({ id: "k1", name: "Pension notice", type: "offer", purpose: "marketing", status: "draft", benefit: "cheaper", body: "x", base_language: "he", requires_approval: false, approved: true, ...over });

describe("campaign translations and results", () => {
  it("saves a reviewed translation for a language other than the base language", async () => {
    const m = server(["manager"], ["campaign.write"],
      (mt, p) => (mt === "GET" && p === "/api/campaigns" ? { json: [camp()] } : undefined),
      (mt, p) => (mt === "PUT" && p === "/api/campaigns/k1/translations/ar" ? { json: { status: "reviewed" } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "קמפיינים" }));
    await userEvent.click(await screen.findByRole("button", { name: "תרגומים" }));
    const lang = screen.getByRole("combobox", { name: "שפת הנוסח" });
    expect([...within(lang).getAllByRole("option")].map((o) => o.textContent)).toEqual(["ar", "ru", "fr", "en"]); // never the base language
    await userEvent.selectOptions(lang, "ar");
    await userEvent.type(screen.getByRole("textbox", { name: /הנוסח המתורגם/ }), "لدينا عرض");
    await userEvent.click(screen.getByRole("button", { name: "שמירה כנבדק" }));
    await waitFor(() => expect(m.calls.find((c) => c.method === "PUT")?.body).toEqual({ body: "لدينا عرض" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("התרגום נשמר כנבדק");
  });

  it("shows the results and who was excluded and why once a campaign has started", async () => {
    const m = server(["manager"], ["campaign.write"],
      (mt, p) => (mt === "GET" && p === "/api/campaigns" ? { json: [camp({ status: "sending" })] } : undefined),
      (_m, p) => (p === "/api/campaigns/k1/results" ? { json: { status: "sending", recipients: { queued: 3, excluded: 1 }, exclusions: { opted_out: 1 }, delivery: { read: 2 }, replied: 1, unsubscribed: 0 } } : undefined),
      (_m, p) => (p === "/api/campaigns/k1/audience" ? { json: [
        { client_id: "a", name: "Opted Out", status: "excluded", channel: null, language: "he", reason: "opted_out" },
        { client_id: "b", name: "Fine", status: "queued", channel: "whatsapp", language: "he", reason: "" }] } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "קמפיינים" }));
    await userEvent.click(await screen.findByRole("button", { name: "תוצאות" }));
    const region = await screen.findByRole("region", { name: "תוצאות" });
    expect(region).toHaveTextContent("queued 3 · excluded 1");
    expect(region).toHaveTextContent("מסירה: read 2");
    expect(region).toHaveTextContent("השיבו: 1");
    expect(within(region).getByText(/Opted Out/).closest("li")).toHaveTextContent("excluded (opted_out)");
    expect(within(region).queryByText(/Fine/)).toBeNull(); // only the exclusions are listed
  });

  it("offers no results button for a draft", async () => {
    mount(server(["manager"], ["campaign.write"], (mt, p) => (mt === "GET" && p === "/api/campaigns" ? { json: [camp()] } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "קמפיינים" }));
    await screen.findByText("Pension notice");
    expect(screen.queryByRole("button", { name: "תוצאות" })).toBeNull();
  });
});

describe("tax deadlines and residency", () => {
  const soon = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);
  const far = new Date(Date.now() + 90 * 86400_000).toISOString().slice(0, 10);

  it("lists the agency's dates (marking near ones), adds one, marks one done", async () => {
    let list = [{ id: "d1", kind: "vat_report", title: "VAT October", due_on: soon, done: false }, { id: "d2", kind: "withholding_cert", title: "Certificates", due_on: far, done: false }];
    const m = server(["backoffice"], ["tax.read", "tax.write"],
      (mt, p) => (mt === "GET" && p.startsWith("/api/tax/certificates") ? { json: [] } : undefined),
      (mt, p) => (mt === "GET" && p === "/api/tax/deadlines" ? { json: list } : undefined),
      (mt, p) => (mt === "GET" && p === "/api/tax/residency/gaps" ? { json: [] } : undefined),
      (mt, p) => { if (mt === "POST" && p === "/api/tax/deadlines") { list = [...list, { id: "d3", kind: "other", title: "Annual package", due_on: far, done: false }]; return { status: 201, json: { id: "d3" } }; } return undefined; },
      (mt, p) => { if (mt === "PUT" && p === "/api/tax/deadlines/d1/done") { list = list.map((d) => (d.id === "d1" ? { ...d, done: true } : d)); return { json: {} }; } return undefined; });
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "מס" }));
    expect((await screen.findByText("VAT October")).closest("li")?.className).toBe("attention");
    expect(screen.getByText("Certificates").closest("li")?.className).toBe("");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "סוג" }), "other");
    await userEvent.type(screen.getByRole("textbox", { name: "שם" }), "Annual package");
    await userEvent.type(screen.getByLabelText("תאריך יעד"), far);
    await userEvent.click(screen.getByRole("button", { name: "הוספת מועד" }));
    await waitFor(() => expect(m.calls.find((c) => c.method === "POST" && c.path === "/api/tax/deadlines")?.body).toEqual({ kind: "other", title: "Annual package", due_on: far }));
    await screen.findByText("Annual package");
    await userEvent.click(screen.getAllByRole("button", { name: "סימון כבוצע" })[0] as HTMLElement);
    await waitFor(() => expect(screen.getByText("VAT October").closest("li")).toHaveTextContent("בוצע"));
  });

  it("lists clients without a residency declaration and records the answer", async () => {
    let gaps = [{ client_id: "c1", client: "No Declaration", gap: "no_declaration" }, { client_id: "c2", client: "Foreign", gap: "foreign_residency_without_form" }];
    const m = server(["backoffice"], ["tax.read", "tax.write"],
      (mt, p) => (mt === "GET" && p.startsWith("/api/tax/certificates") ? { json: [] } : undefined),
      (mt, p) => (mt === "GET" && p === "/api/tax/deadlines" ? { json: [] } : undefined),
      (mt, p) => (mt === "GET" && p === "/api/tax/residency/gaps" ? { json: gaps } : undefined),
      (mt, p) => { if (mt === "PUT" && p === "/api/tax/residency/c2") { gaps = gaps.filter((g) => g.client_id !== "c2"); return { json: {} }; } return undefined; });
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "מס" }));
    expect(await screen.findByText(/תושבות זרה ללא הטופס הנוסף/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "סימון: הטופס התקבל" }));
    await waitFor(() => expect(m.calls.find((c) => c.path === "/api/tax/residency/c2")?.body).toEqual({ residency: "foreign", form_received: true }));
    await waitFor(() => expect(screen.queryByText(/תושבות זרה/)).toBeNull());
    expect(screen.getByText("No Declaration")).toBeInTheDocument();
  });

  it("the accountant sees the dates but cannot change anything", async () => {
    mount(server(["accountant"], ["tax.read"],
      (mt, p) => (mt === "GET" && p.startsWith("/api/tax/certificates") ? { json: [] } : undefined),
      (mt, p) => (mt === "GET" && p === "/api/tax/deadlines" ? { json: [{ id: "d1", kind: "vat_report", title: "VAT", due_on: far, done: false }] } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "מס" }));
    await screen.findByText("VAT");
    expect(screen.queryByRole("button", { name: "סימון כבוצע" })).toBeNull();
    expect(screen.queryByRole("button", { name: "הוספת מועד" })).toBeNull();
    expect(screen.queryByText("הצהרות תושבות מס")).toBeNull();
  });
});
