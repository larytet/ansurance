import { type Page, expect, test } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

async function signIn(page: Page, email: string, secret: string, lang = "en", theme = "light") {
  await page.addInitScript(([l, t]) => { localStorage.setItem("lang", l!); localStorage.setItem("theme", t!); }, [lang, theme]);
  await page.goto("/");
  const he = lang === "he";
  await page.getByLabel(he ? "דוא״ל" : "E-mail").fill(email);
  await page.getByLabel(he ? "סיסמה" : "Password").fill(PASSWORD);
  await page.getByLabel(he ? "קוד מאמת או קוד שחזור" : "Authenticator or recovery code").fill(totp(secret));
  await page.getByRole("button", { name: he ? "כניסה" : "Sign in" }).click();
  await expect(page.getByRole("button", { name: he ? "תיבה" : "Inbox" })).toBeVisible();
}

async function api(page: Page, method: string, path: string, body?: unknown): Promise<{ status: number; json: any }> {
  return page.evaluate(async ([m, p, b]) => {
    const csrf = (await (await fetch("/api/auth/me")).json()).csrf as string;
    const r = await fetch(p as string, { method: m as string, headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: b ? (b as string) : undefined });
    let json: unknown = null;
    try { json = await r.json(); } catch { /* no body */ }
    return { status: r.status, json };
  }, [method, path, body === undefined ? "" : JSON.stringify(body)]);
}

const t = (s: string) => ({ en: s, he: `${s} עב` });
const definition = (key: string) => ({
  id: key, owner_role: "agent", name: t(`E2E ${key}`),
  stages: [
    { id: "first", name: t("First"), role: "agent", depends_on: [], checks: [{ text: t("do the first thing"), kind: "do" }] },
    { id: "second", name: t("Second"), role: "backoffice", depends_on: ["first"], checks: [{ text: t("do the second thing"), kind: "do" }] },
  ],
});

async function openEditor(page: Page, key: string, edit = "Edit this definition", tab = "Processes") {
  await page.getByRole("button", { name: tab }).click();
  await page.locator("label", { hasText: tab }).locator("select").selectOption({ value: key });
  await page.getByRole("button", { name: edit }).click();
  await expect(page.locator(".react-flow__node")).not.toHaveCount(0);
}

test("a manager edits a definition: new stage, a dependency drawn on the canvas, a checklist, publish; running cases keep the old version", async ({ page }) => {
  const stamp = Date.now();
  const key = `e2e${stamp}`;
  const secret = createUser(`edit-${stamp}@e2e.il`, "manager");
  await signIn(page, `edit-${stamp}@e2e.il`, secret);
  expect((await api(page, "POST", "/api/process-defs", definition(key))).status).toBe(201);
  const old = await api(page, "POST", "/api/processes", { key });
  expect(old.status).toBe(201);

  await openEditor(page, key);
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await expect(page.locator(".react-flow__attribution")).toBeVisible(); // the free edition's attribution stays
  await expect(page.getByText(/the server accepts this draft/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Publish a new version" })).toBeDisabled(); // nothing changed yet

  // a new stage, named in both languages, owned by the manager, with its own checklist
  await page.getByRole("button", { name: "Add a stage" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByLabel("Stage id").fill("review");
  await page.getByLabel("Name (English)").fill("Review");
  await page.getByLabel("Name (Hebrew)").fill("סקירה");
  await page.getByLabel("Owner role").selectOption("manager");
  await page.getByLabel("Item (English)").fill("check the work");
  await page.getByLabel("Item (Hebrew)").fill("בדיקת העבודה");

  // draw the dependency: drag from the handle of "second" to the handle of "review"
  const source = page.locator('.react-flow__node[data-id="second"] .react-flow__handle.source');
  const target = page.locator('.react-flow__node[data-id="review"] .react-flow__handle.target');
  const a = (await source.boundingBox())!;
  const b = (await target.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 - 30, a.y + a.height / 2 + 10, { steps: 4 }); // leave the handle first, as a hand does
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 20 });
  await page.waitForTimeout(150);
  await page.mouse.up();
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
  await expect(page.getByText(/added: review · dependency second/)).toBeVisible();

  // a loop is refused with the reason (keyboard path: the dependency boxes)
  await page.getByRole("navigation", { name: "Stages" }).getByRole("button", { name: "First" }).click();
  await page.getByRole("group", { name: /Depends on/ }).getByLabel("Review").check().catch(() => undefined);
  await expect(page.getByRole("alert").filter({ hasText: "That would make a loop" })).toBeVisible();

  await expect(page.getByText(/the server accepts this draft/)).toBeVisible();
  await page.getByRole("button", { name: "Publish a new version" }).click();
  await expect(page.getByText(/Published as version 2/)).toBeVisible();

  // the running case stays on version 1, a new one uses version 2
  const fresh = await api(page, "POST", "/api/processes", { key });
  expect(fresh.json.version).toBe(2);
  expect(fresh.json.stages.length).toBe(3);
  const still = await api(page, "GET", `/api/processes/${old.json.id}`);
  expect([still.json.version, still.json.stages.length]).toEqual([1, 2]);
});

test("the editor in Hebrew and the dark theme: canvas stays left to right, panels right to left, text and attribution visible", async ({ page }) => {
  const stamp = Date.now();
  const key = `e2h${stamp}`;
  const secret = createUser(`edith-${stamp}@e2e.il`, "manager");
  await signIn(page, `edith-${stamp}@e2e.il`, secret, "he", "dark");
  expect((await api(page, "POST", "/api/process-defs", definition(key))).status).toBe(201);
  await openEditor(page, key, "עריכת ההגדרה", "תהליכים");
  expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
  expect(await page.locator('[data-testid="canvas"]').evaluate((el) => getComputedStyle(el).direction)).toBe("ltr");
  await expect(page.locator(".react-flow.dark")).toBeVisible();
  await expect(page.locator(".react-flow__attribution")).toBeVisible();
  const node = page.locator('.react-flow__node[data-id="first"]');
  await expect(node).toContainText("First עב"); // the Hebrew name, readable
  const color = await node.evaluate((el) => getComputedStyle(el).color);
  const background = await node.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(color).not.toBe(background); // not black on dark, the mistake the read-only diagram had
  expect(await page.getByLabel("מזהה שלב").evaluate((el) => getComputedStyle(el.closest(".pe-panel")!).direction)).toBe("rtl");
});
