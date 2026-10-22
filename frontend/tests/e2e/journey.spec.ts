import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";

async function send(page: Page, text: string) {
  await page.getByRole("textbox").first().fill(text);
  const finished = page.waitForResponse((response) =>
    response.url().endsWith("/api/chat/stream"),
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const response = await finished;
  expect(await response.text()).toContain("event: done");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
}

test.beforeEach(async ({ page }) => {
  // 测试不依赖外网；图片、地图等额外请求不能触达第三方。
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("推荐、生成、旅中记录、修订、刷新与导出形成完整流程", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "新建旅行", exact: true }).click();
  await expect(page).toHaveURL(/\/chat\//);
  await send(page, "从上海出发玩三天，少走路，请推荐城市");
  await expect(page.getByText(/还需要知道：预算大约多少/)).toBeVisible();
  await send(page, "预算2000元");
  await expect(page.getByText(/1\. 北京：博物馆与城市漫步/)).toBeVisible();
  await expect(page.getByText("还需要推荐吗？", { exact: true })).toHaveCount(
    0,
  );
  await send(page, "选北京");
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  await send(
    page,
    "第1天第1站已完成，午餐花了86.35元，第2天第2站已预约16:00，停留60分钟",
  );
  await send(page, "第一天轻松一点");
  const sid = page.url().split("/").at(-1);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const bid = session.branches[0].id;
  const versions = session.versions[bid];
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
  ).json();
  expect(detail.itinerary.days[0].activities[0].progress).toBe("completed");
  expect(detail.itinerary.expenses).toHaveLength(1);
  expect(detail.itinerary.expenses[0].amount_cents).toBe(8635);
  expect(detail.itinerary.days[1].activities[1].booking.start_time).toBe(
    "16:00",
  );
  await page.reload();
  await expect(page.getByLabel("费用与预算", { exact: true })).toBeVisible();
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  await page.getByText("完整计划书、导出与版本历史", { exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出 HTML", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.html$/);
  const exported = await page.request.get(
    `${proxy}/api/versions/${detail.version.id}/export?format=html`,
  );
  expect(await exported.text()).toContain("86.35");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  expect(errors).toEqual([]);
});

test("旧文字路书可整理，地图未配置时仍能编辑费用", async ({
  page,
  request,
}) => {
  const legacy = await (await request.post(`${proxy}/test/legacy`)).json();
  await page.goto(`/chat/${legacy.session_id}`);
  await page
    .getByRole("button", { name: "自动整理完整线路", exact: true })
    .click();
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  await expect(
    page.getByText("具体地点定位后将自动显示高德底图。", { exact: true }),
  ).toBeVisible();
  const item = page
    .getByLabel("费用与预算", { exact: true })
    .getByLabel(/费用项目：/)
    .first();
  await item.getByRole("button", { name: "填实际总额", exact: true }).click();
  const form = item.getByRole("form", { name: "填写实际总额", exact: true });
  await expect(form.getByRole("textbox")).toBeFocused();
  await form.getByRole("textbox").fill("99.50");
  await form.getByRole("button", { name: "保存实际总额", exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect(
    item.getByRole("button", { name: "更正实际总额", exact: true }),
  ).toBeVisible();
});
