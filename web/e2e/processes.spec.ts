import { expect, test } from "@playwright/test";
import { PASSWORD, createUser, totp } from "./helpers";

test("the Processes screen draws each of the 18 seeded definitions with its stages and dependencies", async ({ page }) => {
  const email = `proc-${Date.now()}@e2e.il`;
  const secret = createUser(email, "manager");
  await page.addInitScript(() => localStorage.setItem("lang", "en"));
  await page.goto("/");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Authenticator or recovery code").fill(totp(secret));
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("button", { name: "Processes" }).click();

  const select = page.getByRole("combobox", { name: "Processes" });
  await expect(select.locator("option")).toHaveCount(18); // the definitions load after the tab opens
  const options = await select.locator("option").all();

  // the reference process: ten stages, with stages that run in parallel
  const labels = await Promise.all(options.map((o) => o.innerText()));
  const exit = labels.find((t) => t.startsWith("Exit to retirement"));
  expect(exit).toBeTruthy();
  await select.selectOption({ label: exit as string });
  const svg = page.locator("svg.diagram");
  await expect(svg.locator("[data-stage]")).toHaveCount(10);
  expect(await svg.locator("[data-edge]").count()).toBeGreaterThanOrEqual(9);
  const xs = await svg.locator("[data-stage]").evaluateAll((els) => els.map((e) => (e.getAttribute("transform") ?? "").split(" ")[0]));
  expect(new Set(xs).size).toBeLessThan(xs.length); // at least two stages share a column: they run in parallel
  await expect(page.getByLabel("Diagram source (Mermaid)")).toHaveValue(/flowchart/);
  await expect(page.getByRole("link", { name: "Edit in Mermaid Live" })).toHaveAttribute("href", /^https:\/\/mermaid\.live\/edit#base64:/);

  // every definition draws without error
  for (const label of labels) {
    await select.selectOption({ label });
    await expect(svg.locator("[data-stage]").first()).toBeVisible();
  }
});
