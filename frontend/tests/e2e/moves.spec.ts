import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const prompt = (page: Page) => page.getByLabel("待补充的信息", { exact: true });
const sendButton = (page: Page) =>
  page.getByRole("button", { name: "发送", exact: true });
const applyButton = (page: Page) =>
  prompt(page).getByRole("button", { name: "应用两天调整", exact: true });

async function openTrip(page: Page, options = "") {
  const seed = await (
    await page.request.post(`${proxy}/test/moves${options}`)
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

test("已付款景点从时间轴跨天搬移，刷新应用后原位更正和账本保留付款日", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openTrip(page);
  const original = await latest(page);
  await page.getByRole("button", { name: "第 2 天", exact: true }).click();
  await page
    .getByRole("button", { name: "将公园漫步移到第1天", exact: true })
    .click();
  await expect(
    prompt(page).getByLabel("第2天变化", { exact: true }),
  ).toContainText("移至第1天");
  await expect(
    prompt(page).getByLabel("第1天变化", { exact: true }),
  ).toContainText("来自第2天");
  expect((await latest(page)).versions).toHaveLength(1);
  await page.reload();
  await expect(applyButton(page)).toBeVisible();
  await applyButton(page).click();
  await expect(prompt(page)).toHaveCount(0);
  await expect(page.getByLabel("两天联动说明", { exact: true })).toContainText(
    "跨天搬移",
  );
  const applied = await latest(page);
  expect(applied.versions).toHaveLength(2);
  expect(applied.detail.itinerary.expenses).toEqual(
    original.detail.itinerary.expenses,
  );
  expect(applied.detail.itinerary.days[2]).toEqual(
    original.detail.itinerary.days[2],
  );
  expect(
    applied.detail.itinerary.days[0].activities.some(
      (a: { id: string }) => a.id === "park",
    ),
  ).toBe(true);
  expect(
    applied.detail.itinerary.days[1].activities.some(
      (a: { id: string }) => a.id === "park",
    ),
  ).toBe(false);
  const row = page.getByLabel("费用项目：公园门票", { exact: true });
  await expect(row).toContainText("第1天");
  await row.getByRole("button", { name: "更正实际总额", exact: true }).click();
  await row.getByRole("textbox").fill("18");
  await row.getByRole("button", { name: "保存实际总额", exact: true }).click();
  await expect(row.getByRole("textbox")).toHaveCount(0);
  const afterBudget = await latest(page);
  expect(afterBudget.detail.itinerary.expenses[0]).toMatchObject({
    day: 2,
    activity_id: "park",
    amount_cents: 1800,
    budget_item_id: "stay-park",
  });
  await page.getByText("消费明细、图片凭证与退款报销", { exact: true }).click();
  const ledger = page.getByLabel("旅行记账", { exact: true });
  await ledger.getByText("消费明细（1 笔）", { exact: true }).click();
  await expect(ledger.getByLabel("消费明细", { exact: true })).toContainText(
    "付款第 2 天",
  );
  await expect(ledger.getByLabel("消费明细", { exact: true })).toContainText(
    "关联行程第 1 天",
  );
  await ledger
    .getByRole("button", { name: "更正预付公园门票", exact: true })
    .click();
  const form = ledger.getByRole("form", { name: "更正消费", exact: true });
  await expect(
    form.getByRole("combobox", { name: "付款日期", exact: true }),
  ).toHaveValue("2");
  await form.getByText("关联停留点与备注", { exact: true }).click();
  await expect(
    form.getByRole("combobox", { name: "关联活动", exact: true }),
  ).toHaveValue("park");
  await form.getByLabel("实际金额（元，全团）", { exact: true }).fill("17.50");
  await form.getByRole("button", { name: "保存更正", exact: true }).click();
  await expect(form).toHaveCount(0);
  const saved = await latest(page);
  expect(saved.detail.itinerary.expenses[0]).toMatchObject({
    day: 2,
    activity_id: "park",
    amount_cents: 1750,
  });
  const exported = await page.request.get(
    `${proxy}/api/versions/${saved.detail.version.id}/export?format=html`,
  );
  expect(await exported.text()).toContain("付款日：第 2 天");
  expect(await exported.text()).toContain("关联行程：第 1 天");
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

test("同名景点按实际站点选择，取消不保存，应用断流刷新重试仅保存一次", async ({
  page,
}) => {
  await openTrip(page, "?ambiguous=true");
  const original = await latest(page);
  await send(page, "把第2天公园漫步移到第1天");
  const choices = () =>
    prompt(page).getByRole("group", { name: "搬移景点选择", exact: true });
  await expect(choices()).toBeVisible();
  await page.reload();
  await choices()
    .getByRole("button", { name: /另一座公园/ })
    .click();
  await expect(applyButton(page)).toBeVisible();
  await prompt(page)
    .getByRole("button", { name: "取消这次操作", exact: true })
    .click();
  await expect(prompt(page)).toHaveCount(0);
  expect((await latest(page)).detail.itinerary).toEqual(
    original.detail.itinerary,
  );
  await send(page, "把第2天公园漫步移到第1天");
  await choices()
    .getByRole("button", { name: /另一座公园/ })
    .click();
  await expect(applyButton(page)).toBeVisible();
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
  const saved = await latest(page);
  expect(saved.versions).toHaveLength(2);
  expect(
    saved.detail.itinerary.days[0].activities.some(
      (a: { id: string }) => a.id === "park-other",
    ),
  ).toBe(true);
  expect(
    saved.detail.itinerary.days[1].activities.some(
      (a: { id: string }) => a.id === "park",
    ),
  ).toBe(true);
  expect(saved.detail.itinerary.expenses).toEqual(
    original.detail.itinerary.expenses,
  );
});

test("预约不自动换天，搬移影响固定项目前交通时不能应用", async ({ page }) => {
  await openTrip(page, "?booked=true");
  const original = await latest(page);
  await send(page, "把第2天预约展览移到第1天");
  await expect(
    page.getByText(/这个项目已有预约或固定时刻/).last(),
  ).toBeVisible();
  await expect(applyButton(page)).toHaveCount(0);
  await send(page, "把第2天公园漫步移到第1天");
  await expect(
    prompt(page).getByLabel("两天调整冲突", { exact: true }),
  ).toContainText("未核实的交通耗时");
  await expect(applyButton(page)).toHaveCount(0);
  await prompt(page)
    .getByRole("button", { name: "取消这次操作", exact: true })
    .click();
  await expect(prompt(page)).toHaveCount(0);
  expect((await latest(page)).versions).toHaveLength(1);
  expect((await latest(page)).detail.itinerary).toEqual(
    original.detail.itinerary,
  );
});

test("未知付款日不因景点搬移或预算实付更正被填成新行程日", async ({ page }) => {
  await openTrip(page, "?payment_unknown=true");
  await send(page, "把第2天公园漫步移到第1天");
  await applyButton(page).click();
  await expect(prompt(page)).toHaveCount(0);
  const row = page.getByLabel("费用项目：公园门票", { exact: true });
  await row.getByRole("button", { name: "更正实际总额", exact: true }).click();
  await row.getByRole("textbox").fill("18");
  await row.getByRole("button", { name: "保存实际总额", exact: true }).click();
  await expect(row.getByRole("textbox")).toHaveCount(0);
  const saved = await latest(page);
  expect(saved.versions).toHaveLength(3);
  expect(saved.detail.itinerary.expenses[0]).toMatchObject({
    day: null,
    activity_id: "park",
    amount_cents: 1800,
  });
  expect(
    saved.detail.budget_overview.rows.find(
      (r: { id: string }) => r.id === "stay-park",
    ).day,
  ).toBe(1);
});
