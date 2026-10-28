import { expect, test, type Page } from "playwright/test";
const proxy = "/api/backend";
const input = (page: Page) =>
  page.getByRole("textbox", { name: "旅行对话输入", exact: true });
const messages = (page: Page) => page.locator('[aria-label="对话消息"]');
const history = (page: Page) =>
  page.getByRole("region", { name: "对话历史状态", exact: true });
function gate() {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { wait, release };
}
async function create(page: Page) {
  const trip = await (
    await page.request.post(`${proxy}/api/sessions`, { data: {} })
  ).json();
  return { sid: trip.session.id, bid: trip.branches[0].id };
}
async function post(page: Page, sid: string, text: string, bid?: string) {
  const response = await page.request.post(`${proxy}/api/chat`, {
    data: { session_id: sid, branch_id: bid, message: text },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}
async function recommend(page: Page) {
  const trip = await create(page);
  await post(page, trip.sid, "从上海出发玩三天，少走路，请推荐城市");
  await post(page, trip.sid, "预算2000元");
  return trip;
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
async function saved(page: Page, sid: string) {
  return (await page.request.get(`${proxy}/api/sessions/${sid}`)).json();
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

test("推荐后刷新仍可用短序号选择，越界不重排候选或产生版本", async ({
  page,
}) => {
  const trip = await recommend(page);
  await page.goto(`/chat/${trip.sid}`);
  await expect(messages(page)).toContainText("1. 北京");
  await page.reload();
  await expect(messages(page)).toContainText("1. 北京");
  await send(page, "第二个");
  await expect(messages(page)).toContainText("刚才有 1 个候选");
  expect((await saved(page, trip.sid)).versions[trip.bid]).toHaveLength(0);
  await expect(messages(page).getByText(/可以先从这几个方向选/)).toHaveCount(1);
  await send(page, "那就第一个吧");
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  const state = await saved(page, trip.sid);
  expect(state.versions[trip.bid]).toHaveLength(1);
  const detail = await (
    await page.request.get(
      `${proxy}/api/versions/${state.versions[trip.bid][0].id}`,
    )
  ).json();
  expect(detail.requirements.destination).toBe("北京");
  expect(detail.requirements.origin).toBe("上海");
  expect(detail.requirements.budget).toBe(2000);
  expect(detail.requirements.preferences).toEqual(["少走路"]);
  await noOverflow(page);
});

test("空城市推荐显示可重试的发送错误，沿用原请求恢复后可直接选城市", async ({
  page,
}) => {
  const trip = await create(page);
  await page.goto(`/chat/${trip.sid}`);
  const text =
    "空城市推荐验收" + crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  await input(page).fill(text);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/api/chat/stream"),
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect(await (await response).text()).toContain("event: error");
  const failure = page.getByRole("alert", {
    name: "消息发送未完成",
    exact: true,
  });
  await expect(failure).toContainText("没有生成可用的推荐城市");
  await expect(input(page)).toHaveValue(text);
  expect((await saved(page, trip.sid)).versions[trip.bid]).toHaveLength(0);
  const retried = page.waitForResponse((r) =>
    r.url().endsWith("/api/chat/stream"),
  );
  await failure
    .getByRole("button", { name: "重试这条消息", exact: true })
    .click();
  expect(await (await retried).text()).toContain("event: done");
  await expect(messages(page)).toContainText("1. 北京");
  await expect(failure).toHaveCount(0);
  await send(page, "北京");
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  expect((await saved(page, trip.sid)).versions[trip.bid]).toHaveLength(1);
});

test("历史读取失败原位恢复，不显示新旅行提示、不丢输入或重读路书", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("travelflow.theme", "night"),
  );
  const trip = await create(page);
  await post(page, trip.sid, "北京三日游");
  const held = gate();
  let requests = 0,
    versionReads = 0,
    writes = 0,
    waiting = false;
  page.on("request", (r) => {
    if (r.url().includes("/api/backend/") && r.method() !== "GET") writes++;
    if (/\/api\/versions\/[^/]+$/.test(r.url())) versionReads++;
  });
  await page.route(
    `**/api/backend/api/branches/${trip.bid}/messages`,
    async (route) => {
      requests++;
      if (requests === 1)
        return route.fulfill({
          status: 503,
          json: {
            error: { code: "BACKEND_UNREACHABLE", message: "隔离对话读取失败" },
          },
        });
      const response = await route.fetch();
      waiting = true;
      await held.wait;
      await route.fulfill({ response });
    },
  );
  await page.goto(`/chat/${trip.sid}`);
  await expect(history(page).getByRole("alert")).toContainText(
    "隔离对话读取失败",
  );
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  await expect(
    page.getByText("告诉我你的旅行想法即可，例如：", { exact: false }),
  ).toHaveCount(0);
  await input(page).fill("保留还没发送的调整");
  await history(page)
    .getByRole("button", { name: "重试", exact: true })
    .click();
  await expect.poll(() => waiting).toBe(true);
  await expect(history(page).getByRole("status").first()).toContainText(
    "正在读取",
  );
  await input(page).focus();
  held.release();
  await expect(history(page)).toHaveCount(0);
  await expect(messages(page)).toContainText("北京三日游");
  await expect(input(page)).toHaveValue("保留还没发送的调整");
  await expect(input(page)).toBeFocused();
  expect(versionReads).toBe(1);
  expect(writes).toBe(0);
  await noOverflow(page);
});

test("较早的历史响应不能覆盖新发送的城市选择和生成结果", async ({ page }) => {
  const trip = await recommend(page),
    held = gate();
  let waiting = false,
    calls = 0;
  await page.route(
    `**/api/backend/api/branches/${trip.bid}/messages`,
    async (route) => {
      calls++;
      if (calls > 1) return route.continue();
      const data = await (await route.fetch()).json();
      data.messages = [{ role: "assistant", content: "较早的对话快照" }];
      waiting = true;
      await held.wait;
      await route.fulfill({ json: data });
    },
  );
  await page.goto(`/chat/${trip.sid}`);
  await expect.poll(() => waiting).toBe(true);
  await expect(history(page)).toContainText("正在读取");
  await send(page, "第一个");
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  await expect(messages(page)).toContainText("第一个");
  const response = page.waitForResponse((r) =>
    r.url().endsWith(`/branches/${trip.bid}/messages`),
  );
  held.release();
  await response;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(messages(page)).not.toContainText("较早的对话快照");
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  expect((await saved(page, trip.sid)).versions[trip.bid]).toHaveLength(1);
});

test("发送后的历史刷新失败保留已显示候选与新草稿，重试不重复发送", async ({
  page,
}) => {
  const trip = await create(page);
  await post(page, trip.sid, "从上海出发玩三天，少走路，请推荐城市");
  await page.goto(`/chat/${trip.sid}`);
  await expect(messages(page)).toContainText("预算大约多少");
  let fail = true,
    writes = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/api/chat/stream")) writes++;
  });
  await page.route(
    `**/api/backend/api/branches/${trip.bid}/messages`,
    (route) =>
      fail
        ? route.fulfill({
            status: 503,
            json: {
              error: { code: "BACKEND_UNREACHABLE", message: "隔离刷新失败" },
            },
          })
        : route.continue(),
  );
  await send(page, "预算2000元");
  await expect(history(page).getByRole("alert")).toContainText("隔离刷新失败");
  await expect(messages(page)).toContainText("1. 北京");
  await expect(messages(page)).toContainText("预算大约多少");
  await input(page).fill("我的下一个想法");
  fail = false;
  await history(page)
    .getByRole("button", { name: "重试", exact: true })
    .click();
  await expect(history(page)).toHaveCount(0);
  await expect(input(page)).toHaveValue("我的下一个想法");
  await expect(
    messages(page).getByText("预算2000元", { exact: true }),
  ).toHaveCount(1);
  expect(writes).toBe(1);
});

test("切换方案后旧历史不会带回旧消息、旧路书或覆盖分支草稿", async ({
  page,
}) => {
  const trip = await create(page);
  await post(page, trip.sid, "北京甲方案");
  const branches = await (
    await page.request.post(`${proxy}/api/sessions/${trip.sid}/branches`, {
      data: { title: "乙分支", parent_branch_id: trip.bid },
    })
  ).json();
  const other = branches.items.find(
    (b: { title: string }) => b.title === "乙分支",
  );
  await post(page, trip.sid, "北京乙方案", other.id);
  const held = gate();
  let waiting = false;
  await page.route(
    `**/api/backend/api/branches/${trip.bid}/messages`,
    async (route) => {
      const data = await (await route.fetch()).json();
      data.messages.push({ role: "assistant", content: "较早甲分支快照" });
      waiting = true;
      await held.wait;
      await route.fulfill({ json: data });
    },
  );
  await page.goto(`/chat/${trip.sid}?branch=${trip.bid}`);
  await expect.poll(() => waiting).toBe(true);
  await page.getByRole("button", { name: "乙分支", exact: true }).click();
  await expect(messages(page)).toContainText("北京乙方案");
  await input(page).fill("乙分支的草稿");
  const response = page.waitForResponse((r) =>
    r.url().endsWith(`/branches/${trip.bid}/messages`),
  );
  held.release();
  await response;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(input(page)).toHaveValue("乙分支的草稿");
  await expect(messages(page)).not.toContainText("较早甲分支快照");
});

test("短选择后对话记账、修改、回顾保存结束与阅读导出构成同一旅程", async ({
  page,
}) => {
  const trip = await recommend(page);
  await page.goto(`/chat/${trip.sid}`);
  await expect(messages(page)).toContainText("1. 北京");
  await send(page, "选1");
  await send(page, "第1天第1站已完成，午餐花了86.35元");
  await send(page, "第一天轻松一点");
  const recap = page.getByRole("region", { name: "旅行回顾", exact: true });
  await recap
    .getByRole("button", { name: "打开旅行回顾", exact: true })
    .click();
  await recap
    .getByLabel("回顾标题", { exact: true })
    .fill("普通主流程验收 " + trip.sid);
  await recap
    .getByRole("textbox", { name: "我的回忆", exact: true })
    .fill("用户亲手写下的回忆 📷");
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
  const state = await saved(page, trip.sid),
    id = state.versions[trip.bid].at(-1).id;
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${id}`)
  ).json();
  expect(detail.itinerary.expenses[0].amount_cents).toBe(8635);
  expect(detail.itinerary.ended_at).toBeTruthy();
  await page.goto(`/recaps/${id}`);
  await expect(page.locator("main")).toContainText("用户亲手写下的回忆 📷");
  await expect(page.locator("main")).toContainText("86.35");
  const exported = await page.request.get(
    `${proxy}/api/versions/${id}/export?format=html`,
  );
  expect(await exported.text()).toContain("86.35");
  expect((await saved(page, trip.sid)).versions).toEqual(state.versions);
  await noOverflow(page);
});
