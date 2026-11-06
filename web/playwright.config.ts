import { defineConfig } from "@playwright/test";

// Runs against the running compose stack (make up): ANS_BASE_URL defaults to the local proxy.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 1,
  use: { baseURL: process.env.ANS_BASE_URL ?? "https://127.0.0.1:8443", ignoreHTTPSErrors: true },
});
