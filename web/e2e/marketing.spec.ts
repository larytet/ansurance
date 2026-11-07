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

test("marketing sees contact details and consent, and has no way into the full client file", async ({ browser }) => {
  const stamp = Date.now();
  const agentEmail = `a-${stamp}@e2e.il`;
  const marketingEmail = `m-${stamp}@e2e.il`;
  const agentSecret = createUser(agentEmail, "agent");
  const marketingSecret = createUser(marketingEmail, "marketing");

  const agent = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
  await signIn(agent, agentEmail, agentSecret);
  await agent.getByRole("button", { name: "Clients" }).click();
  const form = agent.getByRole("form", { name: "New client or lead" });
  await form.getByLabel("ID number").fill(validId());
  await form.getByLabel("Name").fill(`Contact ${stamp}`);
  await form.getByLabel("Phone").fill("052-123-4567");
  await form.getByRole("button", { name: "Create" }).click();
  await expect(form.getByRole("status")).toContainText("Created.");

  const marketing = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
  await signIn(marketing, marketingEmail, marketingSecret);
  await expect(marketing.getByRole("button", { name: "Clients" })).toHaveCount(0);
  await marketing.getByRole("button", { name: "Contacts" }).click();
  await marketing.getByRole("searchbox").fill(`Contact ${stamp}`);
  const row = marketing.locator("tr", { hasText: `Contact ${stamp}` });
  await expect(row).toContainText("052-123-4567");
  await expect(row).toContainText("WhatsApp: No consent recorded");
  // the full client API is closed to this role even when called directly
  const status = await marketing.evaluate(async () => (await fetch("/api/clients?q=Contact")).status);
  expect(status).toBe(403);
});
