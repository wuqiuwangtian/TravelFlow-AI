import { expect, test, type Page } from "playwright/test";
import { readFile } from "node:fs/promises";

const proxy = "/api/backend";
const studio = (page: Page) =>
  page.getByRole("region", { name: "节点工程", exact: true });
const inspector = (page: Page) =>
  page.getByRole("complementary", { name: "节点参数与产物", exact: true });
async function project(page: Page, bid: string) {
  return (await page.request.get(`${proxy}/api/branches/${bid}/studio`)).json();
}
async function start(page: Page) {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${seed.session_id}?branch=${seed.branch_id}`);
  const presets = page.getByRole("region", {
    name: "默认工程模板",
    exact: true,
  });
  await expect(presets.getByRole("button")).toHaveCount(3);
  await presets.getByRole("button").filter({ hasText: "双方案比较" }).click();
  await expect(
    page.getByRole("region", { name: "自由节点画布", exact: true }),
  ).toBeVisible();
  return seed;
}
async function save(page: Page) {
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/studio") && r.request().method() === "PUT",
  );
  await studio(page)
    .getByRole("button", { name: "保存工程", exact: true })
    .click();
  expect((await done).ok()).toBe(true);
}
async function execute(page: Page, name = "运行全部") {
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/studio/runs") && r.request().method() === "POST",
  );
  await studio(page).getByRole("button", { name, exact: true }).click();
  const response = await done;
  expect(response.ok()).toBe(true);
  await expect(
    studio(page).getByRole("button", { name: "运行全部", exact: true }),
  ).toBeEnabled();
  return response.json();
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("模板分支与汇合真实执行，节点可拖动，成稿和完整运行快照可追溯", async ({
  page,
}, info) => {
  const seed = await start(page);
  await expect(page.getByLabel("节点输入内容", { exact: true })).toHaveValue(
    /北京/,
  );
  if (info.project.name === "desktop") {
    const node = page.getByRole("button", {
      name: "选择节点 旅行需求",
      exact: true,
    });
    const box = await node.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      box!.x + box!.width / 2 + 30,
      box!.y + box!.height / 2 + 25,
      { steps: 5 },
    );
    await page.mouse.up();
  }
  await save(page);
  if (info.project.name === "desktop")
    expect(
      (await project(page, seed.branch_id)).graph.nodes.find(
        (n: { id: string }) => n.id === "brief",
      ).x,
    ).toBeGreaterThan(60);
  const first = await execute(page);
  expect(first.status).toBe("succeeded");
  expect(first.nodes).toHaveLength(6);
  expect(
    first.graph.edges.filter((e: { target: string }) => e.target === "compare"),
  ).toHaveLength(3);
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("document");
  await expect(
    inspector(page).getByLabel("节点生成内容", { exact: true }),
  ).toContainText("节点产物指纹");
  await inspector(page).getByText("资料来源 · 1", { exact: true }).click();
  await expect(
    inspector(page).getByRole("link", { name: "官方资料", exact: true }),
  ).toHaveAttribute("href", "https://example.com/info");
  const download = page.waitForEvent("download");
  await inspector(page)
    .getByRole("button", { name: "下载此节点 Markdown", exact: true })
    .click();
  expect(await readFile((await (await download).path())!, "utf8")).toContain(
    "https://example.com/info",
  );
  await studio(page)
    .getByRole("button", { name: "查看运行时工程", exact: true })
    .click();
  await expect(
    inspector(page).getByRole("heading", {
      name: "运行快照 · 只读",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    inspector(page).getByLabel("节点名称", { exact: true }),
  ).toBeDisabled();
  await studio(page)
    .getByRole("button", { name: "回到当前工程", exact: true })
    .click();
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("compare");
  await page.screenshot({
    path: info.outputPath("free-generation-studio.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  const current = await project(page, seed.branch_id);
  expect(current.revision).toBe(1);
  await page.reload();
  await expect(
    page.getByRole("region", { name: "工程运行记录", exact: true }),
  ).toBeVisible();
  const original = await (
    await page.request.get(`${proxy}/api/sessions/${seed.session_id}`)
  ).json();
  expect(original.versions[seed.branch_id]).toHaveLength(1);
});

test("修改单个分支仅重算必要步骤，重复执行可复用，强制重跑可局部执行", async ({
  page,
}) => {
  await start(page);
  await execute(page);
  const cached = await execute(page);
  expect(
    cached.nodes.every((n: { status: string }) => n.status === "cached"),
  ).toBe(true);
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("slow");
  await inspector(page)
    .getByLabel("节点指令", { exact: true })
    .fill("只生成松弛版，增加一处博物馆，保留预算约束。");
  await expect(
    page
      .getByRole("button", { name: "选择节点 松弛版行程", exact: true })
      .locator(".."),
  ).toContainText("待更新");
  await expect(
    page
      .getByRole("button", { name: "选择节点 对比与决策", exact: true })
      .locator(".."),
  ).toContainText("待更新");
  const changed = await execute(page);
  const states = Object.fromEntries(
    changed.nodes.map((n: { node_id: string; status: string }) => [
      n.node_id,
      n.status,
    ]),
  );
  expect(states.full).toBe("cached");
  expect(states.research).toBe("cached");
  expect(states.slow).toBe("succeeded");
  expect(states.compare).toBe("succeeded");
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("slow");
  const partial = await execute(page, "强制重跑此节点");
  expect(
    partial.nodes.map((n: { node_id: string }) => n.node_id).sort(),
  ).toEqual(["brief", "research", "slow"]);
  expect(
    partial.nodes.find((n: { node_id: string }) => n.node_id === "slow").status,
  ).toBe("succeeded");
});

test("自由增删复制与撤销，端口类型及循环校验，工程导入导出保留结构", async ({
  page,
}, info) => {
  await start(page);
  await page.getByText("连线列表与键盘连接 · 9 条", { exact: true }).click();
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("research:sources");
  await page
    .getByLabel("连线输入", { exact: true })
    .selectOption("compare:context");
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  await expect(studio(page).getByRole("alert")).toContainText("端口不兼容");
  await page
    .getByLabel("连线输入", { exact: true })
    .selectOption("compare:sources");
  if (info.project.name === "desktop") {
    await page
      .getByRole("region", { name: "自由节点画布", exact: true })
      .scrollIntoViewIfNeeded();
    const output = await page
      .getByRole("button", { name: "目的地资料 输出 资料", exact: true })
      .boundingBox();
    const input = await page
      .getByRole("button", { name: "对比与决策 输入 资料", exact: true })
      .boundingBox();
    expect(output).not.toBeNull();
    expect(input).not.toBeNull();
    await page.mouse.move(
      output!.x + output!.width / 2,
      output!.y + output!.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      input!.x + input!.width / 2,
      input!.y + input!.height / 2,
      { steps: 8 },
    );
    await page.mouse.up();
  } else
    await page.getByRole("button", { name: "连接端口", exact: true }).click();
  await expect(studio(page).getByRole("alert")).toHaveCount(0);
  await page
    .getByLabel("连线输出", { exact: true })
    .selectOption("compare:text");
  await page
    .getByLabel("连线输入", { exact: true })
    .selectOption("slow:context");
  await page.getByRole("button", { name: "连接端口", exact: true }).click();
  await expect(studio(page).getByRole("alert")).toContainText("形成循环");
  await page
    .getByRole("complementary", { name: "节点工具箱", exact: true })
    .getByRole("button")
    .filter({ hasText: "＋ AI 生成" })
    .click();
  await inspector(page)
    .getByRole("button", { name: "复制节点", exact: true })
    .click();
  await expect(
    inspector(page).getByLabel("当前节点", { exact: true }).getByRole("option"),
  ).toHaveCount(9);
  await inspector(page)
    .getByRole("button", { name: "删除节点", exact: true })
    .click();
  await studio(page).getByRole("button", { name: "撤销", exact: true }).click();
  const exported = page.waitForEvent("download");
  await studio(page)
    .getByRole("button", { name: "导出工程 JSON", exact: true })
    .click();
  const json = await readFile((await (await exported).path())!, "utf8");
  const document = JSON.parse(json);
  expect(document.nodes).toHaveLength(8);
  expect(document.edges).toHaveLength(10);
  await page.getByLabel("导入工程文件", { exact: true }).setInputFiles({
    name: "workflow.json",
    mimeType: "application/json",
    buffer: Buffer.from(json),
  });
  await expect(
    inspector(page).getByLabel("当前节点", { exact: true }).getByRole("option"),
  ).toHaveCount(9);
  await save(page);
});

test("失败分支可检查并修复，下游阻塞，慢节点可停止后保留上游继续运行", async ({
  page,
}) => {
  const seed = await start(page);
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("slow");
  await inspector(page)
    .getByLabel("节点指令", { exact: true })
    .fill("测试失败节点");
  const failed = await execute(page);
  expect(failed.status).toBe("failed");
  expect(
    failed.nodes.find((n: { node_id: string }) => n.node_id === "compare")
      .status,
  ).toBe("blocked");
  expect(
    failed.nodes.find((n: { node_id: string }) => n.node_id === "full").status,
  ).toBe("succeeded");
  await expect(inspector(page)).toContainText("验收用模型鉴权失败");
  await inspector(page)
    .getByLabel("节点指令", { exact: true })
    .fill("测试慢速节点，生成松弛版路线");
  const finished = page.waitForResponse(
    (r) => r.url().endsWith("/studio/runs") && r.request().method() === "POST",
  );
  await studio(page)
    .getByRole("button", { name: "运行全部", exact: true })
    .click();
  await expect
    .poll(async () =>
      (await project(page, seed.branch_id)).runs[0]?.nodes.some(
        (n: { status: string }) => n.status === "running",
      ),
    )
    .toBe(true);
  await studio(page)
    .getByRole("button", { name: "停止运行", exact: true })
    .click();
  expect((await (await finished).json()).status).toBe("interrupted");
  await expect(
    studio(page).getByRole("button", { name: "运行全部", exact: true }),
  ).toBeEnabled();
  await inspector(page)
    .getByLabel("节点指令", { exact: true })
    .fill("生成松弛版旅行路线，明确待核实事项。");
  const resumed = await execute(page);
  expect(resumed.status).toBe("succeeded");
  expect(
    resumed.nodes.find((n: { node_id: string }) => n.node_id === "research")
      .status,
  ).toBe("cached");
});

test("未保存草稿可刷新恢复，跨窗口保存冲突保留草稿并允许载入最新", async ({
  page,
}) => {
  const seed = await start(page);
  await save(page);
  await inspector(page)
    .getByLabel("节点输入内容", { exact: true })
    .fill("北京三天，我的未保存工程输入");
  await page.reload();
  await expect(
    inspector(page).getByLabel("节点输入内容", { exact: true }),
  ).toHaveValue("北京三天，我的未保存工程输入");
  const server = await project(page, seed.branch_id);
  server.graph.title = "另一个窗口的新工程";
  expect(
    (
      await page.request.put(`${proxy}/api/branches/${seed.branch_id}/studio`, {
        data: { expected_revision: server.revision, graph: server.graph },
      })
    ).ok(),
  ).toBe(true);
  await studio(page)
    .getByRole("button", { name: "保存工程", exact: true })
    .click();
  await expect(studio(page).getByRole("alert")).toContainText("其他窗口更新");
  await expect(
    inspector(page).getByLabel("节点输入内容", { exact: true }),
  ).toHaveValue("北京三天，我的未保存工程输入");
  await studio(page)
    .getByRole("button", { name: "载入最新工程", exact: true })
    .click();
  await expect(
    studio(page).getByLabel("工程名称", { exact: true }),
  ).toHaveValue("另一个窗口的新工程");
});
