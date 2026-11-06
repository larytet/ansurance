import { expect, test, type Page } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

function validId(): string {
  const body = String(Math.floor(Math.random() * 1e8)).padStart(8, "0");
  let total = 0;
  [...body].forEach((d, i) => { const n = Number(d) * (1 + (i % 2)); total += n > 9 ? n - 9 : n; });
  return body + String((10 - (total % 10)) % 10);
}

async function signIn(page: Page, email: string, secret: string) {
  await page.goto("/");
  await page.getByLabel("דוא״ל").fill(email);
  await page.getByLabel("סיסמה").fill(PASSWORD);
  await page.getByLabel("קוד מאמת או קוד שחזור").fill(totp(secret));
  await page.getByRole("button", { name: "כניסה" }).click();
  await expect(page.getByRole("button", { name: "תיבה" })).toBeVisible();
}

test("open a client file, upload a document, record consent, start a process", async ({ page }) => {
  const stamp = Date.now();
  const email = `file-${stamp}@e2e.il`;
  const secret = createUser(email, "manager");
  await signIn(page, email, secret);

  const name = `Browser Client ${stamp}`;
  const csrf = await page.evaluate(async () => (await (await fetch("/api/auth/me")).json()).csrf as string);
  const status = await page.evaluate(async ([n, id, c]) => (await fetch("/api/clients", {
    method: "POST", headers: { "content-type": "application/json", "x-csrf-token": c! },
    body: JSON.stringify({ id_number: id, name: n, phone: "050-123-4567" }) })).status, [name, validId(), csrf]);
  expect(status).toBe(201);

  // find the client by typing part of the name, then open the file
  await page.getByRole("button", { name: "לקוחות" }).click();
  await page.getByRole("searchbox").fill(String(stamp));
  await page.getByRole("button", { name }).click();
  await expect(page.getByRole("heading", { name: new RegExp(name) })).toBeVisible();
  await expect(page.getByText("050-123-4567")).toBeVisible();
  await expect(page.getByText("אין מסמכים.")).toBeVisible();

  // a real upload: the browser sends it, the API scans it with ClamAV and stores it
  await page.getByLabel("בחרו קובץ").setInputFiles({
    name: `policy-${stamp}.pdf`, mimeType: "application/pdf", buffer: Buffer.from(`%PDF-1.4\n% ${stamp}\n%%EOF`),
  });
  const row = page.locator("li[data-scan]", { hasText: `policy-${stamp}.pdf` });
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute("data-scan", "clean");
  const href = await row.getByRole("link", { name: "הורדה" }).getAttribute("href");
  const body = await page.evaluate(async (h) => (await (await fetch(h!)).text()), href);
  expect(body).toContain(`% ${stamp}`); // the exact bytes come back

  // consent needs a source
  const wa = page.locator("li[data-channel=whatsapp]");
  await expect(wa).toContainText("לא נרשמה הסכמה");
  await page.getByLabel("מקור ההסכמה (חובה)").fill("signed form");
  await wa.getByRole("button", { name: "הסכמה" }).click();
  await expect(wa).toContainText("הסכים/ה");
  await wa.getByRole("button", { name: "הסרה" }).click();
  await expect(wa).toContainText("הוסר/ה");

  // start a process: its pipeline appears, with locked and ready stages
  await page.getByLabel("פתיחת תהליך").selectOption({ label: "קליטת לקוח חדש (הכר את לקוחך)" });
  await page.getByRole("button", { name: "פתיחה" }).click();
  const proc = page.locator("li[data-process=onb]");
  await expect(proc).toBeVisible();
  await expect(proc.locator(".stage.ready")).toHaveCount(2); // identify and KYC can run in parallel
  await expect(proc.locator(".stage.locked")).toHaveCount(2);

  // back to the list
  await page.getByRole("button", { name: "חזרה לרשימה" }).click();
  await expect(page.getByRole("searchbox")).toBeVisible();
});
