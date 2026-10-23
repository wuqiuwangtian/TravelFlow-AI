import { expect, test, type Page } from "playwright/test";
const proxy = "/api/backend";
const editor = (page: Page) =>
  page.getByRole("region", { name: "局部重跑", exact: true });
const preview = (page: Page) =>
  page.getByRole("region", { name: "路线优化预览", exact: true });
async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[bid];
  return {
    versions,
    detail: await (
      await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
    ).json(),
  };
}
async function generate(page: Page) {
  const done = page.waitForResponse(
    (r) =>
      r.url().endsWith("/workflow/planner-preview") &&
      r.request().method() === "POST",
  );
  await editor(page)
    .getByRole("button", { name: "生成优化预览", exact: true })
    .click();
  return done;
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("单日优化预览可刷新恢复，应用回复丢失可重试且保留其他天与实际记录", async ({
  page,
}, info) => {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  const before = await latest(page, seed.session_id, seed.branch_id);
  await page.goto(
    `/workflow/${seed.session_id}?branch=${seed.branch_id}&view=process`,
  );
  await editor(page).getByLabel("优化日期", { exact: true }).selectOption("2");
  await editor(page)
    .getByLabel("市内默认交通", { exact: true })
    .selectOption("walking");
  expect((await generate(page)).ok()).toBe(true);
  await expect(preview(page)).toContainText("应用前预览");
  await expect(preview(page)).toContainText("固定保留");
  const unchanged = await latest(page, seed.session_id, seed.branch_id);
  expect(unchanged).toEqual(before);
  await page.reload();
  await expect(preview(page)).toBeVisible();
  await expect(
    editor(page).getByLabel("优化日期", { exact: true }),
  ).toHaveValue("2");
  await expect(
    editor(page).getByLabel("市内默认交通", { exact: true }),
  ).toHaveValue("walking");
  await editor(page)
    .getByLabel("优化要求", { exact: true })
    .fill("保留全部地点，减少折返");
  await expect(
    preview(page).getByRole("button", { name: "应用本次优化", exact: true }),
  ).toBeDisabled();
  expect((await generate(page)).ok()).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await page.screenshot({
    path: info.outputPath("day-route-preview.png"),
    fullPage: true,
  });
  let dropped = false;
  await page.route("**/workflow/planner-preview/*/apply", async (route) => {
    if (!dropped) {
      dropped = true;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      await route.abort();
    } else await route.continue();
  });
  await preview(page)
    .getByRole("button", { name: "应用本次优化", exact: true })
    .click();
  await expect(
    editor(page).getByRole("alert").filter({ hasText: "网络连接暂时中断" }),
  ).toBeVisible();
  await preview(page)
    .getByRole("button", { name: "应用本次优化", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "单日优化已保存为新版本" }),
  ).toBeVisible();
  const after = await latest(page, seed.session_id, seed.branch_id);
  expect(after.versions).toHaveLength(before.versions.length + 1);
  expect(after.detail.itinerary.days[0]).toEqual(
    before.detail.itinerary.days[0],
  );
  expect(after.detail.itinerary.days[2]).toEqual(
    before.detail.itinerary.days[2],
  );
  expect(after.detail.itinerary.expenses).toEqual(
    before.detail.itinerary.expenses,
  );
  expect(after.detail.itinerary.budget_items).toEqual(
    before.detail.itinerary.budget_items,
  );
  expect(
    after.detail.itinerary.days[1].activities.map((a: { id: string }) => a.id),
  ).toEqual(["museum", "park", "hotel-two"]);
  const park = after.detail.itinerary.days[1].activities.find(
    (a: { id: string }) => a.id === "park",
  );
  expect(park.cost_source).toBe("manual");
  expect(park.place).toEqual(
    before.detail.itinerary.days[1].activities[0].place,
  );
  await page
    .getByLabel("查看版本", { exact: true })
    .selectOption(before.versions.at(-1).id);
  await expect(
    page.getByText("历史版本只读；切换“当前过程”后可优化当前方案。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(editor(page)).toHaveCount(0);
});

test("重跑失败可重试、预览可丢弃，其他窗口新消费拒绝旧预览", async ({
  page,
}) => {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(
    `/workflow/${seed.session_id}?branch=${seed.branch_id}&view=process`,
  );
  await editor(page).getByLabel("优化日期", { exact: true }).selectOption("2");
  let failed = false;
  await page.route("**/workflow/planner-preview", async (route) => {
    if (route.request().method() === "POST" && !failed) {
      failed = true;
      await route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "LLM_AUTH_FAILED",
            message: "模型鉴权失败，请检查设置",
          },
        }),
      });
    } else await route.continue();
  });
  expect((await generate(page)).status()).toBe(502);
  await expect(editor(page).getByRole("alert")).toContainText("模型鉴权失败");
  expect((await generate(page)).ok()).toBe(true);
  await preview(page)
    .getByRole("button", { name: "丢弃预览", exact: true })
    .click();
  await expect(preview(page)).toHaveCount(0);
  await page.reload();
  await expect(
    editor(page).getByRole("button", { name: "生成优化预览", exact: true }),
  ).toBeEnabled();
  await expect(preview(page)).toHaveCount(0);
  await editor(page).getByLabel("优化日期", { exact: true }).selectOption("2");
  expect((await generate(page)).ok()).toBe(true);
  const response = await page.request.post(`${proxy}/api/chat`, {
    data: {
      session_id: seed.session_id,
      branch_id: seed.branch_id,
      message: "第1天午餐花了38元",
    },
  });
  expect(response.ok()).toBe(true);
  const newest = await latest(page, seed.session_id, seed.branch_id);
  await preview(page)
    .getByRole("button", { name: "应用本次优化", exact: true })
    .click();
  await expect(editor(page).getByRole("alert")).toContainText("历史版本");
  expect(await latest(page, seed.session_id, seed.branch_id)).toEqual(newest);
  await page.getByRole("button", { name: "刷新流程", exact: true }).click();
  await expect(
    editor(page).getByRole("button", { name: "生成优化预览", exact: true }),
  ).toBeEnabled();
  await expect(preview(page)).toHaveCount(0);
});

test("预约和旅中历史保留，存在固定时间冲突的预览不能应用", async ({ page }) => {
  const seed = await (
    await page.request.post(`${proxy}/test/linked?early_booking=true`)
  ).json();
  const before = await latest(page, seed.session_id, seed.branch_id);
  await page.goto(
    `/workflow/${seed.session_id}?branch=${seed.branch_id}&view=process`,
  );
  await editor(page).getByLabel("优化日期", { exact: true }).selectOption("2");
  expect((await generate(page)).ok()).toBe(true);
  await expect(preview(page)).toContainText("暂不能应用");
  await expect(preview(page)).toContainText("预计无法准时抵达");
  await expect(
    preview(page).getByRole("button", { name: "应用本次优化", exact: true }),
  ).toBeDisabled();
  expect(await latest(page, seed.session_id, seed.branch_id)).toEqual(before);
});
