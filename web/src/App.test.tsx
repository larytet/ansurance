import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const item = (id: string, urgency: string, title: string) => ({
  id, type: "other", role: "agent", title, detail: "", urgency, status: "open", client_id: null, area: null, created_at: "2026-11-06T10:00:00Z",
});
const inbox: Handler = (_m, p) =>
  _m === "GET" && p.startsWith("/api/inbox?")
    ? { json: { items: [item("a", "urgent", "Renewal due"), item("b", "normal", "Call back")], next: null, read_only: false } }
    : undefined;
const counts: Handler = (_m, p) => (p === "/api/inbox/counts" ? { json: { open: 2, urgent: 1, done_today: 5 } } : undefined);
const me = (roles: string[], caps?: string[]): Handler => (_m, p) => (p === "/api/auth/me" ? { json: ME(roles, caps) } : undefined);

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("dir");
});

describe("sign in", () => {
  it("shows the sign-in form when there is no session", async () => {
    const s = fakeServer((_m, p) => (p === "/api/auth/me" ? { status: 401, json: { detail: "not_authenticated" } } : undefined));
    mount(s.fetcher);
    expect(await screen.findByRole("button", { name: "כניסה" })).toBeInTheDocument();
  });

  it("signs in with e-mail, password and code, then shows the Inbox", async () => {
    let signedIn = false;
    const s = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? (signedIn ? { json: ME(["agent"]) } : { status: 401, json: { detail: "x" } }) : undefined),
      (_m, p) => {
        if (p === "/api/auth/login") { signedIn = true; return { json: { csrf: "tok" } }; }
      },
      inbox, counts,
    );
    mount(s.fetcher);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("דוא״ל"), "ann@agency.il");
    await user.type(screen.getByLabelText("סיסמה"), "correct-horse-battery");
    await user.type(screen.getByLabelText("קוד מאמת או קוד שחזור"), "123456");
    await user.click(screen.getByRole("button", { name: "כניסה" }));
    expect(await screen.findByText("Renewal due")).toBeInTheDocument();
    expect(s.calls.find((c) => c.path === "/api/auth/login")?.body).toEqual({
      email: "ann@agency.il", password: "correct-horse-battery", code: "123456",
    });
  });

  it("says so, without detail, when the sign-in fails or the account is locked", async () => {
    const reply = { status: 401 };
    const s = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { status: 401, json: { detail: "x" } } : undefined),
      (_m, p) => (p === "/api/auth/login" ? { status: reply.status, json: { detail: "whatever" } } : undefined),
    );
    mount(s.fetcher);
    const user = userEvent.setup();
    const submit = async () => {
      await user.clear(await screen.findByLabelText("דוא״ל"));
      await user.type(screen.getByLabelText("דוא״ל"), "a@b.il");
      await user.type(screen.getByLabelText("סיסמה"), "x");
      await user.type(screen.getByLabelText("קוד מאמת או קוד שחזור"), "1");
      await user.click(screen.getByRole("button", { name: "כניסה" }));
    };
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("הכניסה נכשלה");
    reply.status = 423;
    await user.click(screen.getByRole("button", { name: "כניסה" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("יותר מדי ניסיונות"));
  });
});

describe("shell", () => {
  it("offers Clients only to roles that may read clients, and names the user and roles", async () => {
    const withClients = fakeServer(me(["agent"], ["client.read"]), inbox, counts);
    const a = mount(withClients.fetcher);
    expect(await screen.findByRole("button", { name: "לקוחות" })).toBeInTheDocument();
    expect(screen.getByText(/Ann/)).toHaveTextContent("סוכן");
    a.unmount();

    const marketing = fakeServer(me(["marketing"], ["client.read_min"]), inbox, counts);
    mount(marketing.fetcher);
    await screen.findByText("Renewal due");
    expect(screen.queryByRole("button", { name: "לקוחות" })).toBeNull();
  });

  it("switches language (and direction) and theme, and remembers both", async () => {
    const s = fakeServer(me(["agent"]), inbox, counts);
    mount(s.fetcher);
    const user = userEvent.setup();
    await screen.findByText("Renewal due");
    expect(document.documentElement.dir).toBe("rtl");
    await user.selectOptions(screen.getByRole("combobox", { name: "שפה" }), "en");
    expect(document.documentElement.dir).toBe("ltr");
    expect(screen.getByRole("button", { name: "Inbox" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Theme" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("lang")).toBe("en");
    expect(localStorage.getItem("theme")).toBe("dark");
  });

  it("signs out and returns to the sign-in form", async () => {
    const s = fakeServer(me(["agent"]), inbox, counts, (_m, p) => (p === "/api/auth/logout" ? { json: {} } : undefined));
    mount(s.fetcher);
    await screen.findByText("Renewal due");
    await userEvent.click(screen.getByRole("button", { name: "יציאה" }));
    expect(await screen.findByRole("button", { name: "כניסה" })).toBeInTheDocument();
    expect(s.calls.find((c) => c.path === "/api/auth/logout")?.csrf).toBe("tok"); // writes carry the CSRF token
  });
});

describe("Inbox", () => {
  it("shows counts, marks urgency, and filters", async () => {
    const s = fakeServer(me(["agent"]), inbox, counts);
    mount(s.fetcher);
    await screen.findByText("Renewal due");
    expect(screen.getByTestId("count-open")).toHaveTextContent("2");
    expect(screen.getByTestId("count-urgent")).toHaveTextContent("1");
    expect(screen.getByTestId("count-done")).toHaveTextContent("5");
    expect(screen.getByText("Renewal due").closest("li")).toHaveAttribute("data-urgency", "urgent");
    await userEvent.click(screen.getByRole("button", { name: "דחופים" }));
    await waitFor(() => expect(s.calls.some((c) => c.path.includes("urgency=urgent"))).toBe(true));
  });

  it("marks an item done with one click", async () => {
    const s = fakeServer(me(["agent"]), inbox, counts, (_m, p) => (_m === "POST" && p === "/api/inbox/a/done" ? { json: {} } : undefined));
    mount(s.fetcher);
    const row = (await screen.findByText("Renewal due")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "בוצע" }));
    await waitFor(() => expect(s.calls.some((c) => c.path === "/api/inbox/a/done" && c.csrf === "tok")).toBe(true));
  });

  it("will not dismiss without a reason, and sends it when given", async () => {
    const s = fakeServer(me(["agent"]), inbox, counts, (_m, p) => (p === "/api/inbox/b/dismiss" ? { json: {} } : undefined));
    mount(s.fetcher);
    const row = (await screen.findByText("Call back")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "דחייה" }));
    const reason = within(row).getByLabelText("סיבה (חובה)");
    expect(reason).toBeRequired();
    fireEvent.submit(reason.closest("form") as HTMLFormElement); // jsdom does not block on `required`
    await userEvent.type(reason, "ab"); // too short: the server rule is 3 characters
    expect(reason).toHaveAttribute("minlength", "3");
    await userEvent.clear(reason);
    await userEvent.type(reason, "client called back");
    await userEvent.click(within(row).getByRole("button", { name: "אישור" }));
    await waitFor(() => expect(s.calls.find((c) => c.path === "/api/inbox/b/dismiss")?.body).toEqual({ reason: "client called back" }));
  });

  it("reassigns to another role with a note", async () => {
    const s = fakeServer(me(["agent"]), inbox, counts, (_m, p) => (p === "/api/inbox/a/reassign" ? { json: {} } : undefined));
    mount(s.fetcher);
    const row = (await screen.findByText("Renewal due")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "העברה" }));
    await userEvent.selectOptions(within(row).getByLabelText("תפקיד"), "manager");
    await userEvent.type(within(row).getByLabelText("הערה (חובה)"), "needs approval");
    await userEvent.click(within(row).getByRole("button", { name: "אישור" }));
    await waitFor(() => expect(s.calls.find((c) => c.path === "/api/inbox/a/reassign")?.body).toEqual({ role: "manager", note: "needs approval" }));
  });

  it("says when nothing is waiting and when loading fails", async () => {
    const empty = fakeServer(me(["agent"]), (_m, p) => (p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : undefined), counts);
    const a = mount(empty.fetcher);
    expect(await screen.findByText("אין פריטים ממתינים.")).toBeInTheDocument();
    a.unmount();
    const broken = fakeServer(me(["agent"]), (_m, p) => (p.startsWith("/api/inbox") ? { status: 500, json: {} } : undefined));
    mount(broken.fetcher);
    expect(await screen.findByRole("alert")).toHaveTextContent("משהו השתבש");
  });

  it("loads more when there is a next page", async () => {
    const pages: Handler = (_m, p) => {
      if (!p.startsWith("/api/inbox?")) return undefined;
      return p.includes("cursor=c2")
        ? { json: { items: [item("c", "normal", "Third")], next: null, read_only: false } }
        : { json: { items: [item("a", "urgent", "First")], next: "c2", read_only: false } };
    };
    const s = fakeServer(me(["agent"]), pages, counts);
    mount(s.fetcher);
    await screen.findByText("First");
    await userEvent.click(screen.getByRole("button", { name: "טען עוד" }));
    expect(await screen.findByText("Third")).toBeInTheDocument();
    expect(screen.getByText("First")).toBeInTheDocument();
  });
});

describe("Clients", () => {
  it("searches by what the user types and shows names with the transliteration", async () => {
    const rows = [{ id: "c1", id_number: "123456782", name: "דוד כהן", name_translit_he: "דוד כהן", name_translit_lat: "dvd kn", preferred_language: "he", status: "lead" }];
    const s = fakeServer(me(["agent"]), inbox, counts, (_m, p) => (p.startsWith("/api/clients?") ? { json: p.includes("david") ? rows : [] } : undefined));
    mount(s.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "לקוחות" }));
    await userEvent.type(screen.getByRole("searchbox"), "david");
    expect(await screen.findByText("דוד כהן")).toBeInTheDocument();
    expect(screen.getByText(/123456782/)).toBeInTheDocument();
    await userEvent.clear(screen.getByRole("searchbox"));
    await userEvent.type(screen.getByRole("searchbox"), "zzz");
    expect(await screen.findByText("לא נמצאו לקוחות.")).toBeInTheDocument();
  });
});
