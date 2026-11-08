import { expect, test } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

test("a long Inbox is shown through the Lens: twenty in full, the rest squashed, moved by keys and by a bar", async ({ page }) => {
  const stamp = Date.now();
  const email = `lens-${stamp}@e2e.il`;
  const secret = createUser(email, "manager");
  await page.addInitScript(() => localStorage.setItem("lang", "en"));
  await page.goto("/");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Authenticator or recovery code").fill(totp(secret));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Inbox" })).toBeVisible();

  // thirty items through the browser's own session; the first is urgent so it sorts to the top
  const made = await page.evaluate(async ([s]) => {
    const csrf = (await (await fetch("/api/auth/me")).json()).csrf as string;
    let ok = 0;
    for (let i = 0; i < 30; i++) {
      const r = await fetch("/api/inbox", { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ type: "approval", role: "manager", title: `Lens item ${s} ${String(i).padStart(2, "0")}`, urgency: i === 0 ? "urgent" : "normal" }) });
      if (r.status === 201) ok += 1;
    }
    return ok;
  }, [String(stamp)]);
  expect(made).toBe(30);

  await page.reload();
  const window = page.getByTestId("lens-window");
  await expect(window).toContainText("1-20 of");
  await expect(page.locator("li.item")).toHaveCount(20);
  expect(await page.locator("li.squashed").count()).toBeGreaterThanOrEqual(10);
  await expect(page.locator(".strip .bar.urgent").first()).toBeVisible();

  const strip = page.getByRole("slider", { name: /Lens/ });
  await strip.focus();
  await page.keyboard.press("PageDown");
  await expect(window).toContainText("21-");
  await page.keyboard.press("PageUp");
  await expect(window).toContainText("1-20 of");

  // a squashed bar jumps to its item and shows it in full
  const bar = page.locator("li.squashed button").last();
  const title = await bar.getAttribute("aria-label");
  await bar.click();
  await expect(window).not.toContainText("1-20 of");
  // titles repeat in a long live inbox (many complaints share one), so check that a full item with this title is shown
  await expect(page.locator("li.item", { hasText: title as string }).first()).toBeVisible();
  await expect(page.locator("li.item")).toHaveCount(20);
});
