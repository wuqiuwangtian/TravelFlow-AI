import { expect, test, type Page } from "playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import type { NodeArtifact } from "../../lib/studio-types";

const proxy = "/api/backend";
const inspector = (page: Page) =>
  page.getByRole("complementary", { name: "节点参数与产物", exact: true });
const dataView = (page: Page) =>
  page.getByRole("region", { name: "结构化节点数据", exact: true });
const select = (page: Page, id: string) =>
  page.getByLabel("当前节点", { exact: true }).selectOption(id);
const artifact = (
  run: { nodes: { node_id: string; artifact: NodeArtifact }[] },
  id: string,
) => run.nodes.find((n) => n.node_id === id)!.artifact;
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
    .filter({ hasText: "候选池分日规划" })
    .click();
  await page
    .getByLabel("节点输入内容", { exact: true })
    .fill(
      "上海出发，北京三天，两人，全团预算2000元，喜欢城市漫步，推荐具体景点。",
    );
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
async function versions(
  page: Page,
  seed: { session_id: string; branch_id: string },
) {
  return (
    await (
      await page.request.get(`${proxy}/api/sessions/${seed.session_id}`)
    ).json()
  ).versions[seed.branch_id];
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

test("独立候选池可核对与导出，改节奏和天数复用选点，分日记录刷新及快照保留", async ({
  page,
}, info) => {
  const seed = await start(page);
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  expect(artifact(first, "allocation").plan!.allocation!.selected_count).toBe(
    9,
  );
  await select(page, "candidates");
  await expect(dataView(page)).toContainText("10处候选 · 尚未分日");
  await expect(dataView(page)).toContainText("候选理由");
  await expect(dataView(page)).toContainText("天坛公园");
  expect(
    await dataView(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await dataView(page).screenshot({
    path: info.outputPath("candidate-pool.png"),
  });
  const done = page.waitForEvent("download");
  await dataView(page)
    .getByRole("button", { name: "下载结构化数据 JSON", exact: true })
    .click();
  const json = JSON.parse(await readFile((await (await done).path())!, "utf8"));
  expect(json.candidates.places).toHaveLength(10);
  expect(json.plan).toBeNull();
  await select(page, "allocation");
  await page
    .getByLabel("分日安排节奏", { exact: true })
    .selectOption("relaxed");
  const second = await execute(page);
  expect(
    second.nodes.find((n: { node_id: string }) => n.node_id === "candidates")
      .status,
  ).toBe("cached");
  expect(artifact(second, "allocation").plan!.allocation!.selected_count).toBe(
    6,
  );
  await expect(
    dataView(page).getByLabel("候选分日记录", { exact: true }),
  ).toContainText("未安排");
  await expect(dataView(page)).toContainText("候选10处 · 本次安排6处");
  await dataView(page).screenshot({
    path: info.outputPath("candidate-allocation.png"),
  });
  await page.getByLabel("分日规划天数", { exact: true }).fill("4");
  await page
    .getByLabel("分日安排节奏", { exact: true })
    .selectOption("balanced");
  const third = await execute(page);
  expect(artifact(third, "allocation").plan!.days).toHaveLength(4);
  expect(artifact(third, "allocation").plan!.allocation!.selected_count).toBe(
    10,
  );
  expect(artifact(first, "candidates").candidates).toEqual(
    artifact(third, "candidates").candidates,
  );
  expect(await versions(page, seed)).toHaveLength(0);
  await page.reload();
  await select(page, "allocation");
  await expect(page.getByLabel("分日规划天数", { exact: true })).toHaveValue(
    "4",
  );
  await page
    .getByRole("button", { name: "查看运行时工程", exact: true })
    .click();
  await expect(page.getByLabel("分日安排节奏", { exact: true })).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});

test("必去缺失、名单矛盾和天数过多清楚报错，修正规则继续复用候选池", async ({
  page,
}) => {
  await start(page);
  await execute(page);
  await select(page, "allocation");
  await page.getByText("时间与筛选规则", { exact: true }).click();
  await page.getByLabel("分日必去名单", { exact: true }).fill("不存在的景点");
  expect((await execute(page, "运行到此节点")).status).toBe("failed");
  await expect(inspector(page)).toContainText("候选池没有找到必去景点");
  await page.getByLabel("分日必去名单", { exact: true }).fill("北海");
  await page.getByLabel("分日排除名单", { exact: true }).fill("北海");
  expect((await execute(page, "运行到此节点")).status).toBe("failed");
  await expect(inspector(page)).toContainText("同一候选景点");
  await page.getByLabel("分日排除名单", { exact: true }).fill("");
  await page.getByLabel("分日规划天数", { exact: true }).fill("11");
  expect((await execute(page, "运行到此节点")).status).toBe("failed");
  await expect(inspector(page)).toContainText("不足以覆盖每一天");
  await page.getByLabel("分日规划天数", { exact: true }).fill("2");
  await page
    .getByLabel("分日安排节奏", { exact: true })
    .selectOption("relaxed");
  await page.getByLabel("分日必去名单", { exact: true }).fill("陶然亭");
  await page.getByLabel("分日排除名单", { exact: true }).fill("北海");
  const result = await execute(page);
  expect(result.status).toBe("succeeded");
  expect(
    result.nodes.find((n: { node_id: string }) => n.node_id === "candidates")
      .status,
  ).toBe("cached");
  await expect(dataView(page)).toContainText("必去优先");
  await expect(dataView(page)).toContainText("命中排除名单");
});

test("同一候选端口可分出不同节奏，独立重跑，参数随个人模板跨旅行复用", async ({
  page,
}) => {
  await start(page);
  await execute(page);
  await select(page, "allocation");
  await page.getByRole("button", { name: "复制节点", exact: true }).click();
  const slow = await page.getByLabel("当前节点", { exact: true }).inputValue();
  await page.getByLabel("节点名称", { exact: true }).fill("松弛分日分支");
  await page
    .getByLabel("分日安排节奏", { exact: true })
    .selectOption("relaxed");
  await page.getByText(/^连线列表与键盘连接/).click();
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("candidates:candidates");
  await page
    .getByLabel("连线输入", { exact: true })
    .selectOption(`${slow}:candidates`);
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  const forked = await execute(page);
  expect(artifact(forked, slow).plan!.allocation!.selected_count).toBe(6);
  expect(
    forked.nodes.find((n: { node_id: string }) => n.node_id === "document")
      .status,
  ).toBe("cached");
  await page.getByLabel("分日规划天数", { exact: true }).fill("4");
  const revised = await execute(page);
  expect(artifact(revised, slow).plan!.allocation!.selected_count).toBe(8);
  expect(
    revised.nodes.find((n: { node_id: string }) => n.node_id === "allocation")
      .status,
  ).toBe("cached");
  await page
    .getByRole("button", { name: "保存为我的模板", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "保存个人工程模板",
    exact: true,
  });
  const name = `双节奏候选-${randomUUID()}`;
  await dialog.getByLabel("个人模板名称", { exact: true }).fill(name);
  await dialog.getByText("核对节点指令与参数", { exact: true }).click();
  await expect(dialog).toContainText("分日天数：4；节奏：松弛");
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
  await select(page, slow);
  await expect(page.getByLabel("分日规划天数", { exact: true })).toHaveValue(
    "4",
  );
  await expect(page.getByLabel("分日安排节奏", { exact: true })).toHaveValue(
    "relaxed",
  );
  expect((await execute(page)).status).toBe("succeeded");
});

test("候选分日成稿可建立正式行程，重分配采用保护消费、人工金额和历史", async ({
  page,
}) => {
  const seed = await start(page);
  await select(page, "budget");
  await page.getByLabel("饮食预留（元／人／天）", { exact: true }).fill("100");
  await execute(page);
  const preview = await prepare(page);
  expect(preview.conflicts).toEqual([]);
  expect(await versions(page, seed)).toHaveLength(0);
  const adopted = await adopt(page);
  expect(adopted.itinerary.days).toHaveLength(3);
  expect(adopted.itinerary.budget_items).toHaveLength(3);
  expect(adopted.markdown).not.toContain("## 候选分日记录");
  const park = adopted.itinerary.days[1].activities[0];
  const edited = await page.request.put(
    `${proxy}/api/versions/${adopted.version.id}/budget-items/stay-${park.id}`,
    { data: { amount: "12.34" } },
  );
  expect(edited.ok()).toBe(true);
  const manual = await edited.json();
  const paid = await page.request.post(
    `${proxy}/api/versions/${manual.version.id}/expenses`,
    {
      data: {
        request_id: `candidate-${randomUUID()}`,
        title: "门票实付",
        amount: "15",
        category: "ticket",
        day: 2,
        activity_id: park.id,
      },
    },
  );
  expect(paid.ok()).toBe(true);
  const before = await paid.json();
  await select(page, "allocation");
  await page
    .getByLabel("分日安排节奏", { exact: true })
    .selectOption("relaxed");
  await execute(page);
  const next = await prepare(page);
  expect(next.conflicts).toEqual([]);
  const second = await adopt(page);
  expect(second.itinerary.expenses).toEqual(before.itinerary.expenses);
  expect(second.itinerary.budget_items).toEqual(before.itinerary.budget_items);
  expect(
    second.itinerary.days[1].activities.find(
      (a: { id: string }) => a.id === park.id,
    ).cost,
  ).toBe(12.34);
  expect(
    await (
      await page.request.get(`${proxy}/api/versions/${before.version.id}`)
    ).json(),
  ).toEqual(before);
});
