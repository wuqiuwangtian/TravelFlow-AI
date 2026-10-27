import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const inspector = (page: Page) =>
  page.getByRole("complementary", { name: "节点参数与产物", exact: true });
const navigation = (page: Page) =>
  page.getByRole("navigation", { name: "工程查看位置", exact: true });
async function start(page: Page, template = "双方案比较") {
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${trip.session_id}?branch=${trip.branch_id}`);
  const templates = page.getByRole("region", {
    name: "默认工程模板",
    exact: true,
  });
  await expect(templates.getByRole("button")).toHaveCount(8);
  await templates.getByRole("button").filter({ hasText: template }).click();
  await expect(
    inspector(page).getByLabel("节点输入内容", { exact: true }),
  ).toHaveValue(/北京/);
  return trip;
}
async function execute(page: Page) {
  const response = page.waitForResponse(
    (res) =>
      res.url().endsWith("/studio/runs") && res.request().method() === "POST",
  );
  await page.getByRole("button", { name: "运行全部", exact: true }).click();
  expect((await response).ok()).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "运行全部", exact: true }),
  ).toBeEnabled();
}
async function saved(page: Page, branch: string) {
  return (
    await page.request.get(`${proxy}/api/branches/${branch}/studio`)
  ).json();
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("W1 电脑画布与检查区并排，手机直达且两端窄屏无横向溢出", async ({
  page,
  isMobile,
}, info) => {
  await start(page, "结构化行程");
  await expect(page.locator(".studio-node-palette button")).toHaveCount(12);
  if (isMobile) {
    await navigation(page)
      .getByRole("button", { name: "参数与产物", exact: true })
      .click();
    await expect(
      inspector(page).getByLabel("当前节点", { exact: true }),
    ).toBeInViewport();
    await navigation(page)
      .getByRole("button", { name: "画布", exact: true })
      .click();
  } else {
    await navigation(page)
      .getByRole("button", { name: "画布", exact: true })
      .click();
    const { stage, panel } = await page.evaluate(() => ({
      stage: document
        .querySelector(".studio-stage")!
        .getBoundingClientRect()
        .toJSON(),
      panel: document
        .querySelector(".studio-inspector")!
        .getBoundingClientRect()
        .toJSON(),
    }));
    expect(stage!.width).toBeGreaterThan(panel!.width);
    expect(stage!.x + stage!.width).toBeLessThan(panel!.x);
    expect(Math.abs(stage!.y - panel!.y)).toBeLessThan(3);
    expect(Math.abs(stage!.height - panel!.height)).toBeLessThan(3);
    await inspector(page)
      .getByLabel("当前节点", { exact: true })
      .selectOption("budget");
    await expect(
      inspector(page).getByLabel("住宿房间数", { exact: true }),
    ).toBeVisible();
    await expect
      .poll(() =>
        page
          .locator(".studio-inspector-scroll")
          .evaluate((el) => el.scrollHeight > el.clientHeight),
      )
      .toBeTruthy();
    await navigation(page)
      .getByRole("button", { name: "画布", exact: true })
      .click();
  }
  await expect
    .poll(() =>
      page
        .locator(".studio-stage")
        .evaluate(
          (el) =>
            Math.abs(
              el.getBoundingClientRect().top -
                parseFloat(getComputedStyle(el).scrollMarginTop),
            ) < 3,
        ),
    )
    .toBeTruthy();
  await page.screenshot({
    path: info.outputPath("studio-workspace.png"),
    fullPage: false,
  });
  await page.setViewportSize({ width: isMobile ? 320 : 980, height: 900 });
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1,
    ),
  ).toBeTruthy();
});

test("切换画布和检查位置保留参数草稿，查看不会保存工程或改写正式行程", async ({
  page,
}) => {
  const trip = await start(page);
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes(`${proxy}/api/`) && request.method() !== "GET")
      writes.push(request.method());
  });
  const input = inspector(page).getByLabel("节点输入内容", { exact: true });
  await input.fill("尚未保存：第三天少走路，留出喝茶时间");
  await navigation(page)
    .getByRole("button", { name: "画布", exact: true })
    .click();
  await navigation(page)
    .getByRole("button", { name: "参数与产物", exact: true })
    .click();
  await expect(input).toHaveValue("尚未保存：第三天少走路，留出喝茶时间");
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("compare");
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("brief");
  await expect(input).toHaveValue("尚未保存：第三天少走路，留出喝茶时间");
  expect(writes).toEqual([]);
  expect((await saved(page, trip.branch_id)).revision).toBe(0);
});

test("独立产物可直接查看，桌面检查保持画布视角，运行不自动采用正式行程", async ({
  page,
  isMobile,
}, info) => {
  const trip = await start(page);
  const before = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  await execute(page);
  await navigation(page)
    .getByRole("button", { name: "画布", exact: true })
    .click();
  await inspector(page)
    .getByLabel("当前节点", { exact: true })
    .selectOption("document");
  const camera = await page
    .locator('[aria-label="自由节点画布"] > div')
    .getAttribute("style");
  await inspector(page)
    .getByRole("navigation", { name: "检查区定位" })
    .getByRole("button", { name: "产物", exact: true })
    .click();
  const product = inspector(page).getByRole("region", {
    name: "节点产物检查",
    exact: true,
  });
  await expect(product).toContainText("节点产物指纹");
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1,
    ),
  ).toBeTruthy();
  await expect(
    product.getByLabel("节点生成内容", { exact: true }),
  ).toBeInViewport();
  if (!isMobile)
    expect(
      await page
        .locator('[aria-label="自由节点画布"] > div')
        .getAttribute("style"),
    ).toBe(camera);
  const after = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  expect(after.versions[trip.branch_id]).toEqual(
    before.versions[trip.branch_id],
  );
  await page.screenshot({
    path: info.outputPath("studio-artifact.png"),
    fullPage: false,
  });
  await inspector(page)
    .getByRole("button", { name: "生成采用预览", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "成稿采用核对",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  const bounds = await dialog.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    await page.evaluate(() => document.documentElement.clientWidth),
  );
  const close = dialog.getByRole("button", {
    name: "关闭采用核对",
    exact: true,
  });
  await expect(close).toBeInViewport({ ratio: 1 });
  await close.click();
  await expect(dialog).toHaveCount(0);
});

test("节点悬停和按下不改变坐标，方向键移动与撤销可实际保存", async ({
  page,
}) => {
  const trip = await start(page);
  await page.getByRole("button", { name: "聚焦节点", exact: true }).click();
  const header = page.getByRole("button", {
    name: "选择节点 旅行需求",
    exact: true,
  });
  const card = page.locator('[data-node="brief"]');
  const original = await card.evaluate((el) => ({
    x: parseFloat((el as HTMLElement).style.left),
    y: parseFloat((el as HTMLElement).style.top),
  }));
  const before = await header.boundingBox();
  await header.hover();
  await expect
    .poll(() => header.evaluate((el) => getComputedStyle(el).translate))
    .toBe("none");
  await expect
    .poll(() => header.evaluate((el) => getComputedStyle(el).scale))
    .toBe("none");
  const hovered = await header.boundingBox();
  expect(Math.abs(hovered!.x - before!.x)).toBeLessThan(1);
  await header.focus();
  await header.press("ArrowRight");
  await header.press("Shift+ArrowDown");
  await page.getByRole("button", { name: "保存工程", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "工程已保存" }),
  ).toBeVisible();
  const moved = (await saved(page, trip.branch_id)).graph.nodes.find(
    (n: { id: string }) => n.id === "brief",
  );
  expect(moved.x).toBe(original.x + 10);
  expect(moved.y).toBe(original.y + 40);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  const position = await card.evaluate((el) => ({
    x: parseFloat((el as HTMLElement).style.left),
    y: parseFloat((el as HTMLElement).style.top),
  }));
  expect(position).toEqual({ x: original.x + 10, y: original.y });
});

test("端口点击和取消立即反馈，运行快照仍可检查但不能移动或改线", async ({
  page,
}) => {
  await start(page);
  await page.getByRole("button", { name: "聚焦节点", exact: true }).click();
  const output = page.getByRole("button", {
    name: "旅行需求 输出 文本",
    exact: true,
  });
  await output.focus();
  await output.press("Space");
  await expect(output).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".studio-canvas-footer")).toContainText(
    "已选择输出端口",
  );
  await output.press("Escape");
  await expect(output).toHaveAttribute("aria-pressed", "false");
  await execute(page);
  await page
    .getByRole("button", { name: "查看运行时工程", exact: true })
    .click();
  await expect(
    inspector(page).getByLabel("节点名称", { exact: true }),
  ).toBeDisabled();
  await expect(output).toBeDisabled();
  await page.getByRole("button", { name: "聚焦节点", exact: true }).click();
  const header = page.getByRole("button", {
    name: "选择节点 旅行需求",
    exact: true,
  });
  const card = page.locator('[data-node="brief"]');
  const position = await card.getAttribute("style");
  await header.focus();
  await header.press("ArrowRight");
  expect(await card.getAttribute("style")).toBe(position);
});

test("保存等待与失败反馈靠近入口，草稿可继续保存，减少动画仍有静态反馈", async ({
  page,
}) => {
  await start(page);
  const input = inspector(page).getByLabel("节点输入内容", { exact: true });
  await input.fill("等待与失败后仍保留的需求");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/backend/api/branches/*/studio", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    await gate;
    await route.fulfill({
      status: 503,
      json: { error: { code: "UNAVAILABLE", message: "验收用保存失败" } },
    });
  });
  await page.getByRole("button", { name: "保存工程", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "正在保存…", exact: true }),
  ).toHaveAttribute("aria-busy", "true");
  await expect(input).toBeDisabled();
  release();
  const failure = page.getByRole("alert").filter({ hasText: "验收用保存失败" });
  await expect(failure).toBeInViewport();
  await expect(input).toHaveValue("等待与失败后仍保留的需求");
  await page.unroute("**/api/backend/api/branches/*/studio");
  await page.getByRole("button", { name: "保存工程", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "工程已保存" }),
  ).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  const canvasButton = navigation(page).getByRole("button", {
    name: "画布",
    exact: true,
  });
  await canvasButton.hover();
  expect(
    await canvasButton.evaluate((el) => getComputedStyle(el).translate),
  ).toBe("none");
});
