import { test, expect, type Page } from "playwright/test";

const proxy = "/api/backend";
async function seed(page: Page) {
  return (await page.request.post(`${proxy}/test/moves`)).json();
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("全局导航标注页面与旅行上下文，画布按钮触屏可点击", async ({ page }) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  const nav = page.getByRole("navigation", { name: "主导航", exact: true });
  await expect(
    nav.getByRole("link", { name: "会话", exact: true }),
  ).toHaveAttribute("aria-current", "location");
  await expect(nav.locator("[aria-current]")).toHaveCount(1);
  const rename = page.getByRole("button", {
    name: "重命名这个旅行",
    exact: true,
  });
  expect((await rename.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  expect((await rename.boundingBox())!.width).toBeGreaterThanOrEqual(40);
  await page.goto(`/workflow/${trip.session_id}?branch=${trip.branch_id}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "双方案比较" })
    .click();
  for (const name of ["缩小画布", "放大画布", "适应画布"]) {
    const control = page.getByRole("button", { name, exact: true });
    const box = await control.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(40);
    await control.click();
  }
  await page.goto("/help");
  await expect(
    nav.getByRole("link", { name: "帮助", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(nav.locator("[aria-current]")).toHaveCount(1);
});

test("减少动画偏好作用于对话自动滚动和共享加载图标", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    const original = Element.prototype.scrollTo;
    Element.prototype.scrollTo = function (options) {
      if (typeof options === "object")
        document.documentElement.dataset.lastScrollBehavior = String(
          options.behavior,
        );
      Reflect.apply(original, this, [options]);
    };
  });
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  await expect(
    page.getByRole("textbox", { name: "旅行对话输入" }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute(
    "data-last-scroll-behavior",
    "instant",
  );
  await page
    .getByRole("button", { name: "重命名这个旅行", exact: true })
    .click();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/api/backend/api/sessions/${trip.session_id}`,
    async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      await gate;
      await route.fulfill({
        status: 503,
        json: { error: { code: "BACKEND_UNREACHABLE", message: "隔离失败" } },
      });
    },
  );
  const form = page.getByRole("form", { name: "修改旅行名称" });
  await form.getByRole("textbox").fill("减少动画验收");
  await form.getByRole("button", { name: "保存名称", exact: true }).click();
  const waiting = form.getByRole("button", { name: "正在保存…", exact: true });
  await expect(waiting).toHaveAttribute("aria-busy", "true");
  expect(
    await waiting
      .locator("svg")
      .evaluate((element) =>
        parseFloat(getComputedStyle(element).animationDuration),
      ),
  ).toBeLessThan(0.02);
  release();
  await expect(form.getByRole("alert")).toContainText("隔离失败");
});

test("长时间等待有说明且不自动重发，超时后输入与原位重试保留", async ({
  page,
}) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  await page
    .getByRole("button", { name: "重命名这个旅行", exact: true })
    .click();
  await page.clock.install();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requests = 0;
  await page.route(
    `**/api/backend/api/sessions/${trip.session_id}`,
    async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      requests++;
      await gate;
      await route.fulfill({
        status: 504,
        json: {
          error: {
            code: "BACKEND_TIMEOUT",
            message:
              "等待响应超时，操作结果尚未确认。请先核对当前内容，再重试原操作。",
          },
        },
      });
    },
  );
  const form = page.getByRole("form", { name: "修改旅行名称" });
  await form.getByRole("textbox").fill("等待中的旅行名称");
  await form.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect.poll(() => requests).toBe(1);
  await page.clock.fastForward(12_100);
  await expect(
    form.getByText("仍在等待服务响应，请勿重复提交。", { exact: true }),
  ).toBeVisible();
  expect(requests).toBe(1);
  release();
  await expect(form.getByRole("alert")).toContainText("操作结果尚未确认");
  await expect(form.getByRole("textbox")).toHaveValue("等待中的旅行名称");
  await expect(
    form.getByText("仍在等待服务响应，请勿重复提交。", { exact: true }),
  ).toHaveCount(0);
  await expect(
    form.getByRole("button", { name: "保存名称", exact: true }),
  ).toBeEnabled();
});

test("设置已保存但读取失败时明确区分，重试只读取不会重复写入", async ({
  page,
}) => {
  let writes = 0;
  let failRead = false;
  await page.route("**/api/backend/api/system/settings", async (route) => {
    if (route.request().method() === "GET") {
      if (failRead) {
        failRead = false;
        return route.fulfill({
          status: 503,
          json: {
            error: { code: "BACKEND_UNREACHABLE", message: "状态读取暂时失败" },
          },
        });
      }
      return route.continue();
    }
    writes++;
    failRead = true;
    return route.fulfill({
      json: {
        saved: true,
        changed: [],
        message: "设置已确认保存",
        reloaded: true,
      },
    });
  });
  await page.route("**/api/backend/api/system/models", (route) =>
    route.fulfill({ json: { models: [], message: "隔离配置" } }),
  );
  await page.goto("/settings");
  const actions = page.getByRole("region", {
    name: "设置保存操作",
    exact: true,
  });
  await actions.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(actions.getByRole("alert")).toContainText(
    "设置已保存，但配置状态刷新失败",
  );
  await expect(actions.getByRole("status")).toContainText("设置已确认保存");
  await actions.getByRole("button", { name: "重试", exact: true }).click();
  await expect(actions.getByRole("alert")).toHaveCount(0);
  expect(writes).toBe(1);
});

test("写入断线不误报未保存或成功，改名草稿可继续", async ({ page }) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  await page
    .getByRole("button", { name: "重命名这个旅行", exact: true })
    .click();
  await page.route(`**/api/backend/api/sessions/${trip.session_id}`, (route) =>
    route.request().method() === "PATCH"
      ? route.abort("failed")
      : route.continue(),
  );
  const form = page.getByRole("form", { name: "修改旅行名称" });
  await form.getByRole("textbox").fill("断线时仍保留的名称");
  await form.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("操作结果尚未确认");
  await expect(form.getByRole("textbox")).toHaveValue("断线时仍保留的名称");
  await expect(form.getByRole("textbox")).toBeFocused();
});
