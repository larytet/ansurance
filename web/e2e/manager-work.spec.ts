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

/** A call made with the browser's own session, as the UI would make it. */
async function api(page: Page, method: string, path: string, body?: unknown): Promise<{ status: number; json: any }> {
  return page.evaluate(async ([m, p, b]) => {
    const csrf = (await (await fetch("/api/auth/me")).json()).csrf as string;
    const r = await fetch(p as string, { method: m as string, headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: b ? (b as string) : undefined });
    let json: unknown = null;
    try { json = await r.json(); } catch { /* no body */ }
    return { status: r.status, json };
  }, [method, path, body === undefined ? "" : JSON.stringify(body)]);
}

test("manager screens on the live stack: Overview and Trust, Complaints, Campaigns, Tax, and the client's page", async ({ page, browser }) => {
  const stamp = Date.now();
  const email = `mgr-${stamp}@e2e.il`;
  const secret = createUser(email, "manager");
  await signIn(page, email, secret);

  // a client with a policy that renews in five days, and a consent for WhatsApp
  const idn = validId();
  const client = await api(page, "POST", "/api/clients", { id_number: idn, name: `Renew ${stamp}`, phone: "050-1234567" });
  expect(client.status).toBe(201);
  const cid = client.json.id as string;
  const renew = new Date(Date.now() + 5 * 86400_000).toISOString().slice(0, 10);
  expect((await api(page, "POST", `/api/clients/${cid}/policies`, { insurer: "Harel", policy_number: `E2E-${stamp}`, product: "pension", premium_ils: "500", renewal_date: renew })).status).toBe(201);
  expect((await api(page, "POST", `/api/clients/${cid}/consent`, { channel: "whatsapp", state: "opt_in", source: "e2e" })).status).toBe(201);

  // only registered clients can be in an audience: a clean ID card, then registration
  const up = await page.evaluate(async ([id]) => {
    const csrf = (await (await fetch("/api/auth/me")).json()).csrf as string;
    const form = new FormData();
    form.append("file", new Blob(["%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF id card"], { type: "application/pdf" }), "id.pdf");
    form.append("doc_type", "id_card");
    return (await fetch(`/api/clients/${id}/documents`, { method: "POST", headers: { "x-csrf-token": csrf }, body: form })).status;
  }, [cid]);
  expect(up).toBe(201);
  expect((await api(page, "POST", `/api/clients/${cid}/register`)).status).toBe(200);

  // Overview: the counts, the compliance block, and the audit chain verifies
  await page.getByRole("button", { name: "סקירה" }).click();
  await expect(page.getByText("תיקי הכר את לקוחך שפג תוקפם", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "אימות שרשרת הביקורת" }).click();
  await expect(page.getByRole("status")).toContainText("שרשרת הביקורת תקינה");

  // Complaints: log one, see it, answer it
  await page.getByRole("button", { name: "תלונות" }).click();
  await page.getByLabel("מה קרה").fill(`The renewal letter never came ${stamp}`);
  await page.getByRole("button", { name: "יצירה" }).click();
  const row = page.locator("li", { hasText: `The renewal letter never came ${stamp}` });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "רישום תשובה" }).click();
  await row.getByLabel("תשובה").fill("We re-sent it and apologise");
  await row.getByRole("button", { name: "רישום" }).click();
  await expect(row).toContainText("נענתה");

  // Campaigns: a manager's own offer needs no approval; the audience shows who would get it
  await page.getByRole("button", { name: "קמפיינים" }).click();
  await page.getByLabel("שם").first().fill(`Pension ${stamp}`);
  await page.getByLabel(/התועלת ללקוח/).fill("a lower management fee for the same cover");
  await page.getByLabel(/נוסח ההודעה/).fill("יש לנו קרן פנסיה בדמי ניהול נמוכים יותר");
  await page.getByLabel(/לקוחות שיש להם את המוצר/).fill("pension");
  await page.getByRole("button", { name: "יצירה" }).click();
  const camp = page.locator("li", { hasText: `Pension ${stamp}` });
  await expect(camp).toBeVisible();
  await camp.getByRole("button", { name: "תצוגה מקדימה של הקהל" }).click();
  await expect(camp.getByRole("status")).toContainText(/יקבלו: [1-9]/);

  // Tax: create the certificate list for the year and see the new row with the three marks
  await page.getByRole("button", { name: "מס" }).click();
  await page.getByRole("button", { name: "יצירת הרשימה" }).click();
  const cert = page.locator("tr", { hasText: `Renew ${stamp}` });
  await expect(cert).toContainText("לאלאלא");
  await page.getByRole("button", { name: "בקשת כל החסרים" }).click();
  await expect(cert).toContainText("כןלאלא");

  // the client's own page: only their data, through the link, with no session at all
  const link = await api(page, "POST", `/api/clients/${cid}/portal-link`);
  expect(link.status).toBe(201);
  const anon = await browser.newContext({ ignoreHTTPSErrors: true });
  const portal = await anon.newPage();
  await portal.goto(`/portal#${link.json.token}`);
  await expect(portal.getByRole("heading", { level: 1 })).toContainText(`Renew ${stamp}`);
  await expect(portal.getByText(`E2E-${stamp}`, { exact: false })).toBeVisible();
  await portal.goto("/portal#not-a-real-token");
  await expect(portal.getByRole("alert")).toContainText("הקישור אינו תקף");
  await anon.close();
});

test("an agent's Today shows the renewal that is due", async ({ page }) => {
  const stamp = Date.now();
  const email = `agent-${stamp}@e2e.il`;
  const secret = createUser(email, "agent");
  await signIn(page, email, secret);
  const client = await api(page, "POST", "/api/clients", { id_number: validId(), name: `Due ${stamp}` });
  const renew = new Date(Date.now() + 4 * 86400_000).toISOString().slice(0, 10);
  await api(page, "POST", `/api/clients/${client.json.id}/policies`, { insurer: "Migdal", policy_number: `D-${stamp}`, product: "car", premium_ils: "900", renewal_date: renew });
  await page.getByRole("button", { name: "היום" }).click();
  // the new client is also a lead (status lead) and appears in the leads list: pick the renewal row
  const li = page.locator("li", { hasText: `Due ${stamp}` }).filter({ hasText: "car" });
  await expect(li).toBeVisible();
  await expect(li).toHaveClass(/attention/);
  await expect(li).toContainText("10 ימים");
  await expect(page.getByRole("heading", { name: /^לידים \(\d+\)$/ })).toBeVisible();
  await expect(page.locator("li", { hasText: `Due ${stamp}` }).filter({ hasNotText: "car" })).toBeVisible();
});
