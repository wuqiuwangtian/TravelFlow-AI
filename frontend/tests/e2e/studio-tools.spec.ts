import { expect, test, type Page } from "playwright/test";
import { readFile } from "node:fs/promises";

const proxy = "/api/backend";
const inspector = (page: Page) =>
  page.getByRole("complementary", { name: "节点参数与产物", exact: true });
const dataView = (page: Page) =>
  page.getByRole("region", { name: "结构化节点数据", exact: true });
async function start(page: Page) {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${seed.session_id}?branch=${seed.branch_id}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "结构化行程" })
    .click();
  return seed;
}
async function execute(page: Page, label = "运行全部") {
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/studio/runs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: label, exact: true }).click();
  const response = await done;
  expect(response.ok()).toBe(true);
  await expect(
    page.getByRole("button", { name: "运行全部", exact: true }),
  ).toBeEnabled();
  return response.json();
}
async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[bid];
  return {
    versions,
    detail: await (
      await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
    ).json(),
  };
}
const select = (page: Page, node: string) =>
  inspector(page).getByLabel("当前节点", { exact: true }).selectOption(node);
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("结构化模板展示每日数据和预算，导出可追溯，路线局部重跑只更新依赖", async ({
  page,
}, info) => {
  const seed = await start(page);
  const before = await latest(page, seed.session_id, seed.branch_id);
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  await select(page, "route");
  await expect(dataView(page)).toContainText("地图未配置");
  await expect(dataView(page)).toContainText("3天 · 4个活动");
  await dataView(page)
    .getByText("第3天 · 新的北京安排", { exact: true })
    .click();
  await expect(dataView(page)).toContainText("天坛公园");
  await expect(dataView(page)).toContainText("未查询地图");
  await select(page, "budget");
  await expect(dataView(page)).toContainText("¥1079.00");
  await expect(dataView(page)).toContainText("未知费用 3项");
  await expect(dataView(page)).toContainText("尚余 ¥921.00");
  expect(
    await dataView(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await dataView(page).screenshot({
    path: info.outputPath("structured-budget.png"),
  });
  const downloaded = page.waitForEvent("download");
  await dataView(page)
    .getByRole("button", { name: "下载结构化数据 JSON", exact: true })
    .click();
  const file = await downloaded;
  const output = JSON.parse(await readFile((await file.path())!, "utf8"));
  expect(output.plan).toBeNull();
  expect(output.budget.categories.ticket).toBe(107900);
  expect(
    output.budget.overview.rows.every(
      (row: { expense_ids: string[] }) => row.expense_ids.length === 0,
    ),
  ).toBe(true);
  await select(page, "route");
  await page
    .getByLabel("路线默认交通", { exact: true })
    .selectOption("walking");
  const partial = await execute(page, "运行到此节点");
  expect(
    partial.nodes.map((node: { node_id: string }) => node.node_id),
  ).not.toContain("budget");
  expect(
    partial.nodes.find((node: { node_id: string }) => node.node_id === "plan")
      .status,
  ).toBe("cached");
  expect(
    partial.nodes.find((node: { node_id: string }) => node.node_id === "route")
      .status,
  ).toBe("succeeded");
  const next = await execute(page);
  expect(
    next.nodes.find((node: { node_id: string }) => node.node_id === "route")
      .status,
  ).toBe("cached");
  expect(
    next.nodes.find((node: { node_id: string }) => node.node_id === "budget")
      .status,
  ).toBe("succeeded");
  expect(await latest(page, seed.session_id, seed.branch_id)).toEqual(before);
  await page.reload();
  await select(page, "route");
  await expect(page.getByLabel("路线默认交通", { exact: true })).toHaveValue(
    "walking",
  );
  await expect(dataView(page)).toContainText("地图未配置");
});

test("结构化成稿可预览采用，原消费和人工费用保留，导出及后续对话沿用新行程", async ({
  page,
}) => {
  const seed = await start(page);
  const before = await latest(page, seed.session_id, seed.branch_id);
  await execute(page);
  await select(page, "document");
  await expect(dataView(page)).toContainText("结构化数据");
  const prepared = page.waitForResponse(
    (r) => r.url().endsWith("/adoption") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成采用预览", exact: true }).click();
  const response = await prepared;
  expect(response.ok()).toBe(true);
  const preview = await response.json();
  expect(preview.conflicts).toEqual([]);
  const dialog = page.getByRole("dialog", {
    name: "成稿采用核对",
    exact: true,
  });
  await expect(dialog).toContainText("保留1笔消费");
  expect(await latest(page, seed.session_id, seed.branch_id)).toEqual(before);
  const done = page.waitForResponse((r) =>
    /\/adoption\/[^/]+\/apply$/.test(r.url()),
  );
  await dialog
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  expect((await done).ok()).toBe(true);
  const after = await latest(page, seed.session_id, seed.branch_id);
  expect(after.versions).toHaveLength(before.versions.length + 1);
  expect(after.detail.itinerary.expenses).toEqual(
    before.detail.itinerary.expenses,
  );
  expect(after.detail.itinerary.budget_items).toEqual(
    before.detail.itinerary.budget_items,
  );
  const activities = after.detail.itinerary.days.flatMap(
    (d: { activities: { id: string; cost: number }[] }) => d.activities,
  );
  expect(activities.find((a: { id: string }) => a.id === "park").cost).toBe(20);
  expect(after.detail.markdown).toContain("天坛公园");
  const exported = await page.request.get(
    `${proxy}/api/versions/${after.detail.version.id}/export?format=md`,
  );
  expect(exported.ok()).toBe(true);
  expect(await exported.text()).toContain("天坛公园");
  await page.getByRole("link", { name: "查看正式行程", exact: true }).click();
  await expect(page).toHaveURL(
    new RegExp(`/chat/${seed.session_id}\\?branch=${seed.branch_id}`),
  );
  await page.getByRole("button", { name: "第 3 天", exact: true }).click();
  await expect(
    page.getByText("天坛公园", { exact: true }).first(),
  ).toBeVisible();
  const payment = await page.request.post(`${proxy}/api/chat`, {
    data: {
      session_id: seed.session_id,
      branch_id: seed.branch_id,
      message: "第3天午餐花了38元",
    },
  });
  expect(payment.ok()).toBe(true);
  const continued = await latest(page, seed.session_id, seed.branch_id);
  expect(continued.detail.itinerary.expenses).toHaveLength(
    before.detail.itinerary.expenses.length + 1,
  );
  expect(continued.detail.markdown).toContain("天坛公园");
});

test("类型端口拦截错误连接，来自不同路线的预算不能汇入同一成稿", async ({
  page,
}) => {
  await start(page);
  await execute(page);
  await page.getByText("连线列表与键盘连接 · 7 条", { exact: true }).click();
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("research:sources");
  await page
    .getByLabel("连线输入", { exact: true })
    .selectOption("budget:itinerary");
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "节点工程", exact: true })
      .getByRole("alert"),
  ).toContainText("端口不兼容");
  await page.getByRole("button", { name: "移除连线 5", exact: true }).click();
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("plan:itinerary");
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  const mismatched = await execute(page);
  expect(mismatched.status).toBe("failed");
  await select(page, "document");
  await expect(inspector(page)).toContainText("预算对应另一份行程");
  await expect(
    page.getByRole("button", { name: "生成采用预览", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "移除连线 7", exact: true }).click();
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("route:itinerary");
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  expect((await execute(page)).status).toBe("succeeded");
  await expect(
    page.getByRole("button", { name: "生成采用预览", exact: true }),
  ).toBeEnabled();
});

test("无每日安排时结构化节点失败并阻塞下游，修改需求后可继续运行", async ({
  page,
}) => {
  await start(page);
  await page
    .getByLabel("节点输入内容", { exact: true })
    .fill("测试无每日安排，只推荐城市");
  const failed = await execute(page);
  expect(failed.status).toBe("failed");
  await select(page, "plan");
  await expect(inspector(page)).toContainText("尚未形成唯一明确的每日安排");
  await select(page, "document");
  await expect(inspector(page)).toContainText("上游节点失败");
  await select(page, "brief");
  await page
    .getByLabel("节点输入内容", { exact: true })
    .fill("上海出发，北京三天，两人，全团预算2000元，需要完整每日安排。");
  const retried = await execute(page);
  expect(retried.status).toBe("succeeded");
  await select(page, "plan");
  await expect(dataView(page)).toContainText("3天 · 4个活动");
});
