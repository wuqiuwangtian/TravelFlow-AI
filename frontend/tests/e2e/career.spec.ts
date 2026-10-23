import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const recap = (page: Page) =>
  page.getByRole("region", { name: "旅行回顾", exact: true });
const money = (cents: number) =>
  `¥${(cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  return session.versions[bid].at(-1).id as string;
}
async function finish(page: Page, sid: string, bid: string) {
  const response = await page.request.put(
    `${proxy}/api/versions/${await latest(page, sid, bid)}/journey-status`,
    { data: { ended: true } },
  );
  expect(response.ok()).toBe(true);
}
async function seedTrip(page: Page) {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  const title = `生涯验收 ${seed.session_id}`;
  await page.request.patch(`${proxy}/api/sessions/${seed.session_id}`, {
    data: { title },
  });
  await finish(page, seed.session_id, seed.branch_id);
  const fork = await (
    await page.request.post(
      `${proxy}/api/sessions/${seed.session_id}/branches`,
      {
        data: { title: "生涯备选方案", parent_branch_id: seed.branch_id },
      },
    )
  ).json();
  const child = fork.items.find(
    (b: { title: string }) => b.title === "生涯备选方案",
  );
  await finish(page, seed.session_id, child.id);
  return { ...seed, title, child_id: child.id };
}
const cardFor = (page: Page, title: string) =>
  page.getByRole("article", { name: `旅行：${title}`, exact: true });

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("生涯按旅行去重，城市可追溯到实际方案与只读回顾，手机无横向溢出", async ({
  page,
}, info) => {
  const seed = await seedTrip(page);
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/travel-career?")),
    page.goto("/career"),
  ]);
  const data = await response.json();
  const own = data.items.filter(
    (item: { session_id: string }) => item.session_id === seed.session_id,
  );
  expect(own).toHaveLength(1);
  expect(own[0].branch_id).toBe(seed.branch_id);
  const summary = page.getByRole("region", { name: "生涯汇总", exact: true });
  await expect(summary).toContainText(`${data.summary.trip_count}次`);
  await expect(summary).toContainText(
    money(data.summary.ledger.net_expense_cents),
  );
  const card = cardFor(page, seed.title);
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("已记净消费 ¥20.00（1笔）");
  await expect(card).toContainText("已结束的2份方案仅计入这一份");
  const city = page.getByRole("button", { name: /^北京/ });
  await city.click();
  await expect(city).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("heading", { name: "北京的旅行", exact: true }),
  ).toBeVisible();
  await expect(card).toBeVisible();
  await expect(
    page.getByRole("region", { name: "已记消费口径", exact: true }),
  ).toContainText("预算估算不计入");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await page.screenshot({
    path: info.outputPath("travel-career.png"),
    fullPage: true,
  });
  await card.getByRole("link", { name: "阅读回顾", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "阅读回顾", exact: true }),
  ).toBeVisible();
  await expect(recap(page).getByRole("textbox")).toHaveCount(0);
  expect(page.url()).toContain(`/recaps/${own[0].version_id}`);
});

test("重新开启主方案后改用结束备选，补记同步生涯，全部开启后移除", async ({
  page,
}) => {
  const seed = await seedTrip(page);
  await page.goto("/career");
  let card = cardFor(page, seed.title);
  await card.getByRole("link", { name: "返回原方案补记", exact: true }).click();
  await expect(page).toHaveURL(
    `/chat/${seed.session_id}?branch=${seed.branch_id}`,
  );
  await recap(page)
    .getByRole("button", { name: "重新开启旅行", exact: true })
    .click();
  await expect(
    recap(page).getByRole("button", {
      name: "结束旅行并收录回顾",
      exact: true,
    }),
  ).toBeEnabled();
  await page.goto("/career");
  card = cardFor(page, seed.title);
  await expect(card).toContainText("统计采用：生涯备选方案（最近结束方案）");
  await card.getByRole("link", { name: "返回原方案补记", exact: true }).click();
  await expect(page).toHaveURL(
    `/chat/${seed.session_id}?branch=${seed.child_id}`,
  );
  await page.getByRole("button", { name: "第 2 天", exact: true }).click();
  await page.getByRole("button", { name: "标记完成", exact: true }).click();
  await expect(recap(page)).toContainText("行程或账目已有变化");
  await page.goto("/career");
  await expect(cardFor(page, seed.title)).toContainText("已完成2项");
  await cardFor(page, seed.title)
    .getByRole("link", { name: "返回原方案补记", exact: true })
    .click();
  await recap(page)
    .getByRole("button", { name: "重新开启旅行", exact: true })
    .click();
  await expect(
    recap(page).getByRole("button", {
      name: "结束旅行并收录回顾",
      exact: true,
    }),
  ).toBeEnabled();
  await page.goto("/career");
  await expect(page.getByRole("status")).toHaveCount(0);
  await expect(cardFor(page, seed.title)).toHaveCount(0);
});

test("空生涯有明确入口，加载失败不展示零统计，重试恢复真实记录", async ({
  page,
}) => {
  const seed = await seedTrip(page);
  let state: "empty" | "failed" | "live" = "empty";
  await page.route("**/api/backend/api/travel-career?*", async (route) => {
    if (state === "live") return route.continue();
    if (state === "failed") {
      state = "live";
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "BACKEND_UNREACHABLE", message: "生涯服务暂时不可用" },
        }),
      });
    }
    const response = await route.fetch();
    const data = await response.json();
    state = "failed";
    await route.fulfill({
      json: {
        summary: Object.fromEntries(
          Object.entries(data.summary).map(([key, value]) => [
            key,
            typeof value === "object"
              ? Object.fromEntries(
                  Object.keys(value as object).map((field) => [field, 0]),
                )
              : 0,
          ]),
        ),
        cities: [],
        items: [],
        total: 0,
      },
    });
  });
  await page.goto("/career");
  await expect(
    page.getByRole("heading", {
      name: "第一段回忆，从一次旅行开始",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "打开我的旅行 →", exact: true }),
  ).toHaveAttribute("href", "/sessions");
  await page.reload();
  await expect(
    page.getByRole("alert").filter({ hasText: "生涯服务暂时不可用" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "生涯汇总", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(cardFor(page, seed.title)).toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: "生涯服务暂时不可用" }),
  ).toHaveCount(0);
});
