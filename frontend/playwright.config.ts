import { defineConfig } from "playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  // 全套用例共享一个临时后端及 SQLite 文件，按开发验收约定串行运行。
  // 单个用例中的多窗口、并发请求和幂等检查仍照常执行。
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3137",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { browserName: "chromium", viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "mobile",
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: [
    {
      command: "node tests/e2e/start-backend.mjs",
      url: "http://127.0.0.1:8137/api/system/health",
      reuseExistingServer: false,
    },
    {
      command:
        "node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3137",
      url: "http://127.0.0.1:3137",
      env: { BACKEND_URL: "http://127.0.0.1:8137" },
      reuseExistingServer: false,
    },
  ],
});
