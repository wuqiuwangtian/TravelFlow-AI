import { test, expect, type Page } from "playwright/test";

const preferencesUrl = "**/api/backend/api/travel-preferences";
const profile = { preferences: ["慢节奏"], revision: 1, updated_at: null };
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});
async function openPreferences(page: Page) {
  await page.route(preferencesUrl, (route) => route.fulfill({ json: profile }));
  await page.goto("/preferences");
  await expect(page.getByLabel("编辑常用偏好")).toHaveValue("慢节奏");
}

test("C1 按钮与导航的反馈可感知，触屏命中区和键盘焦点保留", async ({
  page,
  isMobile,
}) => {
  await openPreferences(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "paper");
  const save = page.getByRole("button", { name: "保存常用偏好", exact: true });
  await expect(save).toHaveCSS("border-radius", "8px");
  expect((await save.boundingBox())!.height).toBeGreaterThanOrEqual(
    isMobile ? 44 : 40,
  );
  const nav = page.getByRole("navigation", { name: "主导航", exact: true });
  const home = nav.getByRole("link", { name: "工作台", exact: true });
  await expect(home).toHaveAttribute("aria-current", "location");
  expect((await home.boundingBox())!.height).toBeGreaterThanOrEqual(
    isMobile ? 44 : 40,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  if (!isMobile) {
    const normal = await save.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );
    await save.hover();
    await page.waitForTimeout(180);
    expect(
      await save.evaluate((el) => getComputedStyle(el).backgroundColor),
    ).not.toBe(normal);
    await page.mouse.down();
    await page.waitForTimeout(120);
    expect(
      parseFloat(await save.evaluate((el) => getComputedStyle(el).scale)),
    ).toBeLessThan(1);
    await page.mouse.move(0, 0);
    await page.mouse.up();
    await home.hover();
    await page.waitForTimeout(180);
    expect(
      await home.evaluate((el) => getComputedStyle(el).translate),
    ).not.toBe("none");
  }
  await page.keyboard.press("Tab");
  await save.focus();
  expect(
    await save.evaluate((el) => el.matches(":focus-visible")),
  ).toBeTruthy();
  await expect(save).toHaveCSS("outline-width", "2px");
  await page.getByLabel("编辑常用偏好").fill("");
  await expect(save).toBeDisabled();
});

test("共享按钮改样式后等待仍阻止重复点击，失败保留输入并能重试", async ({
  page,
}) => {
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(preferencesUrl, async (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({ json: profile });
    calls++;
    if (calls === 1) {
      await gate;
      return route.fulfill({
        status: 503,
        json: {
          error: { code: "BACKEND_UNREACHABLE", message: "共享组件隔离失败" },
        },
      });
    }
    return route.fulfill({
      json: {
        preferences: route.request().postDataJSON().preferences,
        revision: 2,
        updated_at: null,
      },
    });
  });
  await page.goto("/preferences");
  const input = page.getByLabel("编辑常用偏好");
  await expect(input).toHaveValue("慢节奏");
  await input.fill("少走路，保留午休");
  const save = page.getByRole("button", { name: "保存常用偏好", exact: true });
  try {
    await save.click();
    await expect(save).toBeDisabled();
    await expect(save).toHaveAttribute("aria-busy", "true");
    await expect.poll(() => calls).toBe(1);
    await save.evaluate((button) => (button as HTMLButtonElement).click());
    expect(calls).toBe(1);
    await expect(input).toBeDisabled();
  } finally {
    release();
  }
  await expect(page.locator("main").getByRole("alert")).toContainText(
    "共享组件隔离失败",
  );
  await expect(input).toHaveValue("少走路，保留午休");
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByRole("status")).toContainText("常用偏好已保存");
  expect(calls).toBe(2);
});

test("三主题基础色保持共享按钮可读，减少动画关闭按压位移", async ({ page }) => {
  await openPreferences(page);
  const save = page.getByRole("button", { name: "保存常用偏好", exact: true });
  // Internal palette coverage; the global theme setting is a later migration.
  for (const theme of ["paper", "guide", "night"]) {
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
    }, theme);
    await page.waitForTimeout(180);
    const contrast = await save.evaluate((el) => {
      const color = getComputedStyle(el);
      const luminance = (value: string) => {
        const rgb = value
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map((v) => Number(v) / 255)
          .map((v) =>
            v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4,
          );
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      };
      const a = luminance(color.color),
        b = luminance(color.backgroundColor);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    });
    expect(contrast).toBeGreaterThanOrEqual(4.5);
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await save.hover();
  await page.mouse.down();
  try {
    await expect(save).toHaveCSS("scale", "1");
    await expect(save).toHaveCSS("translate", "none");
    expect(
      parseFloat(
        await save.evaluate((el) => getComputedStyle(el).transitionDuration),
      ),
    ).toBeLessThan(0.02);
  } finally {
    await page.mouse.move(0, 0);
    await page.mouse.up();
  }
});
