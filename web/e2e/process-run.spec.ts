import { type Page, expect, test } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

function validId(): string {
  const body = String(Math.floor(Math.random() * 1e8)).padStart(8, "0");
  let total = 0;
  [...body].forEach((d, i) => { const n = Number(d) * (1 + (i % 2)); total += n > 9 ? n - 9 : n; });
  return body + String((10 - (total % 10)) % 10);
}

async function signIn(page: Page, email: string, secret: string) {
  await page.addInitScript(() => localStorage.setItem("lang", "en"));
  await page.goto("/");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Authenticator or recovery code").fill(totp(secret));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Inbox" })).toBeVisible();
}

async function tickAllAndFinish(page: Page, stage: string) {
  const box = page.locator(`fieldset[data-stage="${stage}"]`);
  const finish = box.getByRole("button", { name: "Finish the stage" });
  await expect(finish).toBeDisabled();
  const boxes = box.getByRole("checkbox");
  const n = await boxes.count();
  expect(n).toBeGreaterThan(0);
  for (let i = 0; i < n; i++) {
    await boxes.nth(i).click(); // a controlled box shows the server's answer, so wait for it
    await expect(box.getByRole("checkbox", { checked: true })).toHaveCount(i + 1);
  }
  await expect(box.getByRole("checkbox", { checked: true })).toHaveCount(n);
  await expect(finish).toBeEnabled();
  await finish.click();
  await expect(box).toHaveClass(/final/);
}

test("a manager works a retirement process on the client file: tick every item, finish two stages, the next one unlocks", async ({ page }) => {
  const stamp = Date.now();
  const secret = createUser(`run-${stamp}@e2e.il`, "manager");
  await signIn(page, `run-${stamp}@e2e.il`, secret);
  await page.evaluate(async ([id, name]) => {
    const csrf = (await (await fetch("/api/auth/me")).json()).csrf as string;
    await fetch("/api/clients", { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify({ id_number: id, name }) });
  }, [validId(), `Runner ${stamp}`]);

  await page.getByRole("button", { name: "Clients" }).click();
  await page.getByRole("searchbox").fill(`Runner ${stamp}`);
  await page.getByRole("button", { name: `Runner ${stamp}` }).click();
  await page.getByLabel("Start a process").selectOption("retire");
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await page.getByRole("button", { name: "Open the checklists" }).click();

  const gap = page.locator('fieldset[data-stage="ana"]');
  await expect(gap).toHaveClass(/locked/);
  await expect(gap.getByRole("checkbox").first()).toBeDisabled();

  await tickAllAndFinish(page, "int");
  await expect(gap).toHaveClass(/locked/); // still waiting for the authorizations
  await tickAllAndFinish(page, "auth");
  await expect(gap).toHaveClass(/ready|in_progress/);
  await expect(gap.getByRole("checkbox").first()).toBeEnabled();

  // the work is still there after a reload
  await page.reload();
  await page.getByRole("button", { name: "Clients" }).click();
  await page.getByRole("searchbox").fill(`Runner ${stamp}`);
  await page.getByRole("button", { name: `Runner ${stamp}` }).click();
  await page.getByRole("button", { name: "Open the checklists" }).click();
  await expect(page.locator('fieldset[data-stage="int"]')).toHaveClass(/final/);
});
