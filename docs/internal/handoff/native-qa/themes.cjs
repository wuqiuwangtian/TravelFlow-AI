// Archived from the final 2026-10-28 Windows check; use only isolated QA servers.
const assert = require("node:assert/strict");
const { nativeRuntime, assertTestServer } =
  require("node:module").createRequire(process.execPath)(
    require("node:path").join(__dirname, "runtime.cjs"),
  );
const { chromium, request, edge, output } = nativeRuntime("themes");
(async () => {
  const base = "http://127.0.0.1:3137",
    api = await request.newContext({ baseURL: base });
  await assertTestServer(api);
  const browser = await chromium.launch({
    headless: true,
    executablePath: edge,
  });
  try {
    for (const width of [1440, 390]) {
      const mobile = width < 900,
        suffix = mobile ? "mobile" : "desktop";
      const context = await browser.newContext({
        viewport: { width, height: mobile ? 844 : 1000 },
        isMobile: mobile,
        hasTouch: mobile,
        reducedMotion: "reduce",
        timezoneId: "Asia/Shanghai",
      });
      try {
        const page = await context.newPage(),
          errors = [];
        page.on("pageerror", (err) => errors.push(err.message));
        await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
          route.abort(),
        );
        const trip = await (await api.post("/api/backend/test/moves")).json();
        await api.patch(`/api/backend/api/sessions/${trip.session_id}`, {
          data: { title: `北京三日 · ${mobile ? "手机" : "电脑"}主题验收样本` },
        });
        const before = await (
          await api.get(`/api/backend/api/sessions/${trip.session_id}`)
        ).json();
        await page.goto(base + "/settings");
        await page.locator("#llm-model").waitFor();
        const region = page.getByRole("region", {
          name: "外观主题",
          exact: true,
        });
        await page.locator("#llm-model").fill("fixture-unsaved-model");
        for (const theme of ["paper", "guide", "night"]) {
          await page.locator(`[data-theme-option="${theme}"]`).click();
          assert.equal(
            await page.locator("html").getAttribute("data-theme"),
            theme,
          );
          assert.equal(
            await page.locator("#llm-model").inputValue(),
            "fixture-unsaved-model",
          );
          assert.equal(
            await page.evaluate(() => localStorage.getItem("travelflow.theme")),
            theme,
          );
          assert(
            await page.evaluate(
              () =>
                document.documentElement.scrollWidth <=
                document.documentElement.clientWidth + 1,
            ),
          );
          await region.screenshot({
            path: output(`appearance-${theme}-${suffix}-windows.png`),
          });
        }
        await page.reload();
        assert.equal(
          await page.locator("html").getAttribute("data-theme"),
          "night",
        );
        await page.goto(
          `${base}/chat/${trip.session_id}?branch=${trip.branch_id}`,
        );
        await page.locator('[aria-label="旅行路线与执行"]').waitFor();
        assert.equal(
          await page.locator("html").getAttribute("data-theme"),
          "night",
        );
        assert(
          await page.evaluate(
            () =>
              document.documentElement.scrollWidth <=
              document.documentElement.clientWidth + 1,
          ),
        );
        await page.screenshot({
          path: output(`journey-night-${suffix}-windows.png`),
          fullPage: !mobile,
        });
        await page
          .getByRole("button", { name: "完整路书", exact: true })
          .click();
        await page.locator(".journey-book[open] .prose-report").waitFor();
        await page.locator(".journey-book").scrollIntoViewIfNeeded();
        await page.screenshot({
          path: output(`book-night-${suffix}-windows.png`),
          fullPage: false,
        });
        await page.goto(
          `${base}/workflow/${trip.session_id}?branch=${trip.branch_id}&view=process`,
        );
        await page
          .getByRole("region", { name: "规划流程节点", exact: true })
          .waitFor();
        await page
          .getByRole("region", { name: "规划流程节点", exact: true })
          .scrollIntoViewIfNeeded();
        assert(
          await page.evaluate(
            () =>
              document.documentElement.scrollWidth <=
              document.documentElement.clientWidth + 1,
          ),
        );
        await page.screenshot({
          path: output(`process-night-${suffix}-windows.png`),
          fullPage: false,
        });
        await page.goto(
          `${base}/workflow/${trip.session_id}?branch=${trip.branch_id}`,
        );
        await page
          .getByRole("region", { name: "默认工程模板", exact: true })
          .getByRole("button")
          .filter({ hasText: "结构化行程" })
          .click();
        await page
          .getByRole("button", { name: "聚焦节点", exact: true })
          .click();
        const node = page.getByRole("button", {
          name: "选择节点 旅行需求",
          exact: true,
        });
        const position = () =>
          node.locator("..").evaluate((el) => [el.style.left, el.style.top]);
        const old = await position();
        await node.hover();
        assert.deepEqual(await position(), old);
        await page.screenshot({
          path: output(`studio-night-${suffix}-windows.png`),
          fullPage: false,
        });
        await page
          .getByRole("button", { name: "保存为我的模板", exact: true })
          .click();
        const dialog = page.getByRole("dialog", {
          name: "保存个人工程模板",
          exact: true,
        });
        await dialog.waitFor();
        const box = await dialog.boundingBox();
        assert(
          box.x >= 0 &&
            box.x + box.width <=
              (await page.evaluate(() => document.documentElement.clientWidth)),
        );
        await page.screenshot({
          path: output(`dialog-night-${suffix}-windows.png`),
          fullPage: false,
        });
        await dialog
          .getByRole("button", { name: "关闭模板编辑", exact: true })
          .click();
        const after = await (
          await api.get(`/api/backend/api/sessions/${trip.session_id}`)
        ).json();
        assert.deepEqual(after.versions, before.versions);
        assert.deepEqual(errors, []);
        console.log(
          `Windows Edge ${width}: A/B/C choice/draft/persistence, C journey/book/process/canvas/dialog passed`,
        );
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
    await api.dispose();
  }
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
