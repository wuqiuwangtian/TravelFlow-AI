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
async function mode(page: Page, bid: string, state: string) {
  expect(
    (await page.request.post(`${proxy}/test/geo/${bid}/${state}`)).ok(),
  ).toBe(true);
}
async function calls(page: Page, bid: string) {
  return (await page.request.get(`${proxy}/test/geo/${bid}`)).json();
}
async function start(page: Page, state: string | null = "complete") {
  const session = await (
    await page.request.post(`${proxy}/api/sessions`, { data: {} })
  ).json();
  const seed = {
    session_id: session.session.id,
    branch_id: session.branches[0].id,
  };
  if (state) await mode(page, seed.branch_id, state);
  await page.goto(`/workflow/${seed.session_id}?branch=${seed.branch_id}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "按区域分日规划" })
    .click();
  await page
    .getByLabel("节点输入内容", { exact: true })
    .fill("上海出发，北京三天两人，全团预算2000元，按区域安排景点。");
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

test("候选定位供分日及路线复用，按区域减少分散，参数刷新快照与结构化导出保留", async ({
  page,
}, info) => {
  const seed = await start(page);
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  expect(first.nodes).toHaveLength(8);
  const geography = artifact(first, "allocation").plan!.allocation!.geography!;
  expect(geography.applied).toBe(true);
  expect(geography.after_spread_meters!).toBeLessThan(
    geography.before_spread_meters! / 5,
  );
  expect((await calls(page, seed.branch_id)).search).toHaveLength(10);
  await select(page, "location");
  await expect(
    dataView(page).getByLabel("候选定位汇总", { exact: true }),
  ).toContainText("已定位10/10处");
  await expect(dataView(page)).toContainText("北京模拟区域");
  await dataView(page).screenshot({
    path: info.outputPath("located-candidates.png"),
  });
  await select(page, "allocation");
  await expect(
    dataView(page).getByLabel("地理分组记录", { exact: true }),
  ).toContainText("已按位置调整");
  await expect(dataView(page)).toContainText("非实际路程");
  expect(
    await dataView(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await dataView(page).screenshot({
    path: info.outputPath("geographic-groups.png"),
  });
  const download = page.waitForEvent("download");
  await dataView(page)
    .getByRole("button", { name: "下载结构化数据 JSON", exact: true })
    .click();
  const json = JSON.parse(
    await readFile((await (await download).path())!, "utf8"),
  );
  expect(json.plan.allocation.geography).toEqual(geography);
  await page
    .getByLabel("分日安排节奏", { exact: true })
    .selectOption("relaxed");
  const second = await execute(page);
  expect(second.nodes.find((n) => n.node_id === "location")!.status).toBe(
    "cached",
  );
  expect(artifact(second, "allocation").plan!.allocation!.selected_count).toBe(
    6,
  );
  expect((await calls(page, seed.branch_id)).search).toHaveLength(10);
  await page.reload();
  await select(page, "allocation");
  await expect(page.getByLabel("分日分组策略", { exact: true })).toHaveValue(
    "nearby",
  );
  await page
    .getByRole("button", { name: "查看运行时工程", exact: true })
    .click();
  await expect(page.getByLabel("分日分组策略", { exact: true })).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});

test("同名歧义可指定地图候选，失效指定阻止悄悄替换，清除后可重新定位", async ({
  page,
}) => {
  const seed = await start(page, "ambiguous");
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  expect(
    artifact(first, "allocation").plan!.allocation!.geography!.applied,
  ).toBe(false);
  await select(page, "location");
  await expect(dataView(page)).toContainText("部分地点待明确");
  await page
    .getByRole("button", { name: "北海公园选用 北京西侧分园", exact: true })
    .click();
  const second = await execute(page);
  expect(second.status).toBe("succeeded");
  const north = artifact(second, "route")
    .plan!.days.flatMap((d) => d.activities)
    .find((a) => a.place_name === "北海公园")!;
  expect(north.place!.id).toBe("north-west");
  await expect(dataView(page)).toContainText("候选定位完成");
  await mode(page, seed.branch_id, "stale");
  const stale = await execute(page);
  expect(stale.status).toBe("failed");
  expect(stale.nodes.find((n) => n.node_id === "allocation")!.error).toContain(
    "指定地图地点未能确认",
  );
  await expect(dataView(page)).toContainText("已不在本次地图结果");
  await page
    .getByRole("button", { name: "北海公园清除指定", exact: true })
    .click();
  const repaired = await execute(page);
  expect(repaired.status).toBe("succeeded");
  expect(
    artifact(repaired, "location").candidates!.places.find(
      (p) => p.place_name === "北海公园",
    )!.location!.place!.id,
  ).toBe("north-new");
});

test("地图未配置及单项失败保留完整时长方案，分组规则可保存为个人模板", async ({
  page,
}) => {
  await start(page, null);
  const fallback = await execute(page);
  expect(fallback.status).toBe("succeeded");
  expect(artifact(fallback, "allocation").plan!.days).toHaveLength(3);
  expect(
    artifact(fallback, "allocation").plan!.allocation!.geography!.applied,
  ).toBe(false);
  await select(page, "location");
  await expect(dataView(page)).toContainText("地图未配置");
  await select(page, "allocation");
  await page
    .getByRole("button", { name: "保存为我的模板", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "保存个人工程模板",
    exact: true,
  });
  const name = `地理分日-${randomUUID()}`;
  await dialog.getByLabel("个人模板名称", { exact: true }).fill(name);
  await dialog.getByText("核对节点指令与参数", { exact: true }).click();
  await expect(dialog).toContainText("分日依据：按确认位置分组");
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
  await mode(page, seed.branch_id, "unavailable");
  await page.goto(`/workflow/${seed.session_id}?branch=${seed.branch_id}`);
  await page
    .getByRole("article", { name: `个人模板 ${name}`, exact: true })
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  const result = await execute(page);
  expect(result.status).toBe("succeeded");
  expect(artifact(result, "location").candidates!.location!.status).toBe(
    "partial",
  );
  expect(
    artifact(result, "allocation").plan!.allocation!.geography!.applied,
  ).toBe(false);
  expect(
    (await calls(page, seed.branch_id)).search.length,
  ).toBeGreaterThanOrEqual(10);
});

test("指定地点在正式采用后保留，后续采用保护人工费用、实际消费及原版本", async ({
  page,
}) => {
  const seed = await start(page, "ambiguous");
  await execute(page);
  await select(page, "location");
  await page
    .getByRole("button", { name: "北海公园选用 北京西侧分园", exact: true })
    .click();
  await execute(page);
  const count = (await calls(page, seed.branch_id)).search.length;
  const preview = await prepare(page);
  expect(preview.conflicts).toEqual([]);
  const first = await adopt(page);
  const north = first.itinerary.days
    .flatMap(
      (d: {
        activities: { id: string; place_name: string; place: { id: string } }[];
      }) => d.activities,
    )
    .find((a: { place_name: string }) => a.place_name === "北海公园");
  expect(north.place.id).toBe("north-west");
  expect((await calls(page, seed.branch_id)).search).toHaveLength(count);
  const budget = await page.request.put(
    `${proxy}/api/versions/${first.version.id}/budget-items/stay-${north.id}`,
    { data: { amount: "12.34" } },
  );
  expect(budget.ok()).toBe(true);
  const manual = await budget.json();
  const paid = await page.request.post(
    `${proxy}/api/versions/${manual.version.id}/expenses`,
    {
      data: {
        request_id: `geo-${randomUUID()}`,
        title: "门票实付",
        amount: "15",
        category: "ticket",
        activity_id: north.id,
      },
    },
  );
  expect(paid.ok()).toBe(true);
  const before = await paid.json();
  await execute(page);
  expect((await prepare(page)).conflicts).toEqual([]);
  const second = await adopt(page);
  expect(second.itinerary.expenses).toEqual(before.itinerary.expenses);
  expect(second.itinerary.budget_items).toEqual(before.itinerary.budget_items);
  const actual = second.itinerary.days
    .flatMap(
      (d: {
        activities: { id: string; cost: number; place: { id: string } }[];
      }) => d.activities,
    )
    .find((a: { id: string }) => a.id === north.id);
  expect(actual.cost).toBe(12.34);
  expect(actual.place.id).toBe("north-west");
  expect(
    await (
      await page.request.get(`${proxy}/api/versions/${before.version.id}`)
    ).json(),
  ).toEqual(before);
});
