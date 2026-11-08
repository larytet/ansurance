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
  await expect(page.locator(".who")).toContainText("מנהל");

  // two Inbox items created through the API with the browser's own session
  const csrf = await page.evaluate(async () => (await (await fetch("/api/auth/me")).json()).csrf as string);
  // filler so the list is longer than the Lens window (a fresh stack has none), then the two under test
  const filler = Array.from({ length: 22 }, (_, i): [string, string] => [`Filler ${stamp} ${i}`, "normal"]);
  for (const [title, urgency] of [...filler, [kyc, "urgent"], [weeklyTitle, "normal"]]) {
    const r = await page.evaluate(async ([t, u, c]) => {
      const res = await fetch("/api/inbox", { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": c! },
        body: JSON.stringify({ type: "approval", role: "manager", title: t, urgency: u }) });
      return res.status;
    }, [title, urgency, csrf]);
    expect(r).toBe(201);
  }
  await page.reload();
  const urgent = page.locator("li.item", { hasText: kyc });
  // earlier runs leave open items, so a new one may sit outside the Lens window: page forward, click its bar
  const bring = async (title: string) => {
    const bar = page.locator(`li.squashed button[aria-label="${title}"]`);
    const there = () => page.locator("li.item, li.squashed", { hasText: title }).or(bar).count();
    await expect(page.locator("li.item").first()).toBeVisible();
    for (let i = 0; (await there()) === 0 && (await page.getByRole("button", { name: "טען עוד" }).count()) > 0 && i < 20; i++) await page.getByRole("button", { name: "טען עוד" }).click({ timeout: 3000 }).catch(() => undefined);
    if (await bar.count()) await bar.click();
  };
  await bring(kyc);
  await expect(urgent).toHaveAttribute("data-urgency", "urgent");
  const open = Number(await page.getByTestId("count-open").innerText());
  expect(open).toBeGreaterThanOrEqual(2); // other open items may exist from earlier runs

  // filter, then act
  await page.getByRole("button", { name: "דחופים" }).click();
  await bring(kyc);
  await expect(urgent).toBeVisible();
  await expect(page.locator("li.item", { hasText: weeklyTitle })).toHaveCount(0); // not urgent
  await page.getByRole("button", { name: "הכול" }).click();
  // until the full list arrives the screen still holds the shorter urgent-only one; the count below waits for it
  // a long inbox is shown through the Lens: an item outside the window is a thin bar, click it to bring it in
  const bar = page.locator(`li.squashed button[aria-label="${weeklyTitle}"]`);
  await expect(page.locator("li.item")).toHaveCount(20); // the full list has loaded: twenty in the lens window
  const more = page.getByRole("button", { name: "טען עוד" });
  const present = () => page.locator("li.item, li.squashed", { hasText: weeklyTitle }).or(bar).count();
  for (let pages = 0; (await present()) === 0 && (await more.count()) > 0 && pages < 20; pages++) await more.click({ timeout: 3000 }).catch(() => undefined); // the button goes away after the last page
  await expect(page.locator("li.item, li.squashed").filter({ hasText: weeklyTitle }).or(bar)).not.toHaveCount(0);
  if (await bar.count()) await bar.click();
  const weekly = page.locator("li.item", { hasText: weeklyTitle });
  await weekly.getByRole("button", { name: "בוצע" }).click();
  await expect(weekly).toHaveCount(0);
  await expect(page.getByTestId("count-open")).toHaveText(String(open - 1));

  // the lens moved down to the item above; bring the urgent item back into the window
  const urgentBar = page.locator(`li.squashed button[aria-label="${kyc}"]`);
  if (await urgentBar.count()) await urgentBar.click();

  // a dismissal needs a reason
  await urgent.getByRole("button", { name: "דחייה" }).click();
  await urgent.getByLabel("סיבה (חובה)").fill("handled by phone");
  await urgent.getByRole("button", { name: "אישור" }).click();
  await expect(urgent).toHaveCount(0);
  await expect(page.getByTestId("count-open")).toHaveText(String(open - 2));

  // language and direction
  expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
  await page.getByRole("combobox", { name: "שפה" }).selectOption("en");
  expect(await page.evaluate(() => document.documentElement.dir)).toBe("ltr");
  await expect(page.getByRole("button", { name: "Done" }).first().or(page.getByText("Nothing waiting."))).toBeVisible();

  // survives a reload (cookie session), then sign out
  await page.reload();
  await expect(page.getByRole("button", { name: "Inbox" })).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  void request;
});
