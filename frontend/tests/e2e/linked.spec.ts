import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const prompt = (page: Page) => page.getByLabel("待补充的信息", { exact: true });
const sendButton = (page: Page) =>
  page.getByRole("button", { name: "发送", exact: true });
const applyButton = (page: Page) =>
  prompt(page).getByRole("button", { name: "应用两天调整", exact: true });

async function openTrip(page: Page, options = "") {
  const seed = await (
    await page.request.post(`${proxy}/test/linked${options}`)
  ).json();
  await page.goto(`/chat/${seed.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
}

async function send(page: Page, text: string) {
  await page
    .getByRole("textbox", { name: "旅行对话输入", exact: true })
    .fill(text);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/api/chat/stream"),
  );
  await sendButton(page).click();
  expect(await (await response).text()).toContain("event: done");
  await expect(sendButton(page)).toBeEnabled();
}

async function latest(page: Page) {
  const sid = page.url().split("/").at(-1);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[session.branches[0].id];
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
  ).json();
  return { versions, detail };
}

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("晚间延误可预览两天，刷新后一次应用并同步时间轴、账本和导出", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openTrip(page);
  const original = await latest(page);
  await page
    .getByRole("button", { name: "联动调整第1天和第2天", exact: true })
    .click();
  await expect(
    prompt(page).getByLabel("第1天变化", { exact: true }),
  ).toContainText("次日 01:20");
  await expect(
    prompt(page).getByLabel("第2天变化", { exact: true }),
  ).toContainText("10:20");
  expect((await latest(page)).versions).toHaveLength(1);
  await page.reload();
  await expect(applyButton(page)).toBeVisible();
  await applyButton(page).click();
  await expect(prompt(page)).toHaveCount(0);
  await expect(page.getByLabel("两天联动说明", { exact: true })).toContainText(
    "09:50",
  );
  await page.getByRole("button", { name: "第 2 天", exact: true }).click();
  await expect(page.getByLabel("两天联动说明", { exact: true })).toContainText(
    "09:50",
  );
  const applied = await latest(page);
  expect(applied.versions).toHaveLength(2);
  expect(applied.detail.itinerary.expenses).toEqual(
    original.detail.itinerary.expenses,
  );
  expect(applied.detail.itinerary.days[2]).toEqual(
    original.detail.itinerary.days[2],
  );
  expect(applied.detail.itinerary.days[0].activities.at(-1).start_time).toBe(
    "01:20",
  );
  expect(
    applied.detail.itinerary.days[1].activities.find(
      (a: { id: string }) => a.id === "museum",
    ).booking.start_time,
  ).toBe("14:00");
  expect(
    applied.detail.budget_overview.rows.find(
      (r: { id: string }) => r.id === "stay-park",
    ).actual_cents,
  ).toBe(2000);
  const exported = await page.request.get(
    `${proxy}/api/versions/${applied.detail.version.id}/export?format=html`,
  );
  expect(await exported.text()).toContain("最近两天联动");
  expect(await exported.text()).toContain("09:50");
  const old = await (
    await page.request.get(
      `${proxy}/api/versions/${original.detail.version.id}`,
    )
  ).json();
  expect(old.itinerary).toEqual(original.detail.itinerary);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  expect(errors).toEqual([]);
});

test("赶不上次日预约时集中展示冲突，禁止应用，取消后两天均不变", async ({
  page,
}) => {
  await openTrip(page, "?early_booking=true");
  const original = await latest(page);
  await send(page, "联动调整第1天和第2天");
  await expect(
    prompt(page).getByLabel("两天调整冲突", { exact: true }),
  ).toContainText("09:00");
  await expect(applyButton(page)).toHaveCount(0);
  await send(page, "应用两天调整");
  await expect(applyButton(page)).toHaveCount(0);
  expect((await latest(page)).versions).toHaveLength(1);
  await page.reload();
  await expect(
    prompt(page).getByLabel("两天调整冲突", { exact: true }),
  ).toBeVisible();
  await prompt(page)
    .getByRole("button", { name: "取消这次操作", exact: true })
    .click();
  await expect(prompt(page)).toHaveCount(0);
  expect((await latest(page)).detail.itinerary).toEqual(
    original.detail.itinerary,
  );
});

test("可改休息与次日出发条件，应用回复丢失后重试仍只保存一版", async ({
  page,
}) => {
  await openTrip(page, "?early_booking=true");
  await send(page, "联动调整第1天和第2天，预留0小时休息，次日06:00出发");
  await expect(prompt(page)).toContainText("0小时");
  await expect(
    prompt(page).getByLabel("两天调整冲突", { exact: true }),
  ).toHaveCount(0);
  let truncate = true;
  await page.route("**/api/backend/api/chat/stream", async (route) => {
    if (!truncate) return route.continue();
    truncate = false;
    const response = await route.fetch();
    expect(await response.text()).toContain("event: done");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });
  await applyButton(page).click();
  await expect(
    page.getByLabel("消息发送未完成", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "重试这条消息", exact: true }).click();
  await expect(sendButton(page)).toBeEnabled();
  await expect(page.getByLabel("消息发送未完成", { exact: true })).toHaveCount(
    0,
  );
  const applied = await latest(page);
  expect(applied.versions).toHaveLength(2);
  expect(applied.detail.itinerary.days[1].replan_context.time).toBe("06:00");
  expect(applied.detail.itinerary.expenses).toHaveLength(1);
  expect(
    applied.detail.itinerary.days[1].activities.find(
      (a: { id: string }) => a.id === "museum",
    ).booking.start_time,
  ).toBe("09:00");
});

test("精简的可选项目在预览中明确标注，取消不会留下部分重排", async ({
  page,
}) => {
  await openTrip(page, "?optional_stop=true");
  const original = await latest(page);
  await send(page, "联动调整第1天和第2天");
  const row = prompt(page)
    .getByLabel("第2天变化", { exact: true })
    .getByRole("listitem")
    .filter({ hasText: "可选绕路公园" });
  await expect(row).toContainText("本次精简");
  await expect(prompt(page)).toContainText("未核实的交通耗时");
  await expect(applyButton(page)).toHaveCount(0);
  await prompt(page)
    .getByRole("button", { name: "取消这次操作", exact: true })
    .click();
  await expect(prompt(page)).toHaveCount(0);
  const cancelled = await latest(page);
  expect(cancelled.versions).toHaveLength(1);
  expect(cancelled.detail.itinerary).toEqual(original.detail.itinerary);
});
