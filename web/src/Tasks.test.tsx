import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;
const task = (over: object = {}) => ({ id: "t1", text: "Call David", due: "2026-11-07", done: false, overdue: false, client_id: "c1", client: "David Cohen", owner_id: "u1", owner: "Ann", delegated_by: null, ...over });
const CAPS = ["client.read", "task.write", "inbox.read"];
const agent = (...more: Handler[]) =>
  fakeServer(
    (_m, p) => (p === "/api/auth/me" ? { json: ME(["agent"], CAPS) } : undefined),
    quiet,
    (_m, p) => (p === "/api/renewals?within=60" ? { json: [] } : undefined),
    (_m, p) => (p === "/api/tasks/people" ? { json: [{ id: "u1", name: "Ann" }, { id: "u2", name: "Dan" }] } : undefined),
    ...more,
  );

beforeEach(() => { localStorage.clear(); localStorage.setItem("lang", "en"); });

describe("Today tasks", () => {
  it("shows the chosen chip, marks overdue tasks and opens the client", async () => {
    const m = agent((mt, p) => (mt === "GET" && p === "/api/tasks?view=over" ? { json: [task({ overdue: true })] } : mt === "GET" && p.startsWith("/api/tasks?") ? { json: [] } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Today" }));
    expect(await screen.findByText("No tasks here.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Overdue" }));
    const li = (await screen.findByText("Call David")).closest("li") as HTMLElement;
    expect(li.className).toBe("attention");
    expect(screen.getByRole("button", { name: "Overdue" })).toHaveAttribute("aria-pressed", "true");
    expect(m.calls.some((c) => c.path === "/api/tasks?view=over")).toBe(true);
  });

  it("adds a task with the CSRF token, ticks it done and delegates it", async () => {
    let rows = [task()];
    const m = agent(
      (mt, p) => (mt === "GET" && p.startsWith("/api/tasks?") ? { json: rows } : undefined),
      (mt, p) => { if (mt === "POST" && p === "/api/tasks") { rows = [...rows, task({ id: "t2", text: "Send quote" })]; return { status: 201, json: {} }; } return undefined; },
      (mt, p) => (mt === "PATCH" && p.startsWith("/api/tasks/") ? { json: {} } : undefined),
    );
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Today" }));
    await screen.findByText("Call David");
    await userEvent.type(screen.getByLabelText("New task"), "Send quote");
    await userEvent.click(screen.getByRole("button", { name: "Add task" }));
    expect(await screen.findByText("Send quote")).toBeInTheDocument();
    const post = m.calls.find((c) => c.method === "POST" && c.path === "/api/tasks");
    expect(post?.body).toMatchObject({ text: "Send quote" });
    expect(post?.csrf).toBeTruthy();

    await userEvent.click(within((await screen.findByText("Call David")).closest("li") as HTMLElement).getByRole("checkbox"));
    await waitFor(() => expect(m.calls.find((c) => c.method === "PATCH")?.body).toEqual({ done: true }));

    await userEvent.selectOptions(screen.getByLabelText("Delegate to: Call David"), "u2");
    await waitFor(() => expect(m.calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ owner_id: "u2" }));
  });

  it("is not offered to a role without task.write", async () => {
    const m = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: ME(["marketing"], ["client.read_min", "inbox.read"]) } : undefined),
      quiet,
    );
    mount(m.fetcher);
    await screen.findByRole("button", { name: "Inbox" });
    expect(m.calls.some((c) => c.path.startsWith("/api/tasks"))).toBe(false);
  });
});

describe("meetings on the client file", () => {
  const CLIENT = { id: "c1", id_number: "123456782", name: "David Cohen", name_translit_he: "", name_translit_lat: "", preferred_language: "he", status: "registered", phone: null, email: null, family: [] };
  it("lists meetings with the service contact date, and records a new one", async () => {
    let items: { id: string; kind: string; held_on: string; subject: string; summary: string | null }[] = [{ id: "m1", kind: "phone", held_on: "2025-01-31", subject: "Annual call", summary: "All fine" }];
    const m = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: ME(["agent"], [...CAPS, "policy.read", "document.read"]) } : undefined),
      quiet,
      (_m, p) => (p.startsWith("/api/clients?") ? { json: [CLIENT] } : undefined),
      (_m, p) => (p === "/api/clients/c1" ? { json: CLIENT } : undefined),
      (mt, p) => (mt === "GET" && p === "/api/clients/c1/meetings" ? { json: { items, service_contact_due: "2027-01-31" } } : undefined),
      (mt, p) => { if (mt === "POST" && p === "/api/clients/c1/meetings") { items = [{ id: "m2", kind: "online", held_on: "2026-11-07", subject: "Review", summary: null }, ...items]; return { status: 201, json: {} }; } return undefined; },
      (_m, p) => (p.startsWith("/api/clients/c1/") ? { json: p.endsWith("consent") ? { current: {}, history: [] } : [] } : undefined),
      (_m, p) => (p.startsWith("/api/processes?") || p === "/api/process-defs" ? { json: [] } : undefined),
    );
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Clients" }));
    await userEvent.click(await screen.findByRole("button", { name: "David Cohen" }));
    expect(await screen.findByText("Annual call")).toBeInTheDocument();
    expect(screen.getByText(/Service contact due.*2027-01-31/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Subject"), "Review");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "online");
    await userEvent.click(screen.getByRole("button", { name: "Add meeting" }));
    expect(await screen.findByText("Review")).toBeInTheDocument();
    expect(m.calls.find((c) => c.method === "POST" && c.path === "/api/clients/c1/meetings")?.body).toMatchObject({ kind: "online", subject: "Review", summary: null });
  });
});

describe("new client or lead", () => {
  const made = (over: object = {}) => ({ id: "c9", phone_matches: [], phone_shared_with_other_agent: false, ...over });
  const clientsServer = (caps: string[], ...more: Handler[]) =>
    fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: ME(["agent"], caps) } : undefined),
      quiet,
      (mt, p) => (mt === "GET" && p.startsWith("/api/clients?q=") ? { json: [] } : undefined),
      ...more,
    );

  it("creates a client with the CSRF token and warns about a shared phone number", async () => {
    const m = clientsServer(["client.read", "client.write", "inbox.read"], (mt, p) =>
      mt === "POST" && p === "/api/clients" ? { status: 201, json: made({ phone_matches: [{ id: "c1", name: "David Cohen" }] }) } : undefined);
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Clients" }));
    await userEvent.type(await screen.findByLabelText("ID number"), "123456782");
    await userEvent.type(screen.getByLabelText("Name"), "Dana Levi");
    await userEvent.type(screen.getByLabelText("Phone"), "050-111-2222");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Created.");
    expect(within(status).getByText(/Another client already has this phone number/)).toBeInTheDocument();
    expect(within(status).getByRole("button", { name: "David Cohen" })).toBeInTheDocument();
    const post = m.calls.find((c) => c.method === "POST" && c.path === "/api/clients");
    expect(post?.body).toMatchObject({ id_number: "123456782", name: "Dana Levi", phone: "050-111-2222", email: null, preferred_language: "he" });
    expect(post?.csrf).toBeTruthy();
  });

  it("says only that another agent has the number, never whose, and explains a refused ID", async () => {
    let n = 0;
    const m = clientsServer(["client.read", "client.write", "inbox.read"], (mt, p) => {
      if (mt !== "POST" || p !== "/api/clients") return undefined;
      n += 1;
      return n === 1 ? { status: 201, json: made({ phone_shared_with_other_agent: true }) } : { status: 409, json: { detail: "duplicate_id_number" } };
    });
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Clients" }));
    await userEvent.type(await screen.findByLabelText("ID number"), "123456782");
    await userEvent.type(screen.getByLabelText("Name"), "Dana Levi");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/already used by a client of another agent/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("ID number"), "123456782");
    await userEvent.type(screen.getByLabelText("Name"), "Dana Levi");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A client with this ID number already exists.");
  });

  it("is not offered without client.write", async () => {
    const m = clientsServer(["client.read", "inbox.read"]);
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Clients" }));
    await screen.findByRole("searchbox");
    expect(screen.queryByLabelText("ID number")).toBeNull();
  });
});

describe("leads on Today", () => {
  it("lists the agent's leads and opens one", async () => {
    const m = agent((mt, p) => (mt === "GET" && p === "/api/clients?status=lead" ? { json: [{ id: "c7", name: "Rina Bar", id_number: "1", name_translit_he: "", name_translit_lat: "", preferred_language: "ru", status: "lead" }] } : undefined),
      (mt, p) => (mt === "GET" && p.startsWith("/api/tasks?") ? { json: [] } : undefined),
      (_m, p) => (p === "/api/clients/c7" ? { json: { id: "c7", name: "Rina Bar", id_number: "1", name_translit_he: "", name_translit_lat: "", preferred_language: "ru", status: "lead", phone: null, email: null, family: [] } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Today" }));
    expect(await screen.findByRole("heading", { name: "Leads (1)" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Rina Bar" }));
    await waitFor(() => expect(m.calls.some((c) => c.path === "/api/clients/c7")).toBe(true));
  });
});
