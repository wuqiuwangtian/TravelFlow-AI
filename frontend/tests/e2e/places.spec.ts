import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
async function send(page: Page, text: string) {
  await page
    .getByRole("textbox", { name: "旅行对话输入", exact: true })
    .fill(text);
  const done = page.waitForResponse((r) =>
    r.url().endsWith("/api/chat/stream"),
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect(await (await done).text()).toContain("event: done");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
}

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (r) =>
    r.abort(),
  );
  const seeded = await (await page.request.post(`${proxy}/test/places`)).json();
  await page.goto(`/chat/${seeded.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
});

test("从时间轴确认地点，刷新后选择真实候选并同步导航、预算与导出", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page
    .getByRole("button", { name: "确认第1天第1站的地点", exact: true })
    .click();
  const prompt = page.getByLabel("待补充的信息", { exact: true });
  await expect(
    prompt.getByRole("button", { name: /望京店.*望京路1号/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    prompt.getByRole("button", { name: /西单店.*西单路2号/ }),
  ).toBeVisible();
  await prompt.getByRole("button", { name: /西单店.*西单路2号/ }).click();
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
  await expect(prompt).toHaveCount(0);
  const sid = page.url().split("/").at(-1);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[session.branches[0].id];
  expect(versions).toHaveLength(2);
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
  ).json();
  expect(detail.itinerary.days[0].activities[0].place.id).toBe("west");
  expect(detail.itinerary.days[1].activities[0].place).toBeNull();
  expect(detail.itinerary.expenses).toHaveLength(1);
  expect(detail.itinerary.expenses[0].amount_cents).toBe(2000);
  expect(
    detail.budget_overview.rows.find(
      (row: { id: string }) => row.id === "stay-lunch",
    ).planned_cents,
  ).toBe(8800);
  await page
    .getByRole("button", { name: /入住酒店/ })
    .first()
    .click();
  await expect(
    page.getByRole("link", { name: "打开地点导航", exact: true }),
  ).toHaveAttribute("href", /from=116\.37%2C39\.91/);
  const exported = await page.request.get(
    `${proxy}/api/versions/${detail.version.id}/export?format=html`,
  );
  expect(await exported.text()).toContain("西单店");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  expect(errors).toEqual([]);
});

test("可先选目标站，再用区域回答，取消其他候选不产生空版本", async ({
  page,
}) => {
  await send(page, "确认待定位的地点");
  const prompt = page.getByLabel("待补充的信息", { exact: true });
  await prompt
    .getByRole("button", { name: "第2天第1站 · 晚餐", exact: true })
    .click();
  await expect(
    prompt.getByRole("group", { name: "地点候选选择", exact: true }),
  ).toBeVisible();
  await send(page, "朝阳区那家");
  await expect(prompt).toHaveCount(0);
  await page
    .getByRole("button", { name: "确认第1天第1站的地点", exact: true })
    .click();
  await prompt
    .getByRole("button", { name: "取消这次操作", exact: true })
    .click();
  await expect(prompt).toHaveCount(0);
  const sid = page.url().split("/").at(-1);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  expect(session.versions[session.branches[0].id]).toHaveLength(2);
});
