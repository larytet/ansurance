import { expect, test } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

test("sign in, see Inbox items, act on them, switch language, sign out", async ({ page, request }) => {
  const stamp = Date.now();
  const email = `ui-${stamp}@e2e.il`;
  const kyc = `Approve the KYC flag ${stamp}`;
  const weeklyTitle = `Review the weekly report ${stamp}`;
  const secret = createUser(email, "manager");

  // the sign-in form, wrong password first
  await page.goto("/");
  await expect(page.getByRole("button", { name: "כניסה" })).toBeVisible();
  await page.getByLabel("דוא״ל").fill(email);
  await page.getByLabel("סיסמה").fill("wrong-wrong-wrong-1");
  await page.getByLabel("קוד מאמת או קוד שחזור").fill("000000");
  await page.getByRole("button", { name: "כניסה" }).click();
  await expect(page.getByRole("alert")).toContainText("הכניסה נכשלה");

  // now correctly
  await page.getByLabel("סיסמה").fill(PASSWORD);
  await page.getByLabel("קוד מאמת או קוד שחזור").fill(totp(secret));
  await page.getByRole("button", { name: "כניסה" }).click();
  await expect(page.getByRole("button", { name: "תיבה" })).toBeVisible();
  await expect(page.getByText(/מנהל/)).toBeVisible();

  // two Inbox items created through the API with the browser's own session
  const csrf = await page.evaluate(async () => (await (await fetch("/api/auth/me")).json()).csrf as string);
  for (const [title, urgency] of [[kyc, "urgent"], [weeklyTitle, "normal"]]) {
    const r = await page.evaluate(async ([t, u, c]) => {
      const res = await fetch("/api/inbox", { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": c! },
        body: JSON.stringify({ type: "approval", role: "manager", title: t, urgency: u }) });
      return res.status;
    }, [title, urgency, csrf]);
    expect(r).toBe(201);
  }
  await page.reload();
  const urgent = page.locator("li.item", { hasText: kyc });
  await expect(urgent).toHaveAttribute("data-urgency", "urgent");
  const open = Number(await page.getByTestId("count-open").innerText());
  expect(open).toBeGreaterThanOrEqual(2); // other open items may exist from earlier runs

  // filter, then act
  await page.getByRole("button", { name: "דחופים" }).click();
  await expect(urgent).toBeVisible();
  await expect(page.locator("li.item", { hasText: weeklyTitle })).toHaveCount(0); // not urgent
  await page.getByRole("button", { name: "הכול" }).click();
  const weekly = page.locator("li.item", { hasText: weeklyTitle });
  await weekly.getByRole("button", { name: "בוצע" }).click();
  await expect(weekly).toHaveCount(0);
  await expect(page.getByTestId("count-open")).toHaveText(String(open - 1));

  // a dismissal needs a reason
  await urgent.getByRole("button", { name: "דחייה" }).click();
  await urgent.getByLabel("סיבה (חובה)").fill("handled by phone");
  await urgent.getByRole("button", { name: "אישור" }).click();
  await expect(urgent).toHaveCount(0);
  await expect(page.getByTestId("count-open")).toHaveText(String(open - 2));

  // language and direction
  expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
  await page.getByRole("button", { name: "שפה" }).click();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe("ltr");
  await expect(page.getByRole("button", { name: "Done" }).first().or(page.getByText("Nothing waiting."))).toBeVisible();

  // survives a reload (cookie session), then sign out
  await page.reload();
  await expect(page.getByRole("button", { name: "Inbox" })).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  void request;
});
