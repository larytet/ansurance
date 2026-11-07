import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const contact = (over: object) => ({
  id: "c1", name: "Dana Levi", name_translit_he: "דנה לוי", name_translit_lat: "dana levi", preferred_language: "he",
  phone: "050-111-2222", email: "dana@x.il", consent_whatsapp: "opt_in", consent_email: "opt_out", consent_sms: "none", ...over,
});
const ROWS = [contact({}), contact({ id: "c2", name: "Rina Bar", name_translit_he: "רינה בר", name_translit_lat: "rina bar", preferred_language: "ru", phone: null, email: "rina@x.il", consent_whatsapp: "opt_out", consent_email: "opt_in" })];
const as = (roles: string[], caps: string[]) =>
  fakeServer((_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined), quiet,
    (_m, p) => (p === "/api/marketing/contacts" ? { json: ROWS } : undefined));

beforeEach(() => { localStorage.clear(); localStorage.setItem("lang", "en"); });

describe("Contacts (marketing)", () => {
  it("lists contact details and consent per channel, and says what is not shown", async () => {
    const m = as(["marketing"], ["client.read_min", "inbox.read"]);
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Contacts" }));
    const row = (await screen.findByText("Dana Levi")).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("050-111-2222");
    expect(within(row).getByText(/WhatsApp.*Opted in/)).toBeInTheDocument();
    expect(within(row).getByText(/E-mail.*Opted out/)).toBeInTheDocument();
    expect(within(row).getByText(/SMS.*No consent recorded/)).toBeInTheDocument();
    expect(screen.getByText(/No documents, claims or health data/)).toBeInTheDocument();
    expect(m.calls.some((c) => c.path.startsWith("/api/clients"))).toBe(false); // never the full client API
  });

  it("filters by name in any script and by who opted in to a channel", async () => {
    mount(as(["marketing"], ["client.read_min", "inbox.read"]).fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Contacts" }));
    await screen.findByText("Dana Levi");
    await userEvent.type(screen.getByRole("searchbox"), "רינה");
    expect(screen.queryByText("Dana Levi")).toBeNull();
    expect(screen.getByText("Rina Bar")).toBeInTheDocument();
    await userEvent.clear(screen.getByRole("searchbox"));
    await userEvent.selectOptions(screen.getByLabelText("Opted in to"), "email");
    expect(screen.queryByText("Dana Levi")).toBeNull(); // opted out of e-mail
    expect(screen.getByText("Rina Bar")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Opted in to"), "whatsapp");
    expect(screen.getByText("Dana Levi")).toBeInTheDocument();
    expect(screen.queryByText("Rina Bar")).toBeNull();
  });

  it("is the tab of the role that has no full client access, and not of the others", async () => {
    mount(as(["agent"], ["client.read", "client.read_min", "inbox.read"]).fetcher);
    await screen.findByRole("button", { name: "Clients" });
    expect(screen.queryByRole("button", { name: "Contacts" })).toBeNull();
  });
});
