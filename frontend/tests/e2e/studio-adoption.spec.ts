import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const adoption = (page: Page) =>
  page.getByRole("region", { name: "工程成稿采用", exact: true });
const review = (page: Page) =>
  page.getByRole("dialog", { name: "成稿采用核对", exact: true });
async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[bid];
  return {
    versions,
    detail: versions.length
      ? await (
          await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
        ).json()
      : null,
  };
}
async function start(page: Page, seedPath = "/test/moves") {
  const response = await page.request.post(`${proxy}${seedPath}`, { data: {} });
  expect(response.ok()).toBe(true);
  const seed = await response.json();
  const sid = seed.session_id ?? seed.session.id,
    bid = seed.branch_id ?? seed.branches[0].id;
  await page.goto(`/workflow/${sid}?branch=${bid}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "完整行程" })
    .click();
  const input = page.getByLabel("节点输入内容", { exact: true });
  await input.fill(
    "上海出发，北京三天，两人，全团预算2000元，生成每天具体安排。",
  );
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/studio/runs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "运行全部", exact: true }).click();
  expect((await (await done).json()).status).toBe("succeeded");
  await page.getByLabel("当前节点", { exact: true }).selectOption("document");
  await expect(
    adoption(page).getByRole("button", { name: "生成采用预览", exact: true }),
  ).toBeEnabled();
  return { sid, bid };
}
async function generate(page: Page) {
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/adoption") && r.request().method() === "POST",
  );
  await adoption(page)
    .getByRole("button", { name: "生成采用预览", exact: true })
    .click();
  return done;
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("工程成稿先核对，预览刷新恢复，采用回复丢失重试不重复且原记录与历史保留", async ({
  page,
}, info) => {
  const { sid, bid } = await start(page);
  const before = await latest(page, sid, bid);
  expect((await generate(page)).ok()).toBe(true);
  await expect(review(page)).toBeVisible();
  await expect(review(page)).toContainText("保留1笔消费");
  await expect(review(page)).toContainText("保护保留");
  await expect(review(page)).toContainText("新增");
  await expect(
    review(page).getByRole("button", {
      name: "确认采用到正式行程",
      exact: true,
    }),
  ).toBeEnabled();
  expect(await latest(page, sid, bid)).toEqual(before);
  expect(
    await review(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await review(page).screenshot({
    path: info.outputPath("studio-adoption-preview.png"),
  });
  await review(page)
    .getByRole("button", { name: "关闭采用核对", exact: true })
    .click();
  await page.reload();
  await page.getByLabel("当前节点", { exact: true }).selectOption("document");
  await adoption(page)
    .getByRole("button", { name: "查看采用预览", exact: true })
    .click();
  await expect(review(page)).toBeVisible();
  let dropped = false;
  await page.route("**/adoption/*/apply", async (route) => {
    if (!dropped) {
      dropped = true;
      expect((await route.fetch()).ok()).toBe(true);
      await route.abort();
    } else await route.continue();
  });
  await review(page)
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  await expect(review(page).getByRole("alert")).toContainText(
    "网络连接暂时中断",
  );
  await review(page)
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  await expect(adoption(page).getByRole("status")).toContainText(
    "成稿已采用为正式行程第2版",
  );
  const after = await latest(page, sid, bid);
  expect(after.versions).toHaveLength(before.versions.length + 1);
  expect(after.detail.itinerary.expenses).toEqual(
    before.detail.itinerary.expenses,
  );
  expect(after.detail.itinerary.budget_items).toEqual(
    before.detail.itinerary.budget_items,
  );
  const park = after.detail.itinerary.days[1].activities.find(
    (a: { id: string }) => a.id === "park",
  );
  expect(park).toEqual(before.detail.itinerary.days[1].activities[0]);
  expect(
    after.detail.itinerary.days[2].activities.some(
      (a: { place_name: string }) => a.place_name === "天坛公园",
    ),
  ).toBe(true);
  expect(
    await (
      await page.request.get(
        `${proxy}/api/versions/${before.detail.version.id}`,
      )
    ).json(),
  ).toEqual(before.detail);
  expect(
    await (
      await page.request.get(
        `${proxy}/api/versions/${after.detail.version.id}/export?format=md`,
      )
    ).text(),
  ).toContain("天坛公园");
  await adoption(page)
    .getByRole("link", { name: "查看正式行程", exact: true })
    .click();
  await expect(page).toHaveURL(`/chat/${sid}?branch=${bid}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
});

test("其他窗口新记账后拒绝旧采用预览，重载后仍可保留新消费并采用", async ({
  page,
}) => {
  const { sid, bid } = await start(page);
  expect((await generate(page)).ok()).toBe(true);
  const payment = await page.request.post(`${proxy}/api/chat`, {
    data: { session_id: sid, branch_id: bid, message: "第3天午餐花了38元" },
  });
  expect(payment.ok()).toBe(true);
  const newest = await latest(page, sid, bid);
  await review(page)
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  await expect(review(page).getByRole("alert")).toContainText(
    "正式方案或对话已变化",
  );
  expect(await latest(page, sid, bid)).toEqual(newest);
  await review(page)
    .getByRole("button", { name: "载入最新采用信息", exact: true })
    .click();
  await expect(review(page)).toHaveCount(0);
  expect((await generate(page)).ok()).toBe(true);
  await expect(review(page)).toContainText("保留2笔消费");
  await review(page)
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  await expect(adoption(page).getByRole("status")).toContainText("成稿已采用");
  expect((await latest(page, sid, bid)).detail.itinerary.expenses).toEqual(
    newest.detail.itinerary.expenses,
  );
});

test("预约冲突集中展示并阻止采用，丢弃预览不改变正式行程", async ({ page }) => {
  const { sid, bid } = await start(page, "/test/linked?early_booking=true");
  const before = await latest(page, sid, bid);
  expect((await generate(page)).ok()).toBe(true);
  await expect(review(page).getByRole("alert")).toContainText(
    "存在冲突，暂不能采用",
  );
  await expect(review(page)).toContainText("预约或人工固定时刻保留");
  await expect(
    review(page).getByRole("button", {
      name: "确认采用到正式行程",
      exact: true,
    }),
  ).toBeDisabled();
  await review(page)
    .getByRole("button", { name: "丢弃采用预览", exact: true })
    .click();
  await expect(review(page)).toHaveCount(0);
  expect(await latest(page, sid, bid)).toEqual(before);
  await page.reload();
  await page.getByLabel("当前节点", { exact: true }).selectOption("document");
  await expect(
    adoption(page).getByRole("button", { name: "生成采用预览", exact: true }),
  ).toBeEnabled();
  await expect(
    adoption(page).getByRole("button", { name: "查看采用预览", exact: true }),
  ).toHaveCount(0);
});

test("没有原路书也可从工程创建首版，整理失败重试后生成并采用完整行程", async ({
  page,
}) => {
  const { sid, bid } = await start(page, "/api/sessions");
  expect((await latest(page, sid, bid)).versions).toHaveLength(0);
  let failed = false;
  await page.route("**/adoption", async (route) => {
    if (!failed && route.request().method() === "POST") {
      failed = true;
      await route.fulfill({
        status: 502,
        json: {
          error: {
            code: "LLM_AUTH_FAILED",
            message: "模型鉴权失败，请检查设置",
          },
        },
      });
    } else await route.continue();
  });
  expect((await generate(page)).status()).toBe(502);
  await expect(adoption(page).getByRole("alert")).toContainText("模型鉴权失败");
  expect((await latest(page, sid, bid)).versions).toHaveLength(0);
  expect((await generate(page)).ok()).toBe(true);
  await expect(review(page)).toContainText("暂无原预算");
  await review(page)
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  await expect(adoption(page).getByRole("status")).toContainText(
    "正式行程第1版",
  );
  const after = await latest(page, sid, bid);
  expect(after.versions).toHaveLength(1);
  expect(after.detail.itinerary.days).toHaveLength(3);
});

test("城市推荐等非每日行程成稿不能被误采用成正式计划", async ({ page }) => {
  const created = await (
    await page.request.post(`${proxy}/api/sessions`, {
      data: { title: "灵感提案采用边界" },
    })
  ).json();
  const sid = created.session.id,
    bid = created.branches[0].id;
  const graph = {
    schema_version: 1,
    title: "城市灵感",
    nodes: [
      {
        id: "brief",
        kind: "input",
        title: "城市推荐",
        x: 20,
        y: 20,
        parameters: {
          text: "测试无每日安排：只推荐城市，没有具体行程。",
          prompt: "",
          temperature: 0.5,
          max_results: 5,
        },
      },
      {
        id: "document",
        kind: "output",
        title: "城市灵感提案",
        x: 320,
        y: 20,
        parameters: { text: "", prompt: "", temperature: 0.5, max_results: 5 },
      },
    ],
    edges: [
      {
        source: "brief",
        target: "document",
        source_port: "text",
        target_port: "content",
      },
    ],
  };
  expect(
    (
      await page.request.put(`${proxy}/api/branches/${bid}/studio`, {
        data: { graph, expected_revision: 0 },
      })
    ).ok(),
  ).toBe(true);
  expect(
    (
      await page.request.post(`${proxy}/api/branches/${bid}/studio/runs`, {
        data: {
          request_id: `idea-${bid}`,
          expected_revision: 1,
          target_node: null,
          force: false,
        },
      })
    ).ok(),
  ).toBe(true);
  await page.goto(`/workflow/${sid}?branch=${bid}`);
  await page.getByLabel("当前节点", { exact: true }).selectOption("document");
  expect((await generate(page)).status()).toBe(400);
  await expect(adoption(page).getByRole("alert")).toContainText(
    "成稿尚不能整理为唯一的每日行程",
  );
  await expect(review(page)).toHaveCount(0);
  expect((await latest(page, sid, bid)).versions).toHaveLength(0);
});
