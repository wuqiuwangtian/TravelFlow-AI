import { expect, test, type Page } from "playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

const proxy = "/api/backend";
const select = (page: Page, id: string) =>
  page.getByLabel("当前节点", { exact: true }).selectOption(id);
const dataView = (page: Page) =>
  page.getByRole("region", { name: "结构化节点数据", exact: true });
const fields = [
  "饮食预留（元／人／天）",
  "住宿预留（元／间／晚）",
  "往返大交通预留（元／人／往返）",
  "备用金（元／全团整趟）",
  "住宿房间数",
];
const values = ["100.25", "300.50", "500.75", "123.45", "2"];
async function configure(page: Page) {
  await select(page, "budget");
  for (let i = 0; i < fields.length; i++)
    await page.getByLabel(fields[i], { exact: true }).fill(values[i]);
}
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
async function execute(page: Page) {
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/studio/runs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "运行全部", exact: true }).click();
  const response = await done;
  expect(response.ok()).toBe(true);
  await expect(
    page.getByRole("button", { name: "运行全部", exact: true }),
  ).toBeEnabled();
  return response.json();
}
async function latest(
  page: Page,
  seed: { session_id: string; branch_id: string },
) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${seed.session_id}`)
  ).json();
  const versions = session.versions[seed.branch_id];
  return {
    versions,
    detail: await (
      await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
    ).json(),
  };
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("分类预留参数改变小计、逐项解释口径，刷新快照及导出保留，上游复用且正式计划不变", async ({
  page,
}, info) => {
  const seed = await start(page);
  const before = await latest(page, seed);
  const first = await execute(page);
  await configure(page);
  const second = await execute(page);
  expect(second.status).toBe("succeeded");
  for (const id of ["brief", "research", "plan", "route"])
    expect(
      second.nodes.find((n: { node_id: string }) => n.node_id === id).status,
    ).toBe("cached");
  const budget = second.nodes.find(
    (n: { node_id: string }) => n.node_id === "budget",
  ).artifact.budget;
  const original = first.nodes.find(
    (n: { node_id: string }) => n.node_id === "budget",
  ).artifact.budget;
  expect(budget.overview.planned_cents).toBe(
    original.overview.planned_cents + 232745,
  );
  expect(budget.supplements).toHaveLength(6);
  expect(budget.overview.unknown_count).toBe(original.overview.unknown_count);
  await expect(dataView(page)).toContainText("¥3406.45");
  await expect(
    dataView(page).getByLabel("分类预算补齐", { exact: true }),
  ).toContainText("¥300.50/间/晚 × 2间");
  await expect(dataView(page)).toContainText("第1天住宿已有项目");
  expect(
    await dataView(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await dataView(page).screenshot({
    path: info.outputPath("budget-allowances.png"),
  });
  const done = page.waitForEvent("download");
  await dataView(page)
    .getByRole("button", { name: "下载结构化数据 JSON", exact: true })
    .click();
  const json = JSON.parse(await readFile((await (await done).path())!, "utf8"));
  expect(json.budget.rules.meal_per_person_day_cents).toBe(10025);
  expect(json.budget.supplements).toEqual(budget.supplements);
  expect(await latest(page, seed)).toEqual(before);
  await page.reload();
  await select(page, "budget");
  for (let i = 0; i < fields.length; i++)
    await expect(page.getByLabel(fields[i], { exact: true })).toHaveValue(
      String(Number(values[i])),
    );
  await page.getByLabel("住宿房间数", { exact: true }).fill("");
  const unknown = await execute(page);
  const partial = unknown.nodes.find(
    (n: { node_id: string }) => n.node_id === "budget",
  ).artifact.budget;
  expect(partial.overview.unknown_count).toBe(
    original.overview.unknown_count + 1,
  );
  await expect(dataView(page)).toContainText("缺少房间数");
  await page.getByLabel(fields[0], { exact: true }).fill("0");
  const zero = await execute(page);
  expect(
    zero.nodes.find((n: { node_id: string }) => n.node_id === "budget").artifact
      .budget.rules.meal_per_person_day_cents,
  ).toBe(0);
  await page
    .getByRole("button", { name: "查看运行时工程", exact: true })
    .click();
  await expect(page.getByLabel(fields[0], { exact: true })).toBeDisabled();
});

test("预留采用前结合人工预算重算，后续保留手动改价和原消费", async ({
  page,
}) => {
  const seed = await start(page);
  const before = await latest(page, seed);
  await configure(page);
  await execute(page);
  await select(page, "document");
  const prepared = page.waitForResponse(
    (r) => r.url().endsWith("/adoption") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成采用预览", exact: true }).click();
  const preview = await (await prepared).json();
  expect(preview.conflicts).toEqual([]);
  expect(
    preview.itinerary.budget_items.filter((b: { id: string }) =>
      b.id.startsWith("studio-meal"),
    ),
  ).toHaveLength(3);
  const dialog = page.getByRole("dialog", {
    name: "成稿采用核对",
    exact: true,
  });
  await expect(dialog).toContainText("保留1笔消费");
  await expect(dialog).toContainText("第1天饮食预留");
  const applied = page.waitForResponse((r) =>
    /\/adoption\/[^/]+\/apply$/.test(r.url()),
  );
  await dialog
    .getByRole("button", { name: "确认采用到正式行程", exact: true })
    .click();
  expect((await applied).ok()).toBe(true);
  const after = await latest(page, seed);
  expect(after.detail.itinerary.expenses).toEqual(
    before.detail.itinerary.expenses,
  );
  expect(after.detail.itinerary.budget_items).toEqual(
    preview.itinerary.budget_items,
  );
  expect(after.detail.markdown).toContain("第1天饮食预留");
  const edited = await page.request.put(
    `${proxy}/api/versions/${after.detail.version.id}/budget-items/studio-meal-day-1`,
    { data: { amount: "12.34" } },
  );
  expect(edited.ok()).toBe(true);
  await select(page, "budget");
  await page.getByLabel(fields[0], { exact: true }).fill("200");
  await execute(page);
  await select(page, "document");
  const next = page.waitForResponse(
    (r) => r.url().endsWith("/adoption") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成采用预览", exact: true }).click();
  const second = await (await next).json();
  expect(second.conflicts).toEqual([]);
  expect(
    second.itinerary.budget_items.find(
      (b: { id: string }) => b.id === "studio-meal-day-1",
    ).planned_cents,
  ).toBe(1234);
  expect(second.itinerary.expenses).toEqual(before.detail.itinerary.expenses);
  expect(
    await (
      await page.request.get(
        `${proxy}/api/versions/${before.detail.version.id}`,
      )
    ).json(),
  ).toEqual(before.detail);
});

test("分类估算参数随个人模板跨旅行复用，保存前能核对人数房间和金额口径", async ({
  page,
}) => {
  await start(page);
  await configure(page);
  await page
    .getByRole("button", { name: "保存为我的模板", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "保存个人工程模板",
    exact: true,
  });
  const name = `分类预算-${randomUUID()}`;
  await dialog.getByLabel("个人模板名称", { exact: true }).fill(name);
  await dialog.getByText("核对节点指令与参数", { exact: true }).click();
  await expect(dialog).toContainText("饮食：¥100.25/人/天");
  await expect(dialog).toContainText("住宿：¥300.50/间/晚；2间");
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
  await select(page, "budget");
  for (let i = 0; i < fields.length; i++)
    await expect(page.getByLabel(fields[i], { exact: true })).toHaveValue(
      String(Number(values[i])),
    );
  expect((await execute(page)).status).toBe("succeeded");
});
