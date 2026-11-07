import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { setupKeyOf } from "./pages/Users";
import { ME, fakeServer, mount, type Handler } from "./testutil";

const URI = "otpauth://totp/ansurance:bob%40x.il?secret=JBSWY3DPEHPK3PXP&issuer=ansurance";
const quiet: Handler = (_m, p) =>
  p.startsWith("/api/inbox?") ? { json: { items: [], next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: 0, urgent: 0, done_today: 0 } } : undefined;

beforeEach(() => { localStorage.clear(); localStorage.setItem("lang", "en"); });

describe("add a user", () => {
  it("extracts the setup key from the otpauth URI", () => {
    expect(setupKeyOf(URI)).toBe("JBSWY3DPEHPK3PXP");
    expect(setupKeyOf("garbage")).toBe("");
  });

  it("shows a QR code, an open-in-app link, the setup key and the recovery codes after creating", async () => {
    const m = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: ME(["manager"], ["user.manage"]) } : undefined),
      quiet,
      (method, p) => (p === "/api/users" && method === "GET" ? { json: [] } : undefined),
      (method, p) => (p === "/api/users" && method === "POST" ? { status: 201, json: { totp_uri: URI, recovery_codes: ["aaaaaaaaaa", "bbbbbbbbbb"] } } : undefined),
    );
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Users" }));
    await userEvent.type(await screen.findByLabelText("E-mail"), "bob@x.il");
    await userEvent.type(screen.getByLabelText("Name"), "Bob");
    await userEvent.type(screen.getByLabelText("Password"), "longenough1");
    await userEvent.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByRole("img", { name: "QR code for the authenticator app" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in authenticator app" })).toHaveAttribute("href", URI);
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByText("aaaaaaaaaa")).toBeInTheDocument();
    expect(m.calls.find((c) => c.method === "POST")?.body).toMatchObject({ email: "bob@x.il", roles: ["agent"], auth_mode: "local" });
  });
});

const ROW = { id: "u2", name: "Dana", email: "dana@x.il", roles: ["agent"], active: true, auth_mode: "local" };
const manager = (rows: unknown[], ...more: Handler[]) =>
  fakeServer(
    (_m, p) => (p === "/api/auth/me" ? { json: ME(["manager"], ["user.manage"]) } : undefined),
    quiet,
    (method, p) => (p === "/api/users" && method === "GET" ? { json: rows } : undefined),
    ...more,
  );

describe("administer users", () => {
  it("changes roles with a PATCH and shows the result", async () => {
    const m = manager([ROW], (method, p) => (method === "PATCH" && p === "/api/users/u2" ? { json: { id: "u2" } } : undefined));
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Users" }));
    await userEvent.click(await screen.findByRole("button", { name: "Edit roles" }));
    const editor = within(screen.getByRole("group", { name: /Edit roles: Dana/ }));
    await userEvent.click(editor.getByRole("checkbox", { name: "Back-office" }));
    await userEvent.click(editor.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("User updated.");
    const call = m.calls.find((c) => c.method === "PATCH");
    expect(call?.body).toEqual({ roles: ["agent", "backoffice"] });
    expect(call?.csrf).toBeTruthy();
  });

  it("asks for confirmation before deactivating, and says so when the last manager would go", async () => {
    const m = manager([ROW], (method, p) =>
      method === "PATCH" && p === "/api/users/u2" ? { status: 409, json: { detail: "last_manager" } } : undefined);
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Users" }));
    await userEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    expect(m.calls.some((c) => c.method === "PATCH")).toBe(false); // nothing sent yet
    await userEvent.click(screen.getByRole("button", { name: "Confirm: Deactivate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("At least one active manager must remain.");
  });

  it("marks an inactive user and offers reactivation, which shows the new authenticator once", async () => {
    const m = manager([{ ...ROW, active: false }], (method, p) =>
      method === "PATCH" && p === "/api/users/u2" ? { json: { id: "u2", totp_uri: URI, recovery_codes: ["cccccccccc"] } } : undefined);
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Users" }));
    expect(await screen.findByText("(Inactive)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset authenticator" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Reactivate" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm: Reactivate" }));
    expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(screen.getByText("cccccccccc")).toBeInTheDocument();
    expect(m.calls.find((c) => c.method === "PATCH")?.body).toEqual({ active: true });
  });

  it("resets the authenticator after confirmation and shows the setup", async () => {
    const m = manager([ROW], (method, p) =>
      method === "POST" && p === "/api/users/u2/reset-authenticator" ? { json: { totp_uri: URI, recovery_codes: ["dddddddddd"] } } : undefined);
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Users" }));
    await userEvent.click(await screen.findByRole("button", { name: "Reset authenticator" }));
    expect(screen.getByText(/signed out everywhere/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm: Reset authenticator" }));
    expect(await screen.findByRole("img", { name: "QR code for the authenticator app" })).toBeInTheDocument();
    expect(screen.getByText("dddddddddd")).toBeInTheDocument();
  });

  it("sets a password and does not offer authenticator or password actions for an SSO user", async () => {
    const m = manager([ROW, { ...ROW, id: "u3", name: "Sam", email: "sam@x.il", auth_mode: "sso" }], (method, p) =>
      method === "POST" && p === "/api/users/u2/password" ? { json: { status: "changed" } } : undefined);
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Users" }));
    expect(await screen.findAllByRole("button", { name: "Set password" })).toHaveLength(1); // Dana only
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    await userEvent.type(screen.getByLabelText("New password"), "a-fine-password-2");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Password set.");
    expect(m.calls.find((c) => c.path === "/api/users/u2/password")?.body).toEqual({ password: "a-fine-password-2" });
  });
});

describe("change your own password", () => {
  it("sends the current password, the code and the new password", async () => {
    const m = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: ME(["agent"]) } : undefined),
      quiet,
      (method, p) => (method === "POST" && p === "/api/auth/password" ? { json: { status: "changed" } } : undefined),
    );
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Settings" }));
    await userEvent.type(await screen.findByLabelText("Current password"), "old-password-1");
    await userEvent.type(screen.getByLabelText("Code from the authenticator, or a recovery code"), "123456");
    await userEvent.type(screen.getByLabelText("New password"), "new-password-22");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Password changed.");
    expect(m.calls.find((c) => c.path === "/api/auth/password")?.body).toEqual({
      current_password: "old-password-1", code: "123456", new_password: "new-password-22",
    });
  });

  it("gives no detail when the current password or code is wrong, and is not offered to SSO users", async () => {
    const m = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: ME(["agent"]) } : undefined),
      quiet,
      (method, p) => (method === "POST" && p === "/api/auth/password" ? { status: 401, json: { detail: "invalid_credentials" } } : undefined),
    );
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Settings" }));
    await userEvent.type(await screen.findByLabelText("Current password"), "x");
    await userEvent.type(screen.getByLabelText("Code from the authenticator, or a recovery code"), "1");
    await userEvent.type(screen.getByLabelText("New password"), "y");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign-in failed");
  });

  it("is hidden for an SSO user", async () => {
    const m = fakeServer(
      (_m, p) => (p === "/api/auth/me" ? { json: { ...ME(["agent"]), auth_mode: "sso" } } : undefined),
      quiet,
    );
    mount(m.fetcher);
    await userEvent.click(await screen.findByRole("button", { name: "Settings" }));
    await screen.findByText("Account");
    expect(screen.queryByLabelText("Current password")).toBeNull();
  });
});
