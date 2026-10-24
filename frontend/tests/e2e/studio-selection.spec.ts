import { expect, test, type Page } from "playwright/test";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";

const proxy = "/api/backend";
const inspector = (page: Page) =>
  page.getByRole("complementary", { name: "节点参数与产物", exact: true });
const dataView = (page: Page) =>
  page.getByRole("region", { name: "结构化节点数据", exact: true });
const select = (page: Page, id: string) =>
  page.getByLabel("当前节点", { exact: true }).selectOption(id);
async function start(page: Page) {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${seed.session_id}?branch=${seed.branch_id}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "筛选与优化" })
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
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
  ).json();
  return { versions, detail };
}
async function configure(page: Page) {
  await select(page, "selection");
  await page.getByLabel("每日景点上限", { exact: true }).selectOption("1");
  await page.getByLabel("必去景点名单", { exact: true }).fill("北海");
  await page.getByLabel("景点建议停留", { exact: true }).selectOption("120");
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("筛选取舍与停留参数真实改变日程和预算，上游复用，数据及快照可核对", async ({
  page,
}, info) => {
  const seed = await start(page);
  const before = await latest(page, seed.session_id, seed.branch_id);
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  expect(first.nodes).toHaveLength(7);
  await configure(page);
  const second = await execute(page);
  expect(second.status).toBe("succeeded");
  expect(
    second.nodes.find((n: { node_id: string }) => n.node_id === "plan").status,
  ).toBe("cached");
  expect(
    second.nodes.find((n: { node_id: string }) => n.node_id === "selection")
      .status,
  ).toBe("succeeded");
  await expect(dataView(page)).toContainText("4 → 3个活动");
  await expect(
    dataView(page).getByLabel("景点筛选记录", { exact: true }),
  ).toContainText("移除 · 天坛公园");
  await expect(dataView(page)).toContainText("保留 · 北海公园");
  await expect(dataView(page)).toContainText("120分钟");
  expect(
    await dataView(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await dataView(page).screenshot({
    path: info.outputPath("selection-decisions.png"),
  });
  const done = page.waitForEvent("download");
  await dataView(page)
    .getByRole("button", { name: "下载结构化数据 JSON", exact: true })
    .click();
  const downloaded = await done;
  const json = JSON.parse(await readFile((await downloaded.path())!, "utf8"));
  expect(json.plan.selection.after_count).toBe(3);
  expect(json.plan.days[2].activities[0].place_name).toBe("北海公园");
  expect(json.plan.days[2].activities[0].duration_minutes).toBe(120);
  await select(page, "route");
  await expect(page.getByLabel("路线顺序策略", { exact: true })).toHaveValue(
    "nearby",
  );
  await expect(
    dataView(page).getByLabel("路线顺序核对", { exact: true }),
  ).toContainText("沿用上游");
  await expect(dataView(page)).toContainText("地图未配置");
  await select(page, "budget");
  await expect(dataView(page)).toContainText("¥999.00");
  await expect(dataView(page)).toContainText("未知费用 2项");
  expect(await latest(page, seed.session_id, seed.branch_id)).toEqual(before);
  await page.reload();
  await select(page, "selection");
  await expect(page.getByLabel("每日景点上限", { exact: true })).toHaveValue(
    "1",
  );
  await expect(page.getByLabel("必去景点名单", { exact: true })).toHaveValue(
    "北海",
  );
  await expect(page.getByLabel("景点建议停留", { exact: true })).toHaveValue(
    "120",
  );
  await page
    .getByRole("button", { name: "查看运行时工程", exact: true })
    .click();
  await expect(page.getByLabel("每日景点上限", { exact: true })).toBeDisabled();
});

test("必去缺失、规则矛盾和空白日清楚报错，修正后复用上游继续", async ({
  page,
}) => {
  const seed = await start(page);
  const before = await latest(page, seed.session_id, seed.branch_id);
  await execute(page);
  await select(page, "selection");
  await page.getByLabel("必去景点名单", { exact: true }).fill("不存在的景点");
  const missing = await execute(page, "运行到此节点");
  expect(missing.status).toBe("failed");
  await expect(inspector(page)).toContainText("上游没有找到必去景点");
  await page.getByLabel("必去景点名单", { exact: true }).fill("北海");
  await page.getByLabel("排除景点名单", { exact: true }).fill("北海");
  expect((await execute(page, "运行到此节点")).status).toBe("failed");
  await expect(inspector(page)).toContainText("命中了同一景点");
  await page.getByLabel("必去景点名单", { exact: true }).fill("");
  await page.getByLabel("排除景点名单", { exact: true }).fill("天坛、北海");
  expect((await execute(page, "运行到此节点")).status).toBe("failed");
  await expect(inspector(page)).toContainText("第3天筛选后没有活动");
  await page.getByLabel("必去景点名单", { exact: true }).fill("北海");
  await page.getByLabel("排除景点名单", { exact: true }).fill("天坛");
  const repaired = await execute(page);
  expect(repaired.status).toBe("succeeded");
  expect(
    repaired.nodes.find((n: { node_id: string }) => n.node_id === "plan")
      .status,
  ).toBe("cached");
  await expect(dataView(page)).toContainText("命中排除名单");
  expect(await latest(page, seed.session_id, seed.branch_id)).toEqual(before);
});

test("筛选及路线参数可保存为个人模板并在另一趟旅行复用", async ({ page }) => {
  await start(page);
  await configure(page);
  await page.getByLabel("排除景点名单", { exact: true }).fill("天坛");
  await select(page, "route");
  await page
    .getByLabel("路线默认交通", { exact: true })
    .selectOption("walking");
  await page
    .getByRole("button", { name: "保存为我的模板", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "保存个人工程模板",
    exact: true,
  });
  const name = `筛选慢游-${randomUUID()}`;
  await dialog.getByLabel("个人模板名称", { exact: true }).fill(name);
  await dialog.getByText("核对节点指令与参数", { exact: true }).click();
  await expect(dialog).toContainText("必去：北海");
  await expect(dialog).toContainText("排除：天坛");
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
  const target = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${target.session_id}?branch=${target.branch_id}`);
  await page
    .getByRole("article", { name: `个人模板 ${name}`, exact: true })
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  await select(page, "selection");
  await expect(page.getByLabel("每日景点上限", { exact: true })).toHaveValue(
    "1",
  );
  await expect(page.getByLabel("必去景点名单", { exact: true })).toHaveValue(
    "北海",
  );
  await expect(page.getByLabel("排除景点名单", { exact: true })).toHaveValue(
    "天坛",
  );
  await expect(page.getByLabel("景点建议停留", { exact: true })).toHaveValue(
    "120",
  );
  await select(page, "route");
  await expect(page.getByLabel("路线默认交通", { exact: true })).toHaveValue(
    "walking",
  );
  await expect(page.getByLabel("路线顺序策略", { exact: true })).toHaveValue(
    "nearby",
  );
  expect((await execute(page)).status).toBe("succeeded");
});

test("筛选成稿采用继续保护已记消费、人工预算和原版本", async ({ page }) => {
  const seed = await start(page);
  const before = await latest(page, seed.session_id, seed.branch_id);
  await configure(page);
  await execute(page);
  await select(page, "document");
  const prepared = page.waitForResponse(
    (r) => r.url().endsWith("/adoption") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成采用预览", exact: true }).click();
  const response = await prepared;
  expect(response.ok()).toBe(true);
  const preview = await response.json();
  expect(preview.conflicts).toEqual([]);
  expect(
    preview.itinerary.days[2].activities.map(
      (a: { place_name: string }) => a.place_name,
    ),
  ).toEqual(["北海公园"]);
  const dialog = page.getByRole("dialog", {
    name: "成稿采用核对",
    exact: true,
  });
  await expect(dialog).toContainText("保留1笔消费");
  const done = page.waitForResponse((r) =>
    /\/adoption\/[^/]+\/apply$/.test(r.url()),
  );
  await dialog
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  expect((await done).ok()).toBe(true);
  const after = await latest(page, seed.session_id, seed.branch_id);
  expect(after.detail.markdown).not.toContain("999");
  expect(after.detail.markdown).not.toContain("## 景点筛选记录");
  expect(after.versions).toHaveLength(before.versions.length + 1);
  expect(after.detail.itinerary.expenses).toEqual(
    before.detail.itinerary.expenses,
  );
  expect(after.detail.itinerary.budget_items).toEqual(
    before.detail.itinerary.budget_items,
  );
  expect(
    after.detail.itinerary.days[1].activities.find(
      (a: { id: string }) => a.id === "park",
    ).cost,
  ).toBe(20);
  expect(
    await (
      await page.request.get(
        `${proxy}/api/versions/${before.detail.version.id}`,
      )
    ).json(),
  ).toEqual(before.detail);
});
