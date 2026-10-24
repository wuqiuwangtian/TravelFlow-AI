import { expect, test, type Page } from "playwright/test";
import { randomUUID } from "node:crypto";

const proxy = "/api/backend";
const endpoint = `${proxy}/api/workflow/user-templates`;
const library = (page: Page) =>
  page.getByRole("region", { name: "我的工程模板", exact: true });
const studio = (page: Page) =>
  page.getByRole("region", { name: "节点工程", exact: true });
const editor = (page: Page) =>
  page.getByRole("dialog", { name: "保存个人工程模板", exact: true });
const card = (page: Page, title: string) =>
  library(page).getByRole("article", {
    name: `个人模板 ${title}`,
    exact: true,
  });
const input = (page: Page) => page.getByLabel("节点输入内容", { exact: true });
async function project(page: Page, bid: string) {
  return (await page.request.get(`${proxy}/api/branches/${bid}/studio`)).json();
}
async function latest(page: Page, sid: string, bid: string) {
  return (await page.request.get(`${proxy}/api/sessions/${sid}`))
    .json()
    .then((s) => s.versions[bid]);
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
async function showLibrary(page: Page) {
  if (!(await library(page).isVisible()))
    await page.getByRole("button", { name: "模板库", exact: true }).click();
  await expect(library(page)).toBeVisible();
}
async function apiCreate(page: Page, title: string) {
  const templates = await (
    await page.request.get(`${proxy}/api/workflow/templates`)
  ).json();
  const graph = templates.find(
    (t: { id: string }) => t.id === "structured",
  ).graph;
  const result = await page.request.post(endpoint, {
    data: {
      request_id: randomUUID(),
      title,
      description: "保存的模板说明",
      graph,
    },
  });
  expect(result.ok()).toBe(true);
  return result.json();
}
async function apiUpdate(
  page: Page,
  saved: {
    id: string;
    revision: number;
    title: string;
    description: string;
    graph: unknown;
    trip_input_ids: string[];
  },
  description: string,
) {
  const response = await page.request.put(`${endpoint}/${saved.id}`, {
    data: {
      request_id: randomUUID(),
      expected_revision: saved.revision,
      title: saved.title,
      description,
      graph: saved.graph,
      trip_input_ids: saved.trip_input_ids,
    },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}
async function saveForm(page: Page, label = "保存个人模板") {
  const done = page.waitForResponse(
    (r) =>
      r.url().includes("/workflow/user-templates") &&
      ["POST", "PUT"].includes(r.request().method()),
  );
  await editor(page).getByRole("button", { name: label, exact: true }).click();
  const response = await done;
  expect(response.ok()).toBe(true);
  await expect(editor(page)).toHaveCount(0);
  return response.json();
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("命名模板跨旅行替换需求、保留通用文字，载入草稿可撤销再执行", async ({
  page,
}, info) => {
  const source = await start(page);
  const before = await latest(page, source.session_id, source.branch_id);
  await input(page).fill("来源旅行私人需求，不应自动用于其他旅行。");
  await page
    .getByRole("complementary", { name: "节点工具箱", exact: true })
    .getByRole("button")
    .filter({ hasText: "＋ 文本输入" })
    .click();
  const constantId = await page
    .getByLabel("当前节点", { exact: true })
    .inputValue();
  await page.getByLabel("节点名称", { exact: true }).fill("通用约束");
  await input(page).fill("雨天优先安排室内，午后留休息时间。");
  const name = `慢游模板-${randomUUID()}`;
  await page
    .getByRole("button", { name: "保存为我的模板", exact: true })
    .click();
  await expect(
    editor(page).getByLabel("旅行需求 使用本次旅行需求", { exact: true }),
  ).toBeChecked();
  await editor(page)
    .getByLabel("通用约束 使用本次旅行需求", { exact: true })
    .uncheck();
  await editor(page).getByLabel("个人模板名称", { exact: true }).fill(name);
  await editor(page)
    .getByLabel("个人模板说明", { exact: true })
    .fill("适合雨天与慢节奏的通用编排");
  expect(
    await editor(page).evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(false);
  await editor(page).screenshot({
    path: info.outputPath("personal-template-editor.png"),
  });
  const saved = await saveForm(page);
  expect(saved.graph.nodes[0].parameters.text).toBe("");
  expect(
    saved.graph.nodes.find((n: { id: string }) => n.id === constantId)
      .parameters.text,
  ).toContain("雨天优先");
  expect(saved.trip_input_ids).toEqual(["brief"]);
  expect((await project(page, source.branch_id)).revision).toBe(0);
  expect(await latest(page, source.session_id, source.branch_id)).toEqual(
    before,
  );

  const target = await (await page.request.post(`${proxy}/test/moves`)).json();
  const targetBefore = await latest(page, target.session_id, target.branch_id);
  await page.goto(`/workflow/${target.session_id}?branch=${target.branch_id}`);
  await expect(card(page, name)).toBeVisible();
  await library(page).getByLabel("搜索我的模板", { exact: true }).fill(name);
  await card(page, name).screenshot({
    path: info.outputPath("personal-template-card.png"),
  });
  await card(page, name)
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  await expect(input(page)).toHaveValue(/目的地：北京/);
  await expect(input(page)).not.toHaveValue(/来源旅行私人/);
  await page.getByLabel("当前节点", { exact: true }).selectOption(constantId);
  await expect(input(page)).toHaveValue(/雨天优先/);
  expect((await project(page, target.branch_id)).runs).toEqual([]);
  expect((await project(page, target.branch_id)).revision).toBe(0);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "默认工程模板", exact: true }),
  ).toBeVisible();
  await card(page, name)
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/studio/runs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "运行全部", exact: true }).click();
  const run = await (await done).json();
  expect(run.status).toBe("succeeded");
  expect(
    run.nodes.every((n: { status: string }) => n.status === "succeeded"),
  ).toBe(true);
  expect(await latest(page, target.session_id, target.branch_id)).toEqual(
    targetBefore,
  );
});

test("保存及更新回复丢失可重试，名称说明和当前工程更新各自保留正确内容", async ({
  page,
}) => {
  const source = await start(page);
  const name = `重试模板-${randomUUID()}`;
  await page
    .getByRole("button", { name: "保存为我的模板", exact: true })
    .click();
  await editor(page).getByLabel("个人模板名称", { exact: true }).fill(name);
  await editor(page)
    .getByLabel("个人模板说明", { exact: true })
    .fill("创建前的说明");
  let droppedCreate = false;
  await page.route("**/api/workflow/user-templates", async (route) => {
    if (route.request().method() === "POST" && !droppedCreate) {
      droppedCreate = true;
      expect((await route.fetch()).ok()).toBe(true);
      return route.abort();
    }
    return route.continue();
  });
  await editor(page)
    .getByRole("button", { name: "保存个人模板", exact: true })
    .click();
  await expect(editor(page).getByRole("alert")).toBeVisible();
  await expect(
    editor(page).getByLabel("个人模板说明", { exact: true }),
  ).toHaveValue("创建前的说明");
  const saved = await saveForm(page);
  expect(saved.revision).toBe(1);
  const all = await (await page.request.get(endpoint)).json();
  expect(all.filter((t: { title: string }) => t.title === name)).toHaveLength(
    1,
  );
  await showLibrary(page);
  await card(page, name)
    .getByRole("button", { name: "编辑名称与说明", exact: true })
    .click();
  await editor(page)
    .getByLabel("个人模板说明", { exact: true })
    .fill("重试后只更新一次的说明");
  let droppedUpdate = false;
  await page.route(
    `**/api/workflow/user-templates/${saved.id}`,
    async (route) => {
      if (route.request().method() === "PUT" && !droppedUpdate) {
        droppedUpdate = true;
        expect((await route.fetch()).ok()).toBe(true);
        return route.abort();
      }
      return route.continue();
    },
  );
  await editor(page)
    .getByRole("button", { name: "更新此模板", exact: true })
    .click();
  await expect(editor(page).getByRole("alert")).toBeVisible();
  const updated = await saveForm(page, "更新此模板");
  expect(updated.revision).toBe(2);
  expect(updated.graph).toEqual(saved.graph);
  await page.getByLabel("当前节点", { exact: true }).selectOption("plan");
  await page
    .getByLabel("节点指令", { exact: true })
    .fill("复用时每天安排三处以内，明确未知费用。");
  await card(page, name)
    .getByRole("button", { name: "用当前工程更新", exact: true })
    .click();
  const replaced = await saveForm(page, "更新此模板");
  expect(replaced.id).toBe(saved.id);
  expect(replaced.revision).toBe(3);
  expect(
    replaced.graph.nodes.find((n: { id: string }) => n.id === "plan").parameters
      .prompt,
  ).toContain("三处以内");
  expect((await project(page, source.branch_id)).revision).toBe(0);
});

test("旧窗口保存和删除被拒绝，表单可核对重载，删除回复丢失重试不改变工程", async ({
  page,
}) => {
  const source = await start(page);
  const name = `并发模板-${randomUUID()}`;
  let saved = await apiCreate(page, name);
  await showLibrary(page);
  await library(page)
    .getByRole("button", { name: "刷新我的模板", exact: true })
    .click();
  await card(page, name)
    .getByRole("button", { name: "编辑名称与说明", exact: true })
    .click();
  await editor(page)
    .getByLabel("个人模板说明", { exact: true })
    .fill("本页尚未保存的说明");
  saved = await apiUpdate(page, saved, "另一窗口的新说明");
  await editor(page)
    .getByRole("button", { name: "更新此模板", exact: true })
    .click();
  await expect(editor(page).getByRole("alert")).toContainText("其他页面更新");
  await expect(
    editor(page).getByLabel("个人模板说明", { exact: true }),
  ).toHaveValue("本页尚未保存的说明");
  await editor(page)
    .getByRole("button", { name: "重新载入最新模板（替换表单）", exact: true })
    .click();
  await expect(
    editor(page).getByLabel("个人模板说明", { exact: true }),
  ).toHaveValue("另一窗口的新说明");
  saved = await saveForm(page, "更新此模板");
  expect(saved.revision).toBe(3);
  await card(page, name)
    .getByRole("button", { name: "删除模板", exact: true })
    .click();
  saved = await apiUpdate(page, saved, "删除前新增的说明");
  await card(page, name)
    .getByRole("button", { name: "确认删除模板", exact: true })
    .click();
  await expect(studio(page).getByRole("alert")).toContainText("其他页面更新");
  await library(page)
    .getByRole("button", { name: "刷新我的模板", exact: true })
    .click();
  await expect(card(page, name)).toContainText("修订4");
  await card(page, name)
    .getByRole("button", { name: "删除模板", exact: true })
    .click();
  let dropped = false;
  await page.route(
    `**/api/workflow/user-templates/${saved.id}`,
    async (route) => {
      if (route.request().method() === "DELETE" && !dropped) {
        dropped = true;
        expect((await route.fetch()).ok()).toBe(true);
        return route.abort();
      }
      return route.continue();
    },
  );
  await card(page, name)
    .getByRole("button", { name: "确认删除模板", exact: true })
    .click();
  await expect(studio(page).getByRole("alert")).toContainText(/网络|后端/);
  await card(page, name)
    .getByRole("button", { name: "确认删除模板", exact: true })
    .click();
  await expect(card(page, name)).toHaveCount(0);
  await expect(page.getByLabel("当前节点", { exact: true })).toHaveValue(
    "brief",
  );
  expect((await project(page, source.branch_id)).revision).toBe(0);
});

test("模板库及载入失败可恢复，默认模板仍可用，失败或删除的模板不覆盖未保存草稿", async ({
  page,
}) => {
  let failList = true;
  await page.route("**/api/workflow/user-templates", (route) =>
    route.request().method() === "GET" && failList
      ? route.fulfill({
          status: 503,
          json: { message: "验收用模板库暂不可用" },
        })
      : route.continue(),
  );
  const source = await start(page);
  await input(page).fill("本页未保存的旅行需求");
  const name = `恢复模板-${randomUUID()}`;
  const saved = await apiCreate(page, name);
  await showLibrary(page);
  await expect(library(page).getByRole("alert")).toContainText("读取失败");
  await expect(
    page
      .getByRole("region", { name: "默认工程模板", exact: true })
      .getByRole("button"),
  ).toHaveCount(5);
  failList = false;
  await library(page)
    .getByRole("button", { name: "刷新我的模板", exact: true })
    .click();
  await expect(card(page, name)).toBeVisible();
  let failLoad = true;
  await page.route("**/studio/template-drafts/*", (route) =>
    failLoad ? route.abort() : route.continue(),
  );
  await card(page, name)
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  await expect(studio(page).getByRole("alert")).toContainText(/网络|后端/);
  await expect(input(page)).toHaveValue("本页未保存的旅行需求");
  failLoad = false;
  await card(page, name)
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  await expect(input(page)).toHaveValue(/目的地：北京/);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(input(page)).toHaveValue("本页未保存的旅行需求");
  await showLibrary(page);
  const removed = await page.request.delete(`${endpoint}/${saved.id}`, {
    data: { request_id: randomUUID(), expected_revision: saved.revision },
  });
  expect(removed.ok()).toBe(true);
  await card(page, name)
    .getByRole("button", { name: "载入模板", exact: true })
    .click();
  await expect(studio(page).getByRole("alert")).toContainText("已删除或不存在");
  await expect(input(page)).toHaveValue("本页未保存的旅行需求");
  expect((await project(page, source.branch_id)).revision).toBe(0);
});
