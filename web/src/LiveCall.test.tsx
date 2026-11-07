import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const server = (roles: string[], caps: string[], ...extra: Handler[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined), quiet, ...extra);
beforeEach(() => { localStorage.clear(); });

describe("Live call", () => {
  it("is offered to agents only", async () => {
    mount(server(["backoffice"], ["client.read"]).fetcher);
    await screen.findByRole("button", { name: "לקוחות" });
    expect(screen.queryByRole("button", { name: "שיחה חיה" })).toBeNull();
  });

  it("translates a caller line into the agent's language, keeps both texts and flags risky passages", async () => {
    const m = server(["agent"], ["client.read"],
      (_x, p) => (p.startsWith("/api/clients?") ? { json: [{ id: "c1", id_number: "1", name: "Ivan Petrov", name_translit_he: "", name_translit_lat: "", preferred_language: "ru", status: "active" }] } : undefined),
      (mt, p) => (mt === "POST" && p === "/api/translate" ? { status: 201, json: { id: "t1", translated: "הפרמיה היא ₪1,250", notice: "machine translation, may contain errors", needs_human_check: ["amount", "coverage"] } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "שיחה חיה" }));
    expect(screen.getByText(/ללא זיהוי דיבור|בלי זיהוי דיבור/)).toBeInTheDocument();  // the limit is stated on the screen
    await userEvent.type(screen.getByRole("searchbox"), "Ivan");
    await userEvent.click(await screen.findByRole("button", { name: "Ivan Petrov" }));
    await userEvent.type(screen.getByRole("textbox", { name: "מה נאמר" }), "Премия 1250 шекелей");
    await userEvent.click(screen.getByRole("button", { name: "הוספת השורה" }));
    const line = (await screen.findByText(/Премия 1250/)).closest("li") as HTMLElement;
    expect(line).toHaveTextContent("הפרמיה היא ₪1,250");
    expect(line).toHaveTextContent("לבדוק מול אדם: amount, coverage");
    expect(line).toHaveTextContent("machine translation");
    expect(line.className).toBe("attention");
    expect(m.calls.find((c) => c.path === "/api/translate")?.body).toEqual({ text: "Премия 1250 шекелей", source: "ru", target: "he", client_id: "c1" });
    expect(m.calls.find((c) => c.path === "/api/translate")?.csrf).toBe("tok");
  });

  it("translates the agent's reply the other way, and does not call the server when both languages match", async () => {
    const m = server(["agent"], ["client.read"], (mt, p) => (mt === "POST" && p === "/api/translate" ? { status: 201, json: { id: "t", translated: "Спасибо", notice: "machine translation", needs_human_check: [] } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "שיחה חיה" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "דובר" }), "agent");
    await userEvent.type(screen.getByRole("textbox", { name: "מה נאמר" }), "תודה");
    await userEvent.click(screen.getByRole("button", { name: "הוספת השורה" }));
    await screen.findByText(/Спасибо/);
    expect(m.calls.find((c) => c.path === "/api/translate")?.body).toEqual({ text: "תודה", source: "he", target: "ru" });
    const before = m.calls.length;
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "השפה שלך" }), "ru");
    await userEvent.type(screen.getByRole("textbox", { name: "מה נאמר" }), "ok");
    await userEvent.click(screen.getByRole("button", { name: "הוספת השורה" }));
    await waitFor(() => expect(screen.getAllByRole("listitem").length).toBeGreaterThan(1));
    expect(m.calls.slice(before).some((c) => c.path === "/api/translate")).toBe(false);
  });

  it("shows the server's reason when translation is unavailable and keeps the text for another try", async () => {
    mount(server(["agent"], ["client.read"], (mt, p) => (mt === "POST" && p === "/api/translate" ? { status: 502, json: { detail: "translation_provider_unavailable" } } : undefined)).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "שיחה חיה" }));
    await userEvent.type(screen.getByRole("textbox", { name: "מה נאמר" }), "hello");
    await userEvent.click(screen.getByRole("button", { name: "הוספת השורה" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("translation_provider_unavailable");
    expect(screen.getByRole("textbox", { name: "מה נאמר" })).toHaveValue("hello");
    expect(within(screen.getByRole("region", { name: "שיחה חיה" })).queryAllByRole("listitem")).toHaveLength(0);
  });
});
