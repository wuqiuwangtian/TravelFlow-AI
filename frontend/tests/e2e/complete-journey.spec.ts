import { expect, test, type Page } from "playwright/test";
import type { RecapView, SessionDetail, VersionDetail } from "../../lib/types";

const proxy = "/api/backend";
const input = (page: Page) =>
  page.getByRole("textbox", { name: "旅行对话输入", exact: true });
const sendButton = (page: Page) =>
  page.getByRole("button", { name: "发送", exact: true });
const prompt = (page: Page) => page.getByLabel("待补充的信息", { exact: true });
const failed = (page: Page) =>
  page.getByLabel("消息发送未完成", { exact: true });
const attention = (page: Page) =>
  page.getByRole("region", { name: "行程待核对事项", exact: true });
async function latest(page: Page, sid: string) {
  const session: SessionDetail = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const branch = session.branches.find((item) => item.is_primary)!;
  const versions = session.versions[branch.id];
  const detail: VersionDetail | null = versions.length
    ? await (
        await page.request.get(`${proxy}/api/versions/${versions.at(-1)!.id}`)
      ).json()
    : null;
  return { session, bid: branch.id, versions, detail };
}
async function send(page: Page, text: string, expectError = false) {
  await input(page).fill(text);
  const response = page.waitForResponse((result) =>
    result.url().endsWith("/api/chat/stream"),
  );
  await sendButton(page).click();
  expect(await (await response).text()).toContain(
    expectError ? "event: error" : "event: done",
  );
  if (expectError) await expect(failed(page)).toBeVisible();
  else await expect(sendButton(page)).toBeEnabled();
}
async function retry(page: Page) {
  await page.getByRole("button", { name: "重试这条消息", exact: true }).click();
  await expect(failed(page)).toHaveCount(0);
  await expect(sendButton(page)).toBeEnabled();
}
async function truncateNextReply(page: Page) {
  await page.route(
    "**/api/backend/api/chat/stream",
    async (route) => {
      const result = await route.fetch();
      expect(await result.text()).toContain("event: done");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: custom\ndata: {"type":"progress","message":"正在确认保存结果"}\n\n',
      });
    },
    { times: 1 },
  );
}
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1,
    ),
  ).toBe(true);
}

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

for (const interrupted of [false, true]) {
  test(`同一旅行从推荐、澄清、跨天调整和实付到回顾导出${interrupted ? "，多处故障后继续" : "，正常路径"}`, async ({
    page,
  }, info) => {
    test.setTimeout(120_000);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const requests: { message: string; request_id: string }[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/api/chat/stream"))
        requests.push(request.postDataJSON());
    });
    await page.goto("/");
    await page.getByRole("button", { name: "新建旅行", exact: true }).click();
    await expect(page).toHaveURL(/\/chat\//);
    const sid = page.url().split("/").at(-1)!;
    const failureCase = interrupted
      ? ` flow-error-${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`
      : "";

    await test.step("从未定目的地开始，补一次条件后继续推荐", async () => {
      await send(
        page,
        "连贯主流程验收：从上海出发玩三天，少走路，请先推荐城市" + failureCase,
      );
      const conditions = page.getByRole("region", {
        name: "本次旅行条件",
        exact: true,
      });
      await expect(conditions).toContainText("上海");
      await expect(conditions).toContainText("还需要知道：大致预算");
      await send(page, "两人，全团预算2000元");
      await expect(conditions).toContainText("已推荐的城市");
      await expect(conditions).toContainText("2,000 元（全团）");
      await page.reload();
      await expect(conditions).toContainText("北京");
      expect((await latest(page, sid)).versions).toHaveLength(0);
      await send(page, "第一个", interrupted);
      if (interrupted) {
        await expect(failed(page)).toContainText("模型暂时无法鉴权");
        await expect(conditions).toContainText("行程生成阶段");
        await expect(conditions).not.toContainText(
          /正在生成行程|正在检索旅行资料/,
        );
        await page.reload();
        await expect(conditions).toContainText("行程生成阶段");
        await expect(conditions).not.toContainText(
          /正在生成行程|正在检索旅行资料/,
        );
        await page.screenshot({
          path: info.outputPath("generation-stopped.png"),
          fullPage: true,
        });
        await expect(input(page)).toHaveValue("第一个");
        await retry(page);
      }
      await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
      const generated = await latest(page, sid);
      expect(generated.versions).toHaveLength(1);
      expect(generated.detail!.requirements).toMatchObject({
        destination: "北京",
        origin: "上海",
        duration_days: 3,
        budget: 2000,
        travelers: 2,
        budget_basis: "total",
        departure_date: null,
      });
      expect(generated.detail!.itinerary!.days).toHaveLength(3);
    });

    let state = await latest(page, sid);
    const firstVersion = state.detail!;
    const delayedHistory: {
      current?: { release: () => void; finished: Promise<void> };
    } = {};
    await test.step("历史与路书独立恢复，不重发消息或清空输入", async () => {
      if (!interrupted) return;
      const draft = "保留尚未发送的下一步想法";
      await input(page).fill(draft);
      await page.route(
        `**/api/backend/api/branches/${state.bid}/messages`,
        (route) =>
          route.fulfill({
            status: 503,
            json: { error: { message: "贯通验收：对话读取暂时失败" } },
          }),
        { times: 1 },
      );
      await page.route(
        `**/api/backend/api/versions/${firstVersion.version.id}`,
        (route) =>
          route.fulfill({
            status: 503,
            json: { error: { message: "贯通验收：路书读取暂时失败" } },
          }),
        { times: 1 },
      );
      await page.reload();
      const history = page.getByRole("region", {
        name: "对话历史状态",
        exact: true,
      });
      const book = page.getByRole("region", {
        name: "路书读取状态",
        exact: true,
      });
      await expect(history).toContainText("对话读取暂时失败");
      await expect(book).toContainText("路书读取暂时失败");
      const sends = requests.length;
      await history.getByRole("button", { name: "重试", exact: true }).click();
      await expect(history).toHaveCount(0);
      await expect(book).toContainText("路书读取暂时失败");
      await book.getByRole("button", { name: "重试", exact: true }).click();
      await expect(book).toHaveCount(0);
      await expect(input(page)).toHaveValue(draft);
      expect(requests).toHaveLength(sends);
      expect((await latest(page, sid)).versions).toHaveLength(1);
    });

    await test.step("通过汇总定位真实歧义，刷新后选择地点，丢失回复可原请求重试", async () => {
      await attention(page).locator("summary").click();
      await expect(attention(page)).toContainText("地点 1 处待明确");
      await attention(page)
        .getByRole("button", { name: "查看第1天第3站待明确地点", exact: true })
        .click();
      await page
        .getByRole("button", { name: "确认第1天第3站的地点", exact: true })
        .click();
      await expect(prompt(page)).toContainText("西单店");
      expect((await latest(page, sid)).versions).toHaveLength(1);
      await page.reload();
      await expect(prompt(page)).toContainText("西单店");
      await input(page).fill("这段草稿不作为地点选择发送");
      if (interrupted) await truncateNextReply(page);
      await prompt(page)
        .getByRole("button", { name: /西单店.*西单路2号/ })
        .click();
      if (interrupted) {
        await expect(failed(page)).toBeVisible();
        const committed = await latest(page, sid);
        expect(committed.versions).toHaveLength(2);
        await page.reload();
        await retry(page);
      }
      await expect(prompt(page)).toHaveCount(0);
      await expect(input(page)).toHaveValue("这段草稿不作为地点选择发送");
      state = await latest(page, sid);
      expect(state.versions).toHaveLength(2);
      expect(state.detail!.itinerary!.days[0].activities[2].place!.id).toBe(
        "west",
      );
      expect(state.detail!.itinerary!.expenses).toHaveLength(0);
    });

    await test.step("一次对话记录完成、实付和预约，未知费用由用户补估价", async () => {
      await send(
        page,
        "第1天第2站已完成，午餐花了86.35元，第2天第2站已预约14:00，停留60分钟",
      );
      state = await latest(page, sid);
      expect(state.detail!.itinerary!.expenses).toHaveLength(1);
      expect(state.detail!.itinerary!.expenses[0].amount_cents).toBe(8635);
      expect(
        state.detail!.itinerary!.days[1].activities[1].booking!.start_time,
      ).toBe("14:00");
      const summary = attention(page).locator("details");
      await summary.evaluate((node) => {
        (node as HTMLDetailsElement).open = true;
      });
      await attention(page)
        .getByRole("button", { name: "查看待明确费用：展览参观", exact: true })
        .click();
      const row = page.getByLabel("费用项目：展览参观", { exact: true });
      await row
        .getByRole("button", { name: "修改展览参观预估", exact: true })
        .click();
      const form = row.getByRole("form", { name: "修改预估费用", exact: true });
      await form.getByRole("textbox").fill("30.50");
      await form.getByRole("button", { name: "保存预估", exact: true }).click();
      await expect(form).toHaveCount(0);
      state = await latest(page, sid);
      expect(
        state.detail!.budget_overview!.rows.find(
          (item) => item.title === "展览参观",
        )!.planned_cents,
      ).toBe(3050);
    });

    await test.step("主动分叉与切换保留各自草稿，旧历史稍后到达", async () => {
      if (!interrupted) return;
      await input(page).fill("主方案未发送的补充");
      await page.getByRole("button", { name: "完整路书", exact: true }).click();
      page.once("dialog", (dialog) => dialog.accept("贯通备选"));
      await page
        .locator(".journey-book li")
        .first()
        .getByRole("button", { name: "从这里分叉", exact: true })
        .click();
      await page.getByRole("button", { name: "贯通备选", exact: true }).click();
      await expect(input(page)).toHaveValue("");
      await input(page).fill("备选方案独立草稿");
      let release!: () => void, finish!: () => void;
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      const finished = new Promise<void>((resolve) => {
        finish = resolve;
      });
      let waiting = false;
      await page.route(
        `**/api/backend/api/branches/${state.bid}/messages`,
        async (route) => {
          const response = await route.fetch();
          waiting = true;
          await held;
          await route.fulfill({ response });
          finish();
        },
        { times: 1 },
      );
      await page.getByRole("button", { name: "主方案", exact: true }).click();
      await expect.poll(() => waiting).toBe(true);
      await expect(input(page)).toHaveValue("主方案未发送的补充");
      delayedHistory.current = { release, finished };
    });

    await test.step("途中变化先预览两天，刷新与重试均不重复应用", async () => {
      const before = await latest(page, sid);
      await send(
        page,
        "第1天23:50我在北京站，次日03:00前回示例酒店，联动第2天",
      );
      await expect(prompt(page)).toContainText("应用两天调整");
      if (delayedHistory.current) {
        delayedHistory.current.release();
        await delayedHistory.current.finished;
        await page.evaluate(() => new Promise(requestAnimationFrame));
        await expect(prompt(page)).toContainText("应用两天调整");
      }
      expect((await latest(page, sid)).versions).toHaveLength(
        before.versions.length,
      );
      await page.reload();
      if (interrupted) await truncateNextReply(page);
      await prompt(page)
        .getByRole("button", { name: "应用两天调整", exact: true })
        .click();
      if (interrupted) {
        await expect(failed(page)).toBeVisible();
        await page.reload();
        await retry(page);
      }
      await expect(prompt(page)).toHaveCount(0);
      await expect(
        page.getByLabel("两天联动说明", { exact: true }),
      ).toBeVisible();
      state = await latest(page, sid);
      expect(state.versions).toHaveLength(before.versions.length + 1);
      expect(state.detail!.itinerary!.expenses).toEqual(
        before.detail!.itinerary!.expenses,
      );
      expect(state.detail!.itinerary!.days[2]).toEqual(
        before.detail!.itinerary!.days[2],
      );
      expect(
        state.detail!.itinerary!.days[1].activities.find(
          (item) => item.title === "展览参观",
        )!.booking!.start_time,
      ).toBe("14:00");
      expect(
        state.detail!.budget_overview!.rows.find(
          (item) => item.title === "展览参观",
        )!.planned_cents,
      ).toBe(3050);
      expect(
        state.detail!.itinerary!.days[0].activities.find(
          (item) => item.place_name === "北京站",
        )!.progress,
      ).toBe("not_started");
    });

    await test.step("补记经历与到账退款，保存回顾并导出同一份事实", async () => {
      await send(page, "第1天第5站已完成");
      await send(page, "第1天午餐退款到账10元");
      const recap = page.getByRole("region", { name: "旅行回顾", exact: true });
      await recap
        .getByRole("button", { name: "打开旅行回顾", exact: true })
        .click();
      await recap
        .getByLabel("回顾标题", { exact: true })
        .fill("连贯旅行 " + sid);
      await recap
        .getByRole("textbox", { name: "我的回忆", exact: true })
        .fill("这是我实际记录的故宫与夜间散步 📷");
      await recap
        .getByRole("button", { name: "保存旅行回顾", exact: true })
        .click();
      await expect(
        recap.getByRole("status").filter({ hasText: "旅行回顾已保存" }),
      ).toBeVisible();
      await recap
        .getByRole("button", { name: "结束旅行并收录回顾", exact: true })
        .click();
      await expect(
        recap.getByRole("status").filter({ hasText: "旅行已结束并收录回顾" }),
      ).toBeVisible();
      state = await latest(page, sid);
      const final = state.detail!;
      expect(final.itinerary!.expenses).toHaveLength(1);
      expect(final.itinerary!.expenses[0].amount_cents).toBe(8635);
      expect(final.itinerary!.adjustments).toHaveLength(1);
      expect(final.itinerary!.ledger_summary.net_expense_cents).toBe(7635);
      expect(
        final.itinerary!.days[1].activities.find(
          (item) => item.title === "展览参观",
        )!.progress,
      ).toBe("not_started");
      const recapView: RecapView = await (
        await page.request.get(
          `${proxy}/api/versions/${final.version.id}/recap`,
        )
      ).json();
      expect(recapView.days.flatMap((day) => day.completed)).toEqual([
        expect.stringContaining("故宫游览"),
        expect.stringContaining("夜间散步"),
      ]);
      expect(
        recapView.days.flatMap((day) => day.unconfirmed).join("；"),
      ).toContain("展览参观");
      if (interrupted) {
        const other = state.session.branches.find(
          (branch) => branch.title === "贯通备选",
        )!;
        const otherDetail: VersionDetail = await (
          await page.request.get(
            `${proxy}/api/versions/${state.session.versions[other.id].at(-1)!.id}`,
          )
        ).json();
        expect(otherDetail.itinerary!.expenses).toEqual(
          final.itinerary!.expenses,
        );
        expect(otherDetail.itinerary!.adjustments).toHaveLength(0);
        expect(otherDetail.itinerary!.ended_at).toBeNull();
      }
      await page.goto(`/recaps/${final.version.id}`);
      await expect(page.locator("main")).toContainText(
        "这是我实际记录的故宫与夜间散步 📷",
      );
      await expect(page.locator("main")).toContainText("76.35");
      for (const endpoint of [
        "export?format=html",
        "recap/export?format=html",
        "recap/export?format=md",
      ]) {
        const exported = await page.request.get(
          `${proxy}/api/versions/${final.version.id}/${endpoint}`,
        );
        expect(exported.ok()).toBe(true);
        expect(await exported.text()).toContain("76.35");
      }
      expect((await latest(page, sid)).session.versions).toEqual(
        state.session.versions,
      );
      const old = await (
        await page.request.get(
          `${proxy}/api/versions/${firstVersion.version.id}`,
        )
      ).json();
      expect(old.itinerary).toEqual(firstVersion.itinerary);
      await noOverflow(page);
    });

    expect(requests).toHaveLength(interrupted ? 13 : 10);
    expect(new Set(requests.map((request) => request.request_id)).size).toBe(
      10,
    );
    expect(errors).toEqual([]);
    await info.attach("journey-operations", {
      body: JSON.stringify(
        {
          interrupted,
          chat_requests: requests.length,
          distinct_messages: 10,
          recovery_clicks: interrupted ? 5 : 0,
          deliberate_branch_switches: interrupted ? 2 : 0,
          saved_versions: state.versions.length,
          requests,
        },
        null,
        2,
      ),
      contentType: "application/json",
    });
  });
}
