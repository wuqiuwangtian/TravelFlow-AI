import { readFile } from "node:fs/promises";
import { test, expect, type Page } from "playwright/test";

const proxy = "/api/backend";
const failure = {
  error: { code: "BACKEND_UNREACHABLE", message: "验收服务暂时不可用" },
};
async function studio(page: Page) {
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${trip.session_id}?branch=${trip.branch_id}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "双方案比较" })
    .click();
  const complete = page.waitForResponse(
    (response) =>
      response.url().endsWith("/studio/runs") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "运行全部", exact: true }).click();
  expect((await complete).ok()).toBe(true);
  await expect(
    page.getByRole("button", { name: "运行全部", exact: true }),
  ).toBeEnabled();
  return trip;
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("节点复制成功在操作旁可见，失败可手选且重试，下载回执不声称已保存", async ({
  page,
}, info) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async () => {
          if (document.documentElement.dataset.copyFail === "true")
            throw new Error("Denied");
        },
      },
    });
  });
  await studio(page);
  const inspector = page.getByRole("complementary", {
    name: "节点参数与产物",
    exact: true,
  });
  await inspector.getByLabel("当前节点", { exact: true }).selectOption("slow");
  const actions = inspector.getByRole("region", {
    name: "节点正文操作",
    exact: true,
  });
  await actions.getByRole("button", { name: "复制正文", exact: true }).click();
  await expect(actions.getByRole("status")).toHaveText("节点正文已复制。");
  await expect(actions.getByRole("status")).toBeInViewport();
  await page.screenshot({ path: info.outputPath("node-copy-result.png") });
  await page.evaluate(() => {
    document.documentElement.dataset.copyFail = "true";
  });
  await actions.getByRole("button", { name: "复制正文", exact: true }).click();
  await expect(actions.getByRole("alert")).toContainText("复制未成功");
  await expect(
    actions.getByRole("textbox", { name: "可手动复制的节点正文" }),
  ).toContainText("节点产物指纹");
  await page.evaluate(() => {
    document.documentElement.dataset.copyFail = "false";
  });
  await actions.getByRole("button", { name: "复制正文", exact: true }).click();
  await expect(actions.getByRole("alert")).toHaveCount(0);
  await expect(actions.getByRole("textbox")).toHaveCount(0);
  const downloaded = page.waitForEvent("download");
  await actions
    .getByRole("button", { name: "下载此节点 Markdown", exact: true })
    .click();
  expect(await readFile((await (await downloaded).path())!, "utf8")).toContain(
    "节点产物指纹",
  );
  await expect(actions.getByRole("status")).toContainText(
    "已发起 Markdown 下载",
  );
});

test("工程导出与导入有原位回执，无效文件保留工程，导入成功不自动保存", async ({
  page,
}) => {
  const trip = await studio(page);
  const tools = page.getByRole("complementary", {
    name: "节点工具箱",
    exact: true,
  });
  const downloaded = page.waitForEvent("download");
  await tools
    .getByRole("button", { name: "导出工程 JSON", exact: true })
    .click();
  const graph = JSON.parse(
    await readFile((await (await downloaded).path())!, "utf8"),
  );
  await expect(tools.getByRole("status")).toContainText("已发起工程 JSON 下载");
  const input = page.getByLabel("导入工程文件", { exact: true });
  await input.setInputFiles({
    name: "invalid.json",
    mimeType: "application/json",
    buffer: Buffer.from("invalid"),
  });
  await expect(tools.getByRole("alert")).toContainText("原工程已保留");
  await expect(page.getByLabel("工程名称", { exact: true })).toHaveValue(
    graph.title,
  );
  graph.title = "导入后待保存的验收工程";
  await input.setInputFiles({
    name: "valid.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(graph)),
  });
  await expect(tools.getByRole("status")).toContainText("工程尚未保存，可撤销");
  await expect(tools.getByRole("alert")).toHaveCount(0);
  await expect(page.getByLabel("工程名称", { exact: true })).toHaveValue(
    graph.title,
  );
  const saved = await (
    await page.request.get(`${proxy}/api/branches/${trip.branch_id}/studio`)
  ).json();
  expect(saved.graph.title).not.toBe(graph.title);
  await expect(
    page.getByRole("button", { name: "保存工程", exact: true }),
  ).toBeEnabled();
});

test("设置保存失败在原入口重试保留输入，成功回执靠近按钮且不重复提交", async ({
  page,
}, info) => {
  let persisted = "";
  let requests = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/backend/api/system/settings", async (route) => {
    if (route.request().method() === "GET") {
      const data = await (await route.fetch()).json();
      if (persisted) data.llm_model = persisted;
      return route.fulfill({ json: data });
    }
    requests++;
    if (requests === 1) {
      await gate;
      return route.fulfill({ status: 503, json: failure });
    }
    persisted = route.request().postDataJSON().llm_model;
    return route.fulfill({
      json: {
        saved: true,
        changed: ["llm_model"],
        message: "验收设置已保存",
        reloaded: true,
      },
    });
  });
  await page.route("**/api/backend/api/system/models", (route) =>
    route.fulfill({
      json: { models: ["local-test-model"], message: "隔离模型列表" },
    }),
  );
  await page.goto("/settings");
  const model = page.getByRole("combobox", { name: /^模型名称/ });
  await model.fill("local-test-model");
  const actions = page.getByRole("region", {
    name: "设置保存操作",
    exact: true,
  });
  await actions.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect.poll(() => requests).toBe(1);
  await expect(
    actions.getByRole("button", { name: "正在保存设置…", exact: true }),
  ).toBeDisabled();
  await expect(model).toBeDisabled();
  release();
  await expect(actions.getByRole("alert")).toContainText(failure.error.message);
  await expect(actions.getByRole("alert")).toBeInViewport();
  await expect(model).toHaveValue("local-test-model");
  await actions.getByRole("button", { name: "重试", exact: true }).click();
  await expect(actions.getByRole("status")).toContainText("验收设置已保存");
  await expect(actions.getByRole("status")).toBeInViewport();
  await expect(actions.getByRole("alert")).toHaveCount(0);
  expect(requests).toBe(2);
  await page.screenshot({ path: info.outputPath("settings-result.png") });
});

test("设为当前原位等待和失败重试，选择不被重置且不生成新版本", async ({
  page,
}) => {
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  const title = `当前方案验收 ${crypto.randomUUID()}`;
  const data = await (
    await page.request.post(
      `${proxy}/api/sessions/${trip.session_id}/branches`,
      { data: { title, parent_branch_id: trip.branch_id } },
    )
  ).json();
  const branch = data.items.find(
    (item: { title: string }) => item.title === title,
  );
  await page.goto("/compare");
  await page
    .getByRole("combobox", { name: "选择对比旅行" })
    .selectOption(trip.session_id);
  const toggle = page.getByRole("button").filter({ hasText: title });
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  const card = page.getByRole("region", {
    name: `对比方案：${title}`,
    exact: true,
  });
  let requests = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/api/backend/api/branches/${branch.id}/primary`,
    async (route) => {
      requests++;
      if (requests > 1) return route.continue();
      await gate;
      await route.fulfill({ status: 503, json: failure });
    },
  );
  await card.getByRole("button", { name: "设为当前", exact: true }).click();
  await expect.poll(() => requests).toBe(1);
  await expect(
    card.getByRole("button", { name: "正在设置…", exact: true }),
  ).toBeDisabled();
  release();
  await expect(card.getByRole("alert")).toContainText(failure.error.message);
  await card.getByRole("button", { name: "重试", exact: true }).click();
  await expect(
    card.getByRole("status").filter({ hasText: "已设为当前方案" }),
  ).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  const saved = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  expect(
    saved.branches.find((item: { is_primary: boolean }) => item.is_primary).id,
  ).toBe(branch.id);
  expect(saved.versions[branch.id]).toHaveLength(1);
});
