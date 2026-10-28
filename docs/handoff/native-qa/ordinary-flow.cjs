// Archived from the final 2026-10-28 Windows check; use only isolated QA servers.
const assert = require("node:assert/strict");
const { nativeRuntime, assertTestServer } =
  require("node:module").createRequire(process.execPath)(
    require("node:path").join(__dirname, "runtime.cjs"),
  );
const { chromium, request, edge, output } = nativeRuntime("ordinary-flow");
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
        const data = await (
            await api.post("/api/backend/api/sessions", { data: {} })
          ).json(),
          sid = data.session.id,
          bid = data.branches[0].id;
        await api.patch(`/api/backend/api/sessions/${sid}`, {
          data: {
            title: `北京三日 · ${mobile ? "手机" : "电脑"}普通流程验收样本`,
          },
        });
        const post = (text) =>
          api.post("/api/backend/api/chat", {
            data: { session_id: sid, message: text },
          });
        assert((await post("从上海出发玩三天，少走路，请推荐城市")).ok());
        assert((await post("预算2000元")).ok());
        let fail = true;
        await page.route(
          `**/api/backend/api/branches/${bid}/messages`,
          (route) =>
            fail
              ? route.fulfill({
                  status: 503,
                  json: {
                    error: {
                      code: "BACKEND_UNREACHABLE",
                      message: "验收样本：对话历史暂时不可读，请原位重试。",
                    },
                  },
                })
              : route.continue(),
        );
        await page.goto(`${base}/chat/${sid}`);
        const history = page.getByRole("region", {
          name: "对话历史状态",
          exact: true,
        });
        await history.getByRole("alert").waitFor();
        const input = page.getByRole("textbox", {
          name: "旅行对话输入",
          exact: true,
        });
        await input.fill("保留未发送的想法");
        assert(
          await page.evaluate(
            () =>
              document.documentElement.scrollWidth <=
              document.documentElement.clientWidth + 1,
          ),
        );
        if (mobile)
          await page
            .getByRole("navigation", { name: "旅行快捷导航", exact: true })
            .getByRole("button", { name: "对话", exact: true })
            .click();
        await page.screenshot({
          path: output(`history-error-${suffix}-windows.png`),
          fullPage: false,
        });
        fail = false;
        await history
          .getByRole("button", { name: "重试", exact: true })
          .click();
        await history.waitFor({ state: "detached" });
        assert.equal(await input.inputValue(), "保留未发送的想法");
        assert(
          (
            await page.locator('[aria-label="对话消息"]').textContent()
          ).includes("1. 北京"),
        );
        await page.screenshot({
          path: output(`history-recovered-${suffix}-windows.png`),
          fullPage: false,
        });
        const send = async (text) => {
          await input.fill(text);
          const done = page.waitForResponse((r) =>
            r.url().endsWith("/api/chat/stream"),
          );
          await page.getByRole("button", { name: "发送", exact: true }).click();
          assert((await (await done).text()).includes("event: done"));
          await page
            .getByRole("button", { name: "发送", exact: true })
            .waitFor();
        };
        await send("第二个");
        await page.getByText(/刚才有 1 个候选/).waitFor();
        const before = await (
          await api.get(`/api/backend/api/sessions/${sid}`)
        ).json();
        assert.equal(before.versions[bid].length, 0);
        await send("就选北京吧");
        await page.getByLabel("当天行程", { exact: true }).waitFor();
        if (mobile)
          await page
            .getByRole("navigation", { name: "旅行快捷导航", exact: true })
            .getByRole("button", { name: "今日", exact: true })
            .click();
        await page.screenshot({
          path: output(`selected-route-${suffix}-windows.png`),
          fullPage: false,
        });
        await send("第1天第1站已完成，午餐花了86.35元");
        const recap = page.getByRole("region", {
          name: "旅行回顾",
          exact: true,
        });
        await recap
          .getByRole("button", { name: "打开旅行回顾", exact: true })
          .click();
        await recap
          .getByRole("textbox", { name: "回顾标题", exact: true })
          .fill(`北京三日 · ${mobile ? "手机" : "电脑"}回顾验收样本`);
        await recap
          .getByRole("textbox", { name: "我的回忆", exact: true })
          .fill("一段自己写下的旅行记录。");
        await recap
          .getByRole("button", { name: "保存旅行回顾", exact: true })
          .click();
        await recap
          .getByRole("status")
          .filter({ hasText: "旅行回顾已保存" })
          .waitFor();
        await recap
          .getByRole("button", { name: "结束旅行并收录回顾", exact: true })
          .click();
        await recap
          .getByRole("status")
          .filter({ hasText: "旅行已结束并收录回顾" })
          .waitFor();
        const saved = await (
            await api.get(`/api/backend/api/sessions/${sid}`)
          ).json(),
          version = saved.versions[bid].at(-1).id;
        const detail = await (
          await api.get(`/api/backend/api/versions/${version}`)
        ).json();
        assert.equal(detail.itinerary.expenses[0].amount_cents, 8635);
        assert(detail.itinerary.ended_at);
        await page.goto(`${base}/recaps/${version}`);
        await page.getByRole("heading", { name: /回顾验收样本/ }).waitFor();
        assert((await page.locator("main").textContent()).includes("86.35"));
        await page.screenshot({
          path: output(`recap-${suffix}-windows.png`),
          fullPage: true,
        });
        assert(
          (
            await (
              await api.get(
                `/api/backend/api/versions/${version}/export?format=html`,
              )
            ).text()
          ).includes("86.35"),
        );
        assert.deepEqual(errors, []);
        console.log(
          `Windows Edge ${width}: history failure/retry/draft, short choice, actual expense, recap/end/export passed`,
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
