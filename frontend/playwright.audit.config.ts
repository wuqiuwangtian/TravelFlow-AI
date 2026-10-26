import { defineConfig } from "playwright/test";
import base from "./playwright.config";

// Separate from the business regression suite: these probes document FE-0's
// current behavior, including known defects, without asserting defects must last.
export default defineConfig({
  ...base,
  testDir: "./tests/audit",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
});
