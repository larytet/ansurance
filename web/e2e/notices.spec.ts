import { expect, test } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

test("Settings shows the open source notices and the React Flow attribution", async ({ page }) => {
  const email = `notices-${Date.now()}@e2e.il`;
  const secret = createUser(email, "agent");
  await page.goto("/");
  await page.getByLabel("דוא״ל").fill(email);
  await page.getByLabel("סיסמה").fill(PASSWORD);
  await page.getByLabel("קוד מאמת או קוד שחזור").fill(totp(secret));
  await page.getByRole("button", { name: "כניסה" }).click();
  await page.getByRole("button", { name: "הגדרות" }).click();
  const notices = page.getByRole("region", { name: "הודעות קוד פתוח" });
  await expect(notices.getByRole("link", { name: "React Flow" })).toBeVisible();
  await notices.getByText(/רכיבים/).click();
  await expect(notices.getByRole("cell", { name: "@xyflow/react", exact: true })).toBeVisible();
  await expect(notices.getByRole("cell", { name: "psycopg", exact: true })).toBeVisible(); // the reviewed LGPL driver is listed too
});
