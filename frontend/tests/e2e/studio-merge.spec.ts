import { expect, test, type Page } from "playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import type { StudioRun } from "../../lib/studio-types";

const proxy = "/api/backend";
const select = (page: Page, id: string) =>
  page.getByLabel("当前节点", { exact: true }).selectOption(id);
const dataView = (page: Page) =>
  page.getByRole("region", { name: "结构化节点数据", exact: true });
const artifact = (run: StudioRun, id: string) =>
  run.nodes.find((n) => n.node_id === id)!.artifact!;
async function start(page: Page) {
  const session = await (
    await page.request.post(`${proxy}/api/sessions`, { data: {} })
  ).json();
  const seed = {
    session_id: session.session.id,
    branch_id: session.branches[0].id,
  };
  await page.goto(`/workflow/${seed.session_id}?branch=${seed.branch_id}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "多主题汇合规划" })
    .click();
  await page
    .getByLabel("节点输入内容", { exact: true })
    .fill("上海出发，北京三天两人，全团预算2000元，既想看人文也想逛公园。");
  return seed;
}
async function execute(page: Page, label = "运行全部"): Promise<StudioRun> {
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
async function prepare(page: Page) {
  await select(page, "document");
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/adoption") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成采用预览", exact: true }).click();
  const response = await done;
  expect(response.ok()).toBe(true);
  return response.json();
}
async function adopt(page: Page) {
  const done = page.waitForResponse((r) =>
    /\/adoption\/[^/]+\/apply$/.test(r.url()),
  );
  await page
    .getByRole("dialog", { name: "成稿采用核对", exact: true })
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  const response = await done;
  expect(response.ok()).toBe(true);
  return response.json();
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("多主题真实汇合去重，差异与来源可核对导出，调整优先级反馈可见且上游复用", async ({
  page,
}, info) => {
  const seed = await start(page);
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  expect(first.nodes).toHaveLength(10);
  const original = artifact(first, "merge").candidates!;
  expect(original.merge!.before_count).toBe(20);
  expect(original.merge!.duplicate_count).toBe(10);
  expect(
    original.places.find((p) => p.place_name === "北海公园")!.cost,
  ).toBeNull();
  await select(page, "merge");
  const report = dataView(page).getByRole("region", {
    name: "候选合并记录",
    exact: true,
  });
  await expect(report).toContainText("2路候选 · 20 → 10处");
  await report.getByText(/^北海公园 · 合并/).click();
  const northDetails = report.getByText(/^北海公园 · 合并/).locator("..");
  await expect(
    northDetails.getByText("来源：人文候选", { exact: true }),
  ).toBeVisible();
  await expect(
    northDetails.getByText("来源：户外候选", { exact: true }),
  ).toBeVisible();
  await expect(report).toContainText("¥30.00/人均");
  expect(
    await dataView(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await dataView(page).screenshot({
    path: info.outputPath("merged-candidates.png"),
  });
  const done = page.waitForEvent("download");
  await dataView(page)
    .getByRole("button", { name: "下载结构化数据 JSON", exact: true })
    .click();
  const json = JSON.parse(await readFile((await (await done).path())!, "utf8"));
  expect(json.candidates.merge).toEqual(original.merge);
  await page
    .getByRole("button", { name: "outdoors优先级上移", exact: true })
    .click();
  await expect(
    page
      .getByLabel("合并来源优先级", { exact: true })
      .getByRole("listitem")
      .first(),
  ).toContainText("1. 户外候选");
  await page
    .getByLabel("合并费用冲突策略", { exact: true })
    .selectOption("first");
  const second = await execute(page);
  expect(second.nodes.find((n) => n.node_id === "culture")!.status).toBe(
    "cached",
  );
  expect(second.nodes.find((n) => n.node_id === "outdoors")!.status).toBe(
    "cached",
  );
  expect(second.nodes.find((n) => n.node_id === "merge")!.status).toBe(
    "succeeded",
  );
  expect(
    artifact(second, "merge").candidates!.places.find(
      (p) => p.place_name === "北海公园",
    )!.cost,
  ).toBe(30);
  expect(artifact(second, "merge").candidates!.merge!.source_order).toEqual([
    "outdoors",
    "culture",
  ]);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${seed.session_id}`)
  ).json();
  expect(session.versions[seed.branch_id]).toHaveLength(0);
  await page.reload();
  await select(page, "merge");
  await expect(
    page.getByLabel("合并费用冲突策略", { exact: true }),
  ).toHaveValue("first");
  await page
    .getByRole("button", { name: "查看运行时工程", exact: true })
    .click();
  await expect(
    page.getByLabel("合并费用冲突策略", { exact: true }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});

test("合并上限保留未入池记录，成稿采用和后续重跑保护实付、人工预算与旧版本", async ({
  page,
}) => {
  await start(page);
  await select(page, "merge");
  await page.getByLabel("合并候选上限", { exact: true }).fill("3");
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  expect(artifact(first, "merge").candidates!.merge!.omitted_count).toBe(7);
  await expect(dataView(page)).toContainText("因上限未入池7组");
  expect((await prepare(page)).conflicts).toEqual([]);
  const adopted = await adopt(page);
  const north = adopted.itinerary.days
    .flatMap(
      (d: { activities: { id: string; place_name: string }[] }) => d.activities,
    )
    .find((a: { place_name: string }) => a.place_name === "北海公园");
  const budget = await page.request.put(
    `${proxy}/api/versions/${adopted.version.id}/budget-items/stay-${north.id}`,
    { data: { amount: "12.34" } },
  );
  expect(budget.ok()).toBe(true);
  const manual = await budget.json();
  const paid = await page.request.post(
    `${proxy}/api/versions/${manual.version.id}/expenses`,
    {
      data: {
        request_id: `merge-${randomUUID()}`,
        title: "门票实付",
        amount: "15",
        category: "ticket",
        activity_id: north.id,
      },
    },
  );
  expect(paid.ok()).toBe(true);
  const before = await paid.json();
  await select(page, "merge");
  await page
    .getByLabel("合并费用冲突策略", { exact: true })
    .selectOption("first");
  await execute(page);
  expect((await prepare(page)).conflicts).toEqual([]);
  const after = await adopt(page);
  expect(after.itinerary.expenses).toEqual(before.itinerary.expenses);
  expect(after.itinerary.budget_items).toEqual(before.itinerary.budget_items);
  const kept = after.itinerary.days
    .flatMap(
      (d: { activities: { id: string; cost: number }[] }) => d.activities,
    )
    .find((a: { id: string }) => a.id === north.id);
  expect(kept.cost).toBe(12.34);
  expect(
    await (
      await page.request.get(`${proxy}/api/versions/${before.version.id}`)
    ).json(),
  ).toEqual(before);
});

test("合并策略和优先级随个人模板跨旅行复用，保存时可核对，强制合并复用上游", async ({
  page,
}) => {
  await start(page);
  await select(page, "merge");
  await page
    .getByRole("button", { name: "outdoors优先级上移", exact: true })
    .click();
  await page
    .getByLabel("合并费用冲突策略", { exact: true })
    .selectOption("first");
  await page
    .getByRole("button", { name: "保存为我的模板", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "保存个人工程模板",
    exact: true,
  });
  const name = `主题汇合-${randomUUID()}`;
  await dialog.getByLabel("个人模板名称", { exact: true }).fill(name);
  await dialog.getByText("核对节点指令与参数", { exact: true }).click();
  await expect(dialog).toContainText("费用冲突：沿用优先来源");
  await expect(dialog).toContainText("户外候选 → 人文候选");
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith("/workflow/user-templates") &&
      r.request().method() === "POST",
  );
  await dialog
    .getByRole("button", { name: "保存个人模板", exact: true })
    .click();
  expect((await saved).ok()).toBe(true);
  await expect(dialog).toHaveCount(0);
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${seed.session_id}?branch=${seed.branch_id}`);
  await page
    .getByRole("article", { name: `个人模板 ${name}`, exact: true })
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  const result = await execute(page);
  expect(result.status).toBe("succeeded");
  await select(page, "merge");
  await expect(
    page.getByLabel("合并费用冲突策略", { exact: true }),
  ).toHaveValue("first");
  const forced = await execute(page, "强制重跑此节点");
  expect(forced.nodes.find((n) => n.node_id === "merge")!.status).toBe(
    "succeeded",
  );
  expect(forced.nodes.find((n) => n.node_id === "culture")!.status).toBe(
    "cached",
  );
  expect(forced.nodes.find((n) => n.node_id === "outdoors")!.status).toBe(
    "cached",
  );
});

test("候选合并拒绝错类型和重复连线，保留可执行的工程", async ({ page }) => {
  await start(page);
  await page.getByText(/^连线列表与键盘连接/).click();
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("research:sources");
  await page
    .getByLabel("连线输入", { exact: true })
    .selectOption("merge:candidates");
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "端口不兼容" })).toBeVisible();
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("culture:candidates");
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "已经连接" })).toBeVisible();
  expect((await execute(page)).status).toBe("succeeded");
});
