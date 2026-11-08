import { expect, test, type Page } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

function validId(): string {
  const body = String(Math.floor(Math.random() * 1e8)).padStart(8, "0");
  let total = 0;
  [...body].forEach((d, i) => { const n = Number(d) * (1 + (i % 2)); total += n > 9 ? n - 9 : n; });
  return body + String((10 - (total % 10)) % 10);
}

/** A small real one-page PDF with a line of text, so the sandbox has something to draw. */
function pdf(): Buffer {
  const objs = [
    "<</Type/Catalog/Pages 2 0 R>>",
    "<</Type/Pages/Kids[3 0 R]/Count 1>>",
    "<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    "", "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
  ];
  const stream = "BT /F1 24 Tf 20 100 Td (Hello sandbox) Tj ET";
  objs[3] = `<</Length ${stream.length}>>\nstream\n${stream}\nendstream`;
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objs.forEach((o, i) => { offsets.push(out.length); out += `${i + 1} 0 obj\n${o}\nendobj\n`; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer<</Size ${objs.length + 1}/Root 1 0 R>>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out);
}

async function signIn(page: Page, email: string, secret: string) {
  await page.goto("/");
  await page.getByLabel("דוא״ל").fill(email);
  await page.getByLabel("סיסמה").fill(PASSWORD);
  await page.getByLabel("קוד מאמת או קוד שחזור").fill(totp(secret));
  await page.getByRole("button", { name: "כניסה" }).click();
  await expect(page.getByRole("button", { name: "תיבה" })).toBeVisible();
}

test("a document is shown as server-made page images, and the original needs a warning", async ({ page }) => {
  const stamp = Date.now();
  const email = `docs-${stamp}@e2e.il`;
  await signIn(page, email, createUser(email, "manager"));

  await page.getByRole("button", { name: "לקוחות" }).click();
  const csrf = await page.evaluate(async () => (await (await fetch("/api/auth/me")).json()).csrf as string);
  const created = await page.evaluate(async ([id, name, token]) => {
    const r = await fetch("/api/clients", { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": token as string }, body: JSON.stringify({ id_number: id, name }) });
    return (await r.json()).id as string;
  }, [validId(), `Docs ${stamp}`, csrf]);
  const bytes = pdf();
  const status = await page.evaluate(async ([cid, token, b64]) => {
    const form = new FormData();
    form.append("file", new Blob([Uint8Array.from(atob(b64 as string), (c) => c.charCodeAt(0))], { type: "application/pdf" }), "policy.pdf");
    form.append("doc_type", "policy");
    return (await fetch(`/api/clients/${cid}/documents`, { method: "POST", headers: { "x-csrf-token": token as string }, body: form })).status;
  }, [created, csrf, bytes.toString("base64")]);
  expect(status).toBe(201);

  await page.getByRole("searchbox").fill(`Docs ${stamp}`);
  await page.getByRole("button", { name: new RegExp(`Docs ${stamp}`) }).first().click();

  // viewing: the page image comes from the sandbox; no frame, embed or object shows the file
  await page.getByRole("button", { name: "צפייה" }).first().click();
  const img = page.getByRole("img", { name: "עמוד 1" });
  await expect(img).toBeVisible({ timeout: 60_000 });
  await expect(img).toHaveAttribute("src", /\/api\/documents\/[0-9a-f-]+\/pages\/1$/);
  expect(await img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(50);
  expect(await page.locator("iframe, embed, object").count()).toBe(0);

  // the original: a warning first, and the link carries the acknowledgement
  await page.getByRole("button", { name: "הורדת המקור" }).first().click();
  const box = page.getByRole("alertdialog");
  await expect(box).toContainText("אינו מהימן");
  const href = await box.getByRole("link", { name: "הורדה בכל זאת" }).getAttribute("href");
  expect(href).toMatch(/\/content\?acknowledge=true$/);
  const without = await page.evaluate(async (h) => (await fetch((h as string).replace("?acknowledge=true", ""))).status, href);
  expect(without).toBe(409); // the server asks for the acknowledgement itself
  const download = page.waitForEvent("download");
  await box.getByRole("link", { name: "הורדה בכל זאת" }).click();
  expect((await download).suggestedFilename()).toBe("policy.pdf");
});
