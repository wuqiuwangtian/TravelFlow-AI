import { defineConfig } from "playwright/test";
import base from "./playwright.config";

// Separate from the business regression suite: these probes document FE-0's
// current behavior. Fixed defects now have regression assertions; captures go
// to ignored test-results, preserving docs/frontend-audit as the FE-0 baseline.
export default defineConfig({
  ...base,
  testDir: "./tests/audit",
  outputDir: "./test-results/audit-run",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
});
