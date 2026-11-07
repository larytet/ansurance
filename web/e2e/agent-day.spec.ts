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

test("an agent creates two clients with one phone number, then works the day: lead, task, meeting", async ({ page }) => {
  const stamp = Date.now();
  const secret = createUser(`day-${stamp}@e2e.il`, "agent");
  await signIn(page, `day-${stamp}@e2e.il`, secret);

  // the clients form warns when a phone number is already on another client
  const phone = `050-${String(stamp).slice(-7, -4)}-${String(stamp).slice(-4)}`;
  await page.getByRole("button", { name: "Clients" }).click();
  const form = page.getByRole("form", { name: "New client or lead" });
  for (const [name, plus] of [[`First ${stamp}`, false], [`Second ${stamp}`, true]] as const) {
    await form.getByLabel("ID number").fill(validId());
    await form.getByLabel("Name").fill(name);
    await form.getByLabel("Phone").fill(plus ? `+972-${phone.slice(1)}` : phone);
    await form.getByRole("button", { name: "Create" }).click();
    await expect(form.getByRole("status")).toContainText("Created.");
  }
  await expect(form.getByRole("status")).toContainText("Another client already has this phone number");
  await expect(form.getByRole("status").getByRole("button", { name: `First ${stamp}` })).toBeVisible();

  // Today: both are leads, and a task can be added, finished and moves between chips
  await page.getByRole("button", { name: "Today" }).click();
  await expect(page.getByRole("heading", { name: /^Leads \(\d+\)$/ })).toBeVisible();
  await expect(page.getByRole("button", { name: `First ${stamp}` })).toBeVisible();
  await page.getByLabel("New task").fill(`Call back ${stamp}`);
  await page.getByRole("button", { name: "Add task" }).click();
  const task = page.locator("li", { hasText: `Call back ${stamp}` });
  await expect(task).toBeVisible();
  await task.getByRole("checkbox").click();
  await expect(task).toHaveCount(0); // finished tasks leave "Today"
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.locator("li", { hasText: `Call back ${stamp}` })).toBeVisible();

  // the client file records a meeting and shows the service contact date
  await page.getByRole("button", { name: `First ${stamp}` }).click();
  await page.getByLabel("Subject").fill(`Review ${stamp}`);
  await page.getByRole("button", { name: "Add meeting" }).click();
  await expect(page.locator("li", { hasText: `Review ${stamp}` })).toBeVisible();
  await expect(page.getByText(/Service contact due/)).toBeVisible();
});
