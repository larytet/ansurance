import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clampStart } from "./pages/Lens";
import { ME, fakeServer, mount } from "./testutil";

const item = (i: number) => ({
  id: `i${i}`, type: "task", role: "agent", title: `Item ${i}`, detail: "", status: "open", client_id: null, area: null, created_at: "2026-11-07T10:00:00Z",
  urgency: i < 3 ? "urgent" : i < 8 ? "important" : "normal",
});
const inbox = (n: number) =>
  fakeServer(
    (_m, p) => (p === "/api/auth/me" ? { json: ME(["agent"], ["client.read", "inbox.read"]) } : undefined),
    (_m, p) => (p.startsWith("/api/inbox?") ? { json: { items: Array.from({ length: n }, (_, i) => item(i)), next: null, read_only: false } } : p === "/api/inbox/counts" ? { json: { open: n, urgent: 3, done_today: 0 } } : undefined),
  );
const full = () => document.querySelectorAll("li.item");
const squashed = () => document.querySelectorAll("li.squashed");

beforeEach(() => { localStorage.clear(); localStorage.setItem("lang", "en"); });
afterEach(() => vi.restoreAllMocks());

describe("clampStart", () => {
  it("keeps the window inside the list", () => {
    expect([clampStart(-5, 45), clampStart(0, 45), clampStart(30, 45), clampStart(99, 45), clampStart(3, 10)]).toEqual([0, 0, 25, 25, 0]);
  });
});

describe("Inbox Lens", () => {
  it("shows twenty items in full, squashes the rest, and states the window", async () => {
    mount(inbox(45).fetcher);
    await screen.findByText("Item 0");
    expect(full()).toHaveLength(20);
    expect(squashed()).toHaveLength(25);
    expect(screen.getByTestId("lens-window")).toHaveTextContent("1-20 of 45");
    expect(screen.queryByText("Item 30")).toBeNull(); // squashed: no text, only a label
    expect(screen.getByRole("button", { name: "Item 30" })).toBeInTheDocument();
    const bars = document.querySelectorAll(".strip .bar");
    expect(bars).toHaveLength(45);
    expect([...bars].slice(0, 3).every((b) => b.classList.contains("urgent"))).toBe(true);
    expect(bars[5]?.classList.contains("important")).toBe(true);
  });

  it("jumps to a squashed item when its bar is clicked, centring it", async () => {
    mount(inbox(45).fetcher);
    await screen.findByText("Item 0");
    await userEvent.click(screen.getByRole("button", { name: "Item 30" }));
    expect(screen.getByTestId("lens-window")).toHaveTextContent("21-40 of 45");
    expect(screen.getByText("Item 30")).toBeInTheDocument();
    expect(screen.queryByText("Item 0")).toBeNull();
  });

  it("moves with the arrow and page keys and stops at both ends", async () => {
    mount(inbox(45).fetcher);
    await screen.findByText("Item 0");
    const strip = screen.getByRole("slider", { name: /Lens/ });
    strip.focus();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByTestId("lens-window")).toHaveTextContent("3-22 of 45");
    await userEvent.keyboard("{PageDown}{PageDown}");
    expect(screen.getByTestId("lens-window")).toHaveTextContent("26-45 of 45"); // the last window
    expect(strip).toHaveAttribute("aria-valuenow", "26");
    await userEvent.keyboard("{PageUp}{PageUp}{PageUp}{ArrowUp}");
    expect(screen.getByTestId("lens-window")).toHaveTextContent("1-20 of 45");
  });

  it("follows a drag along the strip, and scrolling over it", async () => {
    mount(inbox(45).fetcher);
    await screen.findByText("Item 0");
    const strip = screen.getByRole("slider", { name: /Lens/ });
    vi.spyOn(strip, "getBoundingClientRect").mockReturnValue({ left: 0, width: 450, top: 0, height: 28, right: 450, bottom: 28, x: 0, y: 0, toJSON: () => ({}) });
    const pointer = (type: string, clientX: number) => fireEvent(strip, new MouseEvent(type, { clientX, bubbles: true }));
    pointer("pointerdown", 225);
    expect(screen.getByTestId("lens-window")).toHaveTextContent("14-33 of 45"); // centred on the middle
    pointer("pointermove", 450);
    expect(screen.getByTestId("lens-window")).toHaveTextContent("26-45 of 45");
    pointer("pointerup", 450);
    pointer("pointermove", 0);
    expect(screen.getByTestId("lens-window")).toHaveTextContent("26-45 of 45"); // no drag, no move
    fireEvent.wheel(strip, { deltaY: -100 });
    expect(screen.getByTestId("lens-window")).toHaveTextContent("25-44 of 45");
  });

  it("is not shown for a short list, which stays complete", async () => {
    mount(inbox(12).fetcher);
    await screen.findByText("Item 0");
    expect(full()).toHaveLength(12);
    expect(squashed()).toHaveLength(0);
    expect(screen.queryByRole("slider")).toBeNull();
  });
});
