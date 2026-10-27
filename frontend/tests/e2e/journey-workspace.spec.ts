import { test, expect, type Page } from "playwright/test";

const proxy = "/api/backend";
async function seed(page: Page) {
  return (await page.request.post(`${proxy}/test/moves`)).json();
}
async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  return (
    await page.request.get(
      `${proxy}/api/versions/${session.versions[bid].at(-1).id}`,
    )
  ).json();
}
const navigation = (page: Page, mobile: boolean) =>
  page.getByRole("navigation", {
    name: mobile ? "旅行快捷导航" : "行程工具",
    exact: true,
  });
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("已有行程电脑对话宽于路线，手机先见今日且消息载入不抢走页面", async ({
  page,
  isMobile,
}, info) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  await expect(
    page.getByRole("region", { name: "当前站点", exact: true }),
  ).toBeVisible();
  const chat = await page.locator(".journey-chat-column").boundingBox(),
    route = await page.locator(".journey-route-column").boundingBox();
  expect(chat).not.toBeNull();
  expect(route).not.toBeNull();
  await expect(page.getByLabel("对话消息", { exact: true })).toContainText(
    "这份行程已经保存",
  );
  if (isMobile) {
    expect(route!.y).toBeLessThan(chat!.y);
    expect(await page.evaluate(() => scrollY)).toBeLessThan(80);
    await expect(navigation(page, true)).toBeVisible();
    await expect(
      page
        .getByRole("region", { name: "当前站点", exact: true })
        .getByRole("button", { name: "标记完成", exact: true }),
    ).toBeInViewport();
  } else {
    expect(chat!.width).toBeGreaterThan(route!.width);
    expect(chat!.x).toBeLessThan(route!.x);
  }
  await page.screenshot({
    path: info.outputPath("workspace-initial.png"),
    fullPage: false,
  });
  await page.setViewportSize({ width: isMobile ? 320 : 980, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  if (!isMobile)
    expect(
      (await page.locator(".journey-chat-column").boundingBox())!.width,
    ).toBeGreaterThan(
      (await page.locator(".journey-route-column").boundingBox())!.width,
    );
});

test("快捷入口不卸载对话与费用草稿，地图与费用可直接到达", async ({
  page,
  isMobile,
}, info) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  const draft = page.getByRole("textbox", {
    name: "旅行对话输入",
    exact: true,
  });
  await draft.fill("还没发出的旅行想法");
  const writes: string[] = [];
  page.on("request", (request) => {
    if (
      request.url().includes("/api/backend/api/") &&
      request.method() !== "GET"
    )
      writes.push(request.method());
  });
  const nav = navigation(page, !!isMobile);
  await nav.getByRole("button", { name: "费用", exact: true }).click();
  const budget = page.getByLabel("费用与预算", { exact: true });
  const row = budget.getByLabel(/费用项目：/).first();
  await row
    .getByRole("button", { name: /^(填实际总额|更正实际总额)$/ })
    .click();
  const form = row.getByRole("form");
  await form.getByRole("textbox").fill("17.25");
  await nav.getByRole("button", { name: "地图", exact: true }).click();
  await expect(page.locator("#journey-map")).toBeInViewport();
  await nav.getByRole("button", { name: "费用", exact: true }).click();
  await expect(form.getByRole("textbox")).toHaveValue("17.25");
  if (isMobile) {
    await nav.getByRole("button", { name: "对话", exact: true }).click();
    await expect(draft).toBeFocused();
  }
  await expect(draft).toHaveValue("还没发出的旅行想法");
  expect(writes).toEqual([]);
  await page.screenshot({
    path: info.outputPath("workspace-drafts.png"),
    fullPage: false,
  });
});

test("旅行日期命中今天优先当天，完成后转向下一站且实际账目保留", async ({
  page,
}) => {
  const trip = await seed(page),
    before = await latest(page, trip.session_id, trip.branch_id);
  const departure = await page.evaluate(() => {
    const date = new Date();
    date.setDate(date.getDate() - 1);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  });
  await page.route("**/api/backend/api/versions/*", async (route) => {
    const data = await (await route.fetch()).json();
    data.requirements.departure_date = departure;
    await route.fulfill({ json: data });
  });
  await page.goto(`/chat/${trip.session_id}`);
  await expect(
    page.getByRole("button", { name: "第 2 天", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  const current = page.getByRole("region", { name: "当前站点", exact: true });
  await expect(current).toContainText("示例公园");
  await current.getByRole("button", { name: "标记完成", exact: true }).click();
  await expect(current).toContainText("示例展览馆");
  const after = await latest(page, trip.session_id, trip.branch_id);
  expect(after.itinerary.days[1].activities[0].progress).toBe("completed");
  expect(after.itinerary.expenses).toEqual(before.itinerary.expenses);
});

test("P1 横图竖图与方图均固定缩略图尺寸，完整查看可关闭并恢复焦点", async ({
  page,
}) => {
  const trip = await seed(page);
  let source = "";
  await page.route(
    "**/api/backend/api/versions/*/activities/*/photos",
    (route) => route.fulfill({ json: { photos: [source] } }),
  );
  for (const [w, h] of [
    [1600, 160],
    [160, 1600],
    [640, 640],
  ]) {
    source = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="100%" height="100%" fill="#355744"/></svg>`)}`;
    await page.goto(`/chat/${trip.session_id}`);
    await page.getByRole("button", { name: "第 2 天", exact: true }).click();
    const card = page
      .getByLabel("当天行程", { exact: true })
      .locator("[data-activity-card]")
      .first();
    await card.scrollIntoViewIfNeeded();
    const photo = card.getByRole("button", {
      name: "查看示例公园地点参考图",
      exact: true,
    });
    await expect(photo).toBeVisible();
    await expect(photo.locator("img")).toHaveJSProperty("naturalWidth", w);
    expect(Math.round((await photo.boundingBox())!.width)).toBe(64);
    expect(Math.round((await photo.boundingBox())!.height)).toBe(56);
    expect(await card.locator("button button").count()).toBe(0);
    await photo.click();
    const dialog = page.getByRole("dialog", {
      name: "示例公园完整参考图",
      exact: true,
    });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("img")).toHaveJSProperty("naturalHeight", h);
    await expect(dialog.getByRole("img")).toHaveCSS("object-fit", "contain");
    expect(
      (await dialog.getByRole("img").boundingBox())!.height,
    ).toBeLessThanOrEqual((await page.evaluate(() => innerHeight)) * 0.62 + 1);
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(photo).toBeFocused();
  }
});

test("参考图获取失败或图片损坏回落文字，不留下大块空白", async ({ page }) => {
  const trip = await seed(page);
  let broken = false;
  await page.route(
    "**/api/backend/api/versions/*/activities/*/photos",
    (route) =>
      broken
        ? route.fulfill({ json: { photos: ["data:image/png;base64,broken"] } })
        : route.fulfill({
            status: 503,
            json: {
              error: { message: "隔离图片失败", code: "MAP_UNAVAILABLE" },
            },
          }),
  );
  for (const damaged of [false, true]) {
    broken = damaged;
    await page.goto(`/chat/${trip.session_id}`);
    await page.getByRole("button", { name: "第 2 天", exact: true }).click();
    const card = page
      .getByLabel("当天行程", { exact: true })
      .locator("[data-activity-card]")
      .first();
    await card.scrollIntoViewIfNeeded();
    await expect(card.locator(".journey-photo")).toHaveAttribute(
      "data-state",
      "unavailable",
    );
    await expect(card.getByRole("button", { name: /公园漫步/ })).toBeVisible();
    await expect(card.locator(".journey-photo-trigger")).toHaveCount(0);
    expect(
      parseFloat(
        await card
          .locator(".journey-stop-select")
          .evaluate((el) => getComputedStyle(el).paddingRight),
      ),
    ).toBeLessThan(80);
  }
});

test("没有每日行程时仍以对话开始，不把新规划藏到今日空页面之后", async ({
  page,
  isMobile,
}) => {
  const result = await (
    await page.request.post(`${proxy}/api/sessions`, {
      data: { title: "工作区空状态验收" },
    })
  ).json();
  await page.goto(`/chat/${result.session.id}`);
  await expect(
    page.getByRole("textbox", { name: "旅行对话输入", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".journey-workspace")).toHaveAttribute(
    "data-has-itinerary",
    "false",
  );
  if (isMobile) {
    const chat = await page.locator(".journey-chat-column").boundingBox(),
      empty = await page.locator(".journey-route-column").boundingBox();
    expect(chat!.y).toBeLessThan(empty!.y);
    await expect(
      navigation(page, true).getByRole("button", { name: "地图", exact: true }),
    ).toBeDisabled();
  }
});
