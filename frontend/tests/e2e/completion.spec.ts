import { expect, test, type Page } from "playwright/test";
const proxy = "/api/backend";
const recap = (page: Page) =>
  page.getByRole("region", { name: "旅行回顾", exact: true });
const endButton = (page: Page) =>
  recap(page).getByRole("button", { name: "结束旅行并收录回顾", exact: true });
async function openTrip(page: Page) {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  const title = `收尾验收 ${seed.session_id}`;
  await page.request.patch(`${proxy}/api/sessions/${seed.session_id}`, {
    data: { title },
  });
  await page.goto(`/chat/${seed.session_id}`);
  await expect(endButton(page)).toBeVisible();
  return { ...seed, title };
}
async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[bid];
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
  ).json();
  return { versions, detail };
}
async function archive(page: Page, title: string) {
  await page.goto("/recaps");
  await page
    .getByRole("textbox", { name: "搜索回顾", exact: true })
    .fill(title);
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});
test("结束时保留未完成和未结算，回顾列表可搜索并只读阅读导出", async ({
  page,
}, info) => {
  const seed = await openTrip(page);
  const before = await latest(page, seed.session_id, seed.branch_id);
  await endButton(page).click();
  await expect(
    recap(page).getByLabel("旅行结束状态", { exact: true }),
  ).toContainText("当前方案已结束");
  const ended = await latest(page, seed.session_id, seed.branch_id);
  expect(ended.versions).toHaveLength(2);
  expect(ended.detail.itinerary.days).toEqual(before.detail.itinerary.days);
  expect(ended.detail.itinerary.expenses).toEqual(
    before.detail.itinerary.expenses,
  );
  await page.reload();
  await expect(recap(page)).toContainText("当前方案已结束");
  await archive(page, seed.title);
  const card = page.getByRole("article").filter({ hasText: seed.title });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("已完成1项");
  await expect(card).toContainText("未确认到访");
  await page.screenshot({
    path: info.outputPath("recap-archive.png"),
    fullPage: true,
  });
  await card.getByRole("link", { name: "阅读回顾", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "阅读回顾", exact: true }),
  ).toBeVisible();
  await expect(recap(page).getByRole("textbox")).toHaveCount(0);
  await expect(
    recap(page).getByRole("button", { name: "重新开启旅行", exact: true }),
  ).toHaveCount(0);
  await expect(
    recap(page).getByLabel("回忆正文", { exact: true }),
  ).toContainText("已完成活动");
  const exported = await page.request.get(
    (await recap(page)
      .getByRole("link", { name: "导出回顾 HTML", exact: true })
      .getAttribute("href"))!,
  );
  expect(await exported.text()).toContain("旅行状态：已结束");
  const old = await (
    await page.request.get(`${proxy}/api/versions/${before.detail.version.id}`)
  ).json();
  expect(old.itinerary.ended_at).toBeNull();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});
test("结束后补记同步回顾，重新开启移出列表，旧结束版本仍保留", async ({
  page,
}) => {
  const seed = await openTrip(page);
  await endButton(page).click();
  await expect(recap(page)).toContainText("当前方案已结束");
  const ended = await latest(page, seed.session_id, seed.branch_id);
  await page.getByRole("button", { name: "第 2 天", exact: true }).click();
  await page.getByRole("button", { name: "标记完成", exact: true }).click();
  await expect(recap(page)).toContainText("行程或账目已有变化");
  await expect(recap(page)).toContainText("当前方案已结束");
  await archive(page, seed.title);
  const card = page.getByRole("article").filter({ hasText: seed.title });
  await expect(card).toContainText("已完成2项");
  await card.getByRole("link", { name: "补记或重新开启", exact: true }).click();
  await recap(page)
    .getByRole("button", { name: "重新开启旅行", exact: true })
    .click();
  await expect(endButton(page)).toBeEnabled();
  await archive(page, seed.title);
  await expect(
    page.getByRole("article").filter({ hasText: seed.title }),
  ).toHaveCount(0);
  const old = await (
    await page.request.get(
      `${proxy}/api/versions/${ended.detail.version.id}/recap`,
    )
  ).json();
  expect(old.ended_at).toBeTruthy();
});
test("非主方案结束后从回顾返回正确分支，分叉不重复收录", async ({ page }) => {
  const seed = await openTrip(page);
  const fork = await (
    await page.request.post(
      `${proxy}/api/sessions/${seed.session_id}/branches`,
      { data: { title: "备选结束", parent_branch_id: seed.branch_id } },
    )
  ).json();
  const child = fork.items.find(
    (b: { title: string }) => b.title === "备选结束",
  );
  await page.goto(`/chat/${seed.session_id}?branch=${child.id}`);
  await endButton(page).click();
  await expect(recap(page)).toContainText("当前方案已结束");
  await archive(page, seed.title);
  const card = page.getByRole("article").filter({ hasText: seed.title });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("备选结束");
  await card.getByRole("link", { name: "补记或重新开启", exact: true }).click();
  await expect(recap(page)).toContainText("当前方案已结束");
  const ended = await latest(page, seed.session_id, child.id);
  const clone = await (
    await page.request.post(
      `${proxy}/api/sessions/${seed.session_id}/branches`,
      {
        data: {
          title: "新的旅程方案",
          parent_branch_id: child.id,
          fork_version_id: ended.detail.version.id,
        },
      },
    )
  ).json();
  const newBranch = clone.items.find(
    (b: { title: string }) => b.title === "新的旅程方案",
  );
  const newDetail = await latest(page, seed.session_id, newBranch.id);
  expect(newDetail.detail.itinerary.ended_at).toBeNull();
  await recap(page)
    .getByRole("button", { name: "重新开启旅行", exact: true })
    .click();
  expect(
    (await latest(page, seed.session_id, seed.branch_id)).detail.itinerary
      .ended_at,
  ).toBeNull();
  expect(
    (await latest(page, seed.session_id, child.id)).detail.itinerary.ended_at,
  ).toBeNull();
});
test("回顾未保存时先保留改动，相同内容保存可继续结束，回复丢失重试只保存一次", async ({
  page,
}) => {
  const seed = await openTrip(page);
  await page.getByRole("button", { name: "打开旅行回顾", exact: true }).click();
  await recap(page)
    .getByRole("button", { name: "保存旅行回顾", exact: true })
    .click();
  await expect(recap(page)).toContainText("回顾已随当前版本保存");
  const title = recap(page).getByRole("textbox", {
    name: "回顾标题",
    exact: true,
  });
  const original = await title.inputValue();
  await title.fill(original + "改");
  await title.fill(original);
  await expect(endButton(page)).toBeDisabled();
  await recap(page)
    .getByRole("button", { name: "保存旅行回顾", exact: true })
    .click();
  await expect(endButton(page)).toBeEnabled();
  expect(
    (await latest(page, seed.session_id, seed.branch_id)).versions,
  ).toHaveLength(2);
  let lose = true;
  await page.route(
    "**/api/backend/api/versions/*/journey-status",
    async (route) => {
      if (!lose) return route.continue();
      lose = false;
      const result = await route.fetch();
      expect(result.ok()).toBe(true);
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "BACKEND_UNREACHABLE",
            message: "回复暂时丢失，请重试。",
          },
        }),
      });
    },
  );
  await endButton(page).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "回复暂时丢失" }),
  ).toBeVisible();
  await endButton(page).click();
  await expect(recap(page)).toContainText("当前方案已结束");
  expect(
    (await latest(page, seed.session_id, seed.branch_id)).versions,
  ).toHaveLength(3);
});
