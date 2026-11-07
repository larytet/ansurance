import { screen } from "@testing-library/react";
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
      (method, p) => (p === "/api/auth/users" && method === "GET" ? { json: [] } : undefined),
      (method, p) => (p === "/api/auth/users" && method === "POST" ? { status: 201, json: { totp_uri: URI, recovery_codes: ["aaaaaaaaaa", "bbbbbbbbbb"] } } : undefined),
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
