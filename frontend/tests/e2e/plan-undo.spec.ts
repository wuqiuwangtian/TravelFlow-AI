import { test, expect, type Page } from "playwright/test";

const proxy = "/api/backend";
async function latest(
  page: Page,
  trip: { session_id: string; branch_id: string },
) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  return (
    await page.request.get(
      `${proxy}/api/versions/${session.versions[trip.branch_id].at(-1).id}`,
    )
  ).json();
}
async function seed(page: Page) {
  return (await page.request.post(`${proxy}/test/moves`)).json();
}
async function modify(
  page: Page,
  trip: { session_id: string; branch_id: string },
) {
  const response = await page.request.post(`${proxy}/api/chat`, {
    data: {
      ...trip,
      message: "第2天轻松一点",
      request_id: crypto.randomUUID(),
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return latest(page, trip);
}
const note = (page: Page) =>
  page.getByRole("region", { name: "规划修改记录", exact: true });

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("对话直接更新后显示变化，撤销追加版本、同步路线并保留消费和输入草稿", async ({
  page,
  isMobile,
}, info) => {
  const trip = await seed(page),
    before = await latest(page, trip);
  await page.goto(`/chat/${trip.session_id}`);
  if (isMobile)
    await page
      .getByRole("navigation", { name: "旅行快捷导航" })
      .getByRole("button", { name: "对话", exact: true })
      .click();
  const input = page.getByRole("textbox", {
    name: "旅行对话输入",
    exact: true,
  });
  await input.fill("第2天轻松一点");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const undo = note(page).getByRole("button", {
    name: "撤销这次规划修改",
    exact: true,
  });
  await expect(undo).toBeVisible();
  const changed = await latest(page, trip);
  await expect(note(page)).toContainText("第 2 天安排已更新");
  await input.fill("下一条还没有发送的想法");
  await page.screenshot({
    path: info.outputPath("plan-change.png"),
    fullPage: false,
  });
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/backend/api/versions/*", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    // 两个正式读取独立返回，确保新版本稍迟于消息返回时，完成说明仍会回到视野内。
    if (data.version?.trigger?.startsWith("plan_undo:"))
      await new Promise((resolve) => setTimeout(resolve, 200));
    await route.fulfill({ response });
  });
  await page.route("**/api/backend/api/versions/*/undo-plan", async (route) => {
    const response = await route.fetch();
    await waiting;
    await route.fulfill({ response });
  });
  await undo.click();
  await expect(
    note(page).getByRole("button", { name: "正在撤销并同步…" }),
  ).toHaveAttribute("aria-busy", "true");
  await expect(input).toBeDisabled();
  release();
  await expect(note(page).getByRole("status")).toContainText("已撤销规划修改");
  await expect(input).toHaveValue("下一条还没有发送的想法");
  const result = await latest(page, trip);
  expect(result.version.seq).toBe(changed.version.seq + 1);
  expect(result.itinerary.days).toEqual(before.itinerary.days);
  expect(result.itinerary.expenses).toEqual(changed.itinerary.expenses);
  expect(result.itinerary.adjustments).toEqual(changed.itinerary.adjustments);
  await expect(note(page)).toBeFocused();
  await expect(note(page).getByRole("status")).toBeInViewport();
  if (isMobile)
    await expect
      .poll(() =>
        page.evaluate(() => {
          const input = document
            .querySelector(".journey-chat-input")!
            .getBoundingClientRect();
          const nav = document
            .querySelector(".journey-mobile-nav")!
            .getBoundingClientRect();
          return input.top >= 64 && input.bottom <= nav.top;
        }),
      )
      .toBeTruthy();
  await expect(
    note(page).getByRole("button", { name: "撤销这次规划修改", exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: info.outputPath("plan-undone.png"),
    fullPage: false,
  });
});

test("撤销已保存但回包丢失，刷新后沿用请求号重试且不覆盖后来新增的消费", async ({
  page,
}) => {
  const trip = await seed(page),
    changed = await modify(page, trip);
  const requests: { request_id: string; change_version_id: string }[] = [];
  await page.route("**/api/backend/api/versions/*/undo-plan", async (route) => {
    requests.push(route.request().postDataJSON());
    await route.fetch();
    await route.abort("failed");
  });
  await page.goto(`/chat/${trip.session_id}`);
  await note(page)
    .getByRole("button", { name: "撤销这次规划修改", exact: true })
    .click();
  await expect(note(page).getByRole("alert")).toContainText("操作结果尚未确认");
  const undone = await latest(page, trip);
  expect(undone.version.seq).toBe(changed.version.seq + 1);
  const paid = await page.request.post(
    `${proxy}/api/versions/${undone.version.id}/expenses`,
    {
      data: {
        request_id: "later-meal-001",
        title: "后来记的晚餐",
        amount: "73.25",
        category: "meal",
      },
    },
  );
  expect(paid.ok(), await paid.text()).toBeTruthy();
  const current = await paid.json();
  await page.unroute("**/api/backend/api/versions/*/undo-plan");
  await page.route("**/api/backend/api/versions/*/undo-plan", async (route) => {
    requests.push(route.request().postDataJSON());
    await route.continue();
  });
  await page.reload();
  await note(page)
    .getByRole("button", { name: "重试撤销", exact: true })
    .click();
  await expect(note(page).getByRole("status")).toContainText("已撤销规划修改");
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  const result = await latest(page, trip);
  expect(result.version.id).toBe(current.version.id);
  expect(result.itinerary.expenses).toEqual(current.itinerary.expenses);
  await expect(page.getByLabel("对话消息", { exact: true })).toContainText(
    "已撤销这次规划修改",
  );
});

test("另一处修改后旧撤销显示原位冲突，可刷新当前安排且保留聊天草稿", async ({
  page,
}) => {
  const trip = await seed(page),
    changed = await modify(page, trip);
  await page.goto(`/chat/${trip.session_id}`);
  await expect(
    note(page).getByRole("button", { name: "撤销这次规划修改", exact: true }),
  ).toBeVisible();
  const input = page.getByRole("textbox", {
    name: "旅行对话输入",
    exact: true,
  });
  await input.fill("尚未发送的草稿");
  const added = await page.request.post(
    `${proxy}/api/versions/${changed.version.id}/expenses`,
    {
      data: {
        request_id: "concurrent-meal",
        title: "刚记的午饭",
        amount: "41.50",
        category: "meal",
      },
    },
  );
  expect(added.ok()).toBeTruthy();
  const current = await added.json();
  await note(page)
    .getByRole("button", { name: "撤销这次规划修改", exact: true })
    .click();
  await expect(note(page).getByRole("alert")).toContainText("规划已变化");
  expect((await latest(page, trip)).version.id).toBe(current.version.id);
  await expect(input).toHaveValue("尚未发送的草稿");
  await note(page)
    .getByRole("button", { name: "刷新当前安排", exact: true })
    .click();
  await expect(
    note(page).getByRole("button", { name: "撤销这次规划修改", exact: true }),
  ).toBeEnabled();
  await note(page)
    .getByRole("button", { name: "撤销这次规划修改", exact: true })
    .click();
  await expect(note(page).getByRole("status")).toContainText("已撤销规划修改");
  expect((await latest(page, trip)).itinerary.expenses).toEqual(
    current.itinerary.expenses,
  );
});

test("初次生成与历史版本不提供撤销，修改记录查询失败可重试", async ({
  page,
}) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  await expect(note(page)).toHaveCount(0);
  await modify(page, trip);
  await page.route("**/api/backend/api/versions/*/planning-change", (route) =>
    route.fulfill({
      status: 503,
      json: { error: { code: "UNAVAILABLE", message: "模拟修改记录暂不可用" } },
    }),
  );
  await page.reload();
  await expect(note(page)).toContainText("暂时无法读取修改记录");
  await expect(
    page.getByRole("textbox", { name: "旅行对话输入", exact: true }),
  ).toBeEnabled();
  await page.unroute("**/api/backend/api/versions/*/planning-change");
  await note(page)
    .getByRole("button", { name: "刷新当前安排", exact: true })
    .click();
  await expect(
    note(page).getByRole("button", { name: "撤销这次规划修改", exact: true }),
  ).toBeVisible();
  await page.getByText("完整计划书、导出与版本历史", { exact: true }).click();
  await page.getByRole("button", { name: /^v1\s/ }).click();
  await expect(note(page)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "从此版本分叉后编辑", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^v2\s/ }).click();
  await expect(
    note(page).getByRole("button", { name: "撤销这次规划修改", exact: true }),
  ).toBeVisible();
});
