import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";

const ROOT = new URL("../..", import.meta.url).pathname;
const COMPOSE = ["compose", "-f", `${ROOT}deploy/compose/docker-compose.yml`, "--env-file", `${ROOT}deploy/compose/.env`];
const PASSWORD = "an-e2e-password-1";

function totp(secret: string): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1]! & 15;
  const n = ((h[o]! & 0x7f) << 24) | (h[o + 1]! << 16) | (h[o + 2]! << 8) | h[o + 3]!;
  return String(n % 1_000_000).padStart(6, "0");
}

function createUser(email: string, role: string): string {
  const out = execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-m", "app.cli", "create-user",
    "--email", email, "--name", `E2E ${role}`, "--role", role, "--password", PASSWORD], { encoding: "utf8" });
  return /secret=([A-Z2-7]+)/.exec(out)![1]!;
}

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
