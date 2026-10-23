import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const input = (page: Page) =>
  page.getByRole("textbox", { name: "旅行对话输入", exact: true });
const prompt = (page: Page) => page.getByLabel("待补充的信息", { exact: true });
const sendButton = (page: Page) =>
  page.getByRole("button", { name: "发送", exact: true });

async function send(page: Page, text: string) {
  await input(page).fill(text);
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
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (r) =>
    r.abort(),
  );
  const seed = await (await page.request.post(`${proxy}/test/expenses`)).json();
  await page.goto(`/chat/${seed.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
});

test("同日同名同金额消费可刷新后精确更正，保留草稿并同步预算和导出", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await send(page, "第1天咖啡消费改成18.50元");
  await expect(
    prompt(page).getByRole("group", { name: "消费记录选择", exact: true }),
  ).toBeVisible();
  await expect(
    prompt(page).getByRole("button", { name: /20.00元.*上午在餐厅/ }),
  ).toBeVisible();
  expect((await latest(page)).versions).toHaveLength(1);
  await page.reload();
  const second = prompt(page).getByRole("button", {
    name: /20.00元.*下午在酒店/,
  });
  await expect(second).toBeVisible();
  await input(page).fill("这是还没发出的旅行想法");
  await second.click();
  await expect(sendButton(page)).toBeEnabled();
  await expect(prompt(page)).toHaveCount(0);
  await expect(input(page)).toHaveValue("这是还没发出的旅行想法");
  const { versions, detail } = await latest(page);
  expect(versions).toHaveLength(2);
  expect(
    detail.itinerary.expenses.map(
      (e: { amount_cents: number }) => e.amount_cents,
    ),
  ).toEqual([2000, 1850]);
  expect(detail.itinerary.expenses[1]).toMatchObject({
    id: "fixture-coffee-2",
    notes: "下午在酒店",
    activity_id: "hotel",
    budget_item_id: "stay-lunch",
    settles_budget: true,
  });
  expect(
    detail.budget_overview.rows.find(
      (r: { id: string }) => r.id === "stay-lunch",
    ).actual_cents,
  ).toBe(3850);
  const exported = await page.request.get(
    `${proxy}/api/versions/${detail.version.id}/export?format=html`,
  );
  expect(await exported.text()).toContain("18.50");
  expect(await exported.text()).toContain("下午在酒店");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  expect(errors).toEqual([]);
});

test("选择回款消费后断流，刷新重试不重复记录，也不误删另一笔", async ({
  page,
}) => {
  await send(page, "第1天咖啡退款到账5元");
  let truncate = true;
  await page.route("**/api/backend/api/chat/stream", async (route) => {
    if (!truncate) return route.continue();
    truncate = false;
    const response = await route.fetch();
    expect(await response.text()).toContain("event: done");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: custom\ndata: {"type":"progress","message":"处理中"}\n\n',
    });
  });
  await prompt(page)
    .getByRole("button", { name: /下午在酒店/ })
    .click();
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
  expect(saved.detail.itinerary.adjustments).toHaveLength(1);
  expect(saved.detail.itinerary.adjustments[0]).toMatchObject({
    expense_id: "fixture-coffee-2",
    amount_cents: 500,
  });
  expect(saved.detail.itinerary.ledger_summary.refunded_cents).toBe(500);
  await send(page, "删除第1天咖啡消费");
  await prompt(page)
    .getByRole("button", { name: /下午在酒店/ })
    .click();
  await expect(sendButton(page)).toBeEnabled();
  expect((await latest(page)).versions).toHaveLength(2);
  await expect(page.getByText(/已有退款或报销记录/).last()).toBeVisible();
  await send(page, "删除第1天咖啡消费");
  await prompt(page)
    .getByRole("button", { name: /上午在餐厅/ })
    .click();
  await expect(sendButton(page)).toBeEnabled();
  const remaining = await latest(page);
  expect(remaining.versions).toHaveLength(3);
  expect(remaining.detail.itinerary.expenses).toHaveLength(1);
  expect(remaining.detail.itinerary.expenses[0].id).toBe("fixture-coffee-2");
  expect(remaining.detail.itinerary.adjustments).toHaveLength(1);
});

test("多项操作中的同名消费逐个确认，中途取消不留下部分修改", async ({
  page,
}) => {
  await send(page, "第1天咖啡消费改成18元；咖啡退款到账5元");
  await prompt(page)
    .getByRole("button", { name: /下午在酒店/ })
    .click();
  await expect(
    prompt(page).getByRole("button", { name: /18.00元.*下午在酒店/ }),
  ).toBeVisible();
  expect((await latest(page)).versions).toHaveLength(1);
  await page.reload();
  await prompt(page)
    .getByRole("button", { name: "取消这次操作", exact: true })
    .click();
  await expect(prompt(page)).toHaveCount(0);
  const cancelled = await latest(page);
  expect(cancelled.versions).toHaveLength(1);
  expect(cancelled.detail.itinerary.expenses[1].amount_cents).toBe(2000);
  expect(cancelled.detail.itinerary.adjustments).toHaveLength(0);
  await send(page, "第1天咖啡消费改成18元；咖啡退款到账5元");
  await prompt(page)
    .getByRole("button", { name: /下午在酒店/ })
    .click();
  await expect(
    prompt(page).getByRole("button", { name: /18.00元.*下午在酒店/ }),
  ).toBeVisible();
  await prompt(page)
    .getByRole("button", { name: /上午在餐厅/ })
    .click();
  await expect(prompt(page)).toHaveCount(0);
  const saved = await latest(page);
  expect(saved.versions).toHaveLength(2);
  expect(saved.detail.itinerary.expenses[1].amount_cents).toBe(1800);
  expect(saved.detail.itinerary.adjustments[0].expense_id).toBe(
    "fixture-coffee-1",
  );
});
