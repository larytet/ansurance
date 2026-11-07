import { type Browser, type Page, expect, test } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

async function open(browser: Browser): Promise<Page> {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, baseURL: process.env.ANS_BASE_URL ?? "https://127.0.0.1:8443" });
  await context.addInitScript(() => localStorage.setItem("lang", "en"));
  return context.newPage();
}

async function signIn(page: Page, email: string, password: string, secret: string) {
  await page.goto("/");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByLabel("Authenticator or recovery code").fill(totp(secret));
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("a manager adds, resets, deactivates and reactivates a user in the browser", async ({ browser }) => {
  const stamp = Date.now();
  const managerEmail = `adm-${stamp}@e2e.il`;
  const newEmail = `new-${stamp}@e2e.il`;
  const managerSecret = createUser(managerEmail, "manager");

  const manager = await open(browser);
  await signIn(manager, managerEmail, PASSWORD, managerSecret);
  await manager.getByRole("button", { name: "Users" }).click();

  // add a local user: the setup is shown once
  await manager.getByLabel("E-mail").fill(newEmail);
  await manager.getByLabel("Name").fill("New Person");
  await manager.getByLabel("Password", { exact: true }).fill("a-fine-password-9");
  await manager.getByRole("button", { name: "Create user" }).click();
  const secret1 = (await manager.locator(".setup-key").innerText()).trim();
  expect(secret1).toMatch(/^[A-Z2-7]+$/);
  await manager.getByRole("button", { name: "I have saved them" }).click();

  // the new user signs in on another device
  const person = await open(browser);
  await signIn(person, newEmail, "a-fine-password-9", secret1);
  await expect(person.locator(".who")).toContainText("New Person");

  // the manager resets the authenticator: the person is signed out and the new secret works
  const row = manager.locator("tr", { hasText: newEmail });
  await row.getByRole("button", { name: "Reset authenticator" }).click();
  await row.getByRole("button", { name: "Confirm: Reset authenticator" }).click();
  const secret2 = (await manager.locator(".setup-key").innerText()).trim();
  expect(secret2).not.toBe(secret1);
  await manager.getByRole("button", { name: "I have saved them" }).click();
  await person.reload();
  await expect(person.getByRole("button", { name: "Sign in" })).toBeVisible();
  await signIn(person, newEmail, "a-fine-password-9", secret2);
  await expect(person.locator(".who")).toContainText("New Person");

  // deactivate: the open session ends and signing in fails
  await row.getByRole("button", { name: "Deactivate" }).click();
  await row.getByRole("button", { name: "Confirm: Deactivate" }).click();
  await expect(row).toContainText("Inactive");
  await person.reload();
  await expect(person.getByRole("button", { name: "Sign in" })).toBeVisible();

  // reactivate: a new authenticator is issued and works
  await row.getByRole("button", { name: "Reactivate" }).click();
  await row.getByRole("button", { name: "Confirm: Reactivate" }).click();
  const secret3 = (await manager.locator(".setup-key").innerText()).trim();
  await manager.getByRole("button", { name: "I have saved them" }).click();
  await signIn(person, newEmail, "a-fine-password-9", secret3);
  await expect(person.locator(".who")).toContainText("New Person");
});
