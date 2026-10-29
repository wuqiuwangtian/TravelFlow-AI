import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const conditions = (page: Page) =>
  page.getByRole("region", { name: "本次旅行条件", exact: true });
const bookRead = (page: Page) =>
  page.getByRole("region", { name: "路书读取状态", exact: true });
const input = (page: Page) =>
  page.getByRole("textbox", { name: "旅行对话输入", exact: true });
const routePanel = (page: Page) => page.getByLabel("当天行程", { exact: true });
function gate() {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { wait, release };
}
async function create(page: Page) {
  const data = await (
    await page.request.post(`${proxy}/api/sessions`, { data: {} })
  ).json();
  return { sid: data.session.id, bid: data.branches[0].id };
}
async function post(page: Page, sid: string, message: string, branch?: string) {
  const response = await page.request.post(`${proxy}/api/chat`, {
    data: { session_id: sid, branch_id: branch, message },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}
async function state(page: Page, sid: string) {
  return (await page.request.get(`${proxy}/api/sessions/${sid}`)).json();
}
async function send(page: Page, text: string) {
  await input(page).fill(text);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/api/chat/stream"),
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect(await (await response).text()).toContain("event: done");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
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
async function rename(page: Page) {
  await page
    .getByRole("button", { name: "重命名这个旅行", exact: true })
    .click();
  await page
    .getByRole("form", { name: "修改旅行名称", exact: true })
    .getByRole("textbox")
    .fill("读取恢复验收");
  await page.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(
    page.getByRole("form", { name: "修改旅行名称", exact: true }),
  ).toHaveCount(0);
}

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("集中展示已有条件、必要缺项与推荐意愿，刷新不生成版本", async ({
  page,
}, testInfo) => {
  const trip = await create(page);
  await post(page, trip.sid, "从上海出发玩三天，少走路，请推荐城市");
  await page.goto(`/chat/${trip.sid}`);
  await expect(conditions(page)).toContainText("上海");
  await expect(conditions(page)).toContainText("3 天");
  await expect(conditions(page)).toContainText("少走路");
  await expect(conditions(page)).toContainText("还需要知道：大致预算");
  await expect(conditions(page)).toContainText("已记住你想先推荐城市");
  await expect(conditions(page)).not.toContainText("还需要知道：目的地");
  await send(page, "预算2000元");
  await expect(conditions(page)).toContainText("2,000 元（口径未定）");
  await expect(conditions(page)).toContainText("已推荐的城市");
  await expect(conditions(page)).toContainText("北京");
  await expect(
    conditions(page).getByText("出行人数", { exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(conditions(page)).toContainText("已推荐的城市");
  expect((await state(page, trip.sid)).versions[trip.bid]).toHaveLength(0);
  await noOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath("planning-context.png"),
    fullPage: true,
  });
});

test("条件摘要读取失败能保留新摘要，迟到的旧条件不能覆盖发送结果", async ({
  page,
}) => {
  const trip = await create(page);
  await post(page, trip.sid, "从上海出发玩三天，少走路，请推荐城市");
  const held = gate();
  let waiting = false;
  await page.route(
    `**/api/backend/api/branches/${trip.bid}/messages`,
    async (route) => {
      const data = await (await route.fetch()).json();
      data.planning_context.requirements.origin = "过期出发地";
      waiting = true;
      await held.wait;
      await route.fulfill({ json: data });
    },
    { times: 1 },
  );
  await page.goto(`/chat/${trip.sid}`);
  await expect.poll(() => waiting).toBe(true);
  await page.route(
    `**/api/backend/api/branches/${trip.bid}/messages`,
    (route) =>
      route.fulfill({
        status: 503,
        json: { error: { message: "条件摘要暂时读取失败" } },
      }),
  );
  await send(page, "预算2000元");
  await expect(conditions(page)).toContainText("上海");
  await expect(conditions(page)).toContainText("2,000 元");
  await expect(conditions(page)).toContainText("已推荐的城市");
  const oldResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/branches/${trip.bid}/messages`) &&
      response.status() === 200,
  );
  held.release();
  await oldResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(conditions(page)).not.toContainText("过期出发地");
  expect((await state(page, trip.sid)).versions[trip.bid]).toHaveLength(0);
});

for (const theme of ["paper", "guide", "night"]) {
  test(`路书首次读取失败原位重试，保留对话草稿且不重读历史（${theme}）`, async ({
    page,
  }, testInfo) => {
    await page.addInitScript(
      (value) => localStorage.setItem("travelflow.theme", value),
      theme,
    );
    const trip = await create(page);
    const result = await post(page, trip.sid, "北京三日游");
    let reads = 0,
      historyReads = 0,
      writes = 0;
    const held = gate();
    let waiting = false;
    page.on("request", (r) => {
      if (r.url().endsWith(`/branches/${trip.bid}/messages`)) historyReads++;
      if (r.url().includes("/api/backend/") && r.method() !== "GET") writes++;
    });
    await page.route(
      `**/api/backend/api/versions/${result.version_id}`,
      async (route) => {
        if (++reads === 1)
          return route.fulfill({
            status: 503,
            json: { error: { message: "隔离路书读取失败" } },
          });
        waiting = true;
        await held.wait;
        await route.continue();
      },
    );
    await page.goto(`/chat/${trip.sid}`);
    await expect(bookRead(page)).toContainText("隔离路书读取失败");
    await expect(bookRead(page)).toBeInViewport();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.screenshot({
      path: testInfo.outputPath("version-read-failed.png"),
      fullPage: true,
    });
    await expect(conditions(page)).toHaveCount(0);
    await expect(page.getByLabel("对话消息")).toContainText("北京三日游");
    await input(page).fill("保留尚未发送的调整");
    await bookRead(page)
      .getByRole("button", { name: "重试", exact: true })
      .click();
    await expect.poll(() => waiting).toBe(true);
    await expect(bookRead(page)).toContainText("正在读取所选路书");
    held.release();
    await expect(routePanel(page)).toBeVisible();
    await expect(bookRead(page)).toHaveCount(0);
    await expect(input(page)).toHaveValue("保留尚未发送的调整");
    expect(historyReads).toBe(1);
    expect(writes).toBe(0);
    expect((await state(page, trip.sid)).versions[trip.bid]).toHaveLength(1);
    await noOverflow(page);
  });
}

test("同版本后台读取失败和重试保留费用表单草稿", async ({ page }) => {
  const trip = await create(page);
  const result = await post(page, trip.sid, "北京三日游");
  await page.goto(`/chat/${trip.sid}`);
  await expect(routePanel(page)).toBeVisible();
  const budget = page.getByRole("region", { name: "费用与预算", exact: true });
  await budget
    .getByRole("button", { name: "添加自定义项", exact: true })
    .click();
  const form = budget.getByRole("form", {
    name: "添加自定义预算",
    exact: true,
  });
  await form.getByLabel("项目名称", { exact: true }).fill("未保存的行李寄存");
  await form.getByLabel("预估金额（元，全团）", { exact: true }).fill("30.50");
  let fail = true,
    historyReads = 0;
  page.on("request", (r) => {
    if (r.url().endsWith(`/branches/${trip.bid}/messages`)) historyReads++;
  });
  await page.route(
    `**/api/backend/api/versions/${result.version_id}`,
    (route) =>
      fail
        ? route.fulfill({
            status: 503,
            json: { error: { message: "后台刷新失败" } },
          })
        : route.continue(),
  );
  await rename(page); // 刷新同一会话快照，不改变路书版本。
  await expect(bookRead(page)).toContainText("后台刷新失败");
  await expect(routePanel(page)).toBeVisible();
  await expect(form.getByLabel("项目名称", { exact: true })).toHaveValue(
    "未保存的行李寄存",
  );
  const historyBeforeRetry = historyReads;
  fail = false;
  await bookRead(page)
    .getByRole("button", { name: "重试", exact: true })
    .click();
  await expect(bookRead(page)).toHaveCount(0);
  await expect(
    form.getByLabel("预估金额（元，全团）", { exact: true }),
  ).toHaveValue("30.50");
  expect(historyReads).toBe(historyBeforeRetry);
  expect((await state(page, trip.sid)).versions[trip.bid]).toHaveLength(1);
});

test("较早的路书响应不能覆盖后来选择的只读历史版本", async ({ page }) => {
  const trip = await create(page);
  const first = await post(page, trip.sid, "北京三日游");
  const second = await post(page, trip.sid, "第1天午餐花了86.35元");
  const held = gate();
  let waiting = false;
  await page.route(
    `**/api/backend/api/versions/${second.version_id}`,
    async (route) => {
      const response = await route.fetch();
      waiting = true;
      await held.wait;
      await route.fulfill({ response });
    },
    { times: 1 },
  );
  await page.goto(`/chat/${trip.sid}`);
  await expect.poll(() => waiting).toBe(true);
  await page.getByRole("button", { name: "完整路书", exact: true }).click();
  await page
    .locator(".journey-book ol button")
    .filter({ hasText: "v1" })
    .click();
  await expect(routePanel(page)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "从此版本分叉后编辑", exact: true }),
  ).toBeVisible();
  const response = page.waitForResponse((r) =>
    r.url().endsWith(`/versions/${second.version_id}`),
  );
  held.release();
  await response;
  await expect(
    page.getByRole("button", { name: "从此版本分叉后编辑", exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(`a[href*="${first.version_id}/export"]`).first(),
  ).toBeVisible();
  expect((await state(page, trip.sid)).versions[trip.bid]).toHaveLength(2);
});

test("分支切换后隔离旧路书响应，空分支只显示自己的条件", async ({ page }) => {
  const trip = await create(page);
  const result = await post(page, trip.sid, "北京三日游");
  const other = await (
    await page.request.post(`${proxy}/api/sessions/${trip.sid}/branches`, {
      data: { title: "独立需求" },
    })
  ).json();
  const otherBranch = other.items.find(
    (branch: { title: string; id: string }) => branch.title === "独立需求",
  );
  await post(
    page,
    trip.sid,
    "从上海出发玩三天，少走路，请推荐城市",
    otherBranch.id,
  );
  const held = gate();
  let waiting = false;
  await page.route(
    `**/api/backend/api/versions/${result.version_id}`,
    async (route) => {
      const response = await route.fetch();
      waiting = true;
      await held.wait;
      await route.fulfill({ response });
    },
  );
  await page.goto(`/chat/${trip.sid}`);
  await expect.poll(() => waiting).toBe(true);
  await page.getByRole("button", { name: "独立需求", exact: true }).click();
  await expect(conditions(page)).toContainText("还需要知道：大致预算");
  const oldResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/versions/${result.version_id}`),
  );
  held.release();
  await oldResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(routePanel(page)).toHaveCount(0);
  await expect(conditions(page)).toContainText("上海");
  await expect(conditions(page)).not.toContainText("北京");
  await expect(bookRead(page)).toHaveCount(0);
});
