import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const attention = (page: Page) =>
  page.getByRole("region", { name: "行程待核对事项", exact: true });
const input = (page: Page) =>
  page.getByRole("textbox", { name: "旅行对话输入", exact: true });
async function seed(page: Page) {
  const trip = await (
    await page.request.post(`${proxy}/test/places?attention=true`)
  ).json();
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  const version = session.versions[trip.branch_id].at(-1).id;
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${version}`)
  ).json();
  return { ...trip, version, detail };
}
async function saved(page: Page, sid: string) {
  return (await page.request.get(`${proxy}/api/sessions/${sid}`)).json();
}
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1,
    ),
  ).toBe(true);
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

for (const theme of ["paper", "guide", "night"]) {
  test(`整趟旅行集中展示待定位、未定价及时间冲突，只读查看不写版本（${theme}）`, async ({
    page,
  }, info) => {
    await page.addInitScript(
      (value) => localStorage.setItem("travelflow.theme", value),
      theme,
    );
    const trip = await seed(page);
    let writes = 0;
    page.on("request", (request) => {
      if (request.url().includes("/api/backend/") && request.method() !== "GET")
        writes++;
    });
    await page.goto(`/chat/${trip.session_id}`);
    await expect(attention(page)).toBeVisible();
    await expect(attention(page).locator("summary")).toContainText(
      "地点 2 处待明确",
    );
    await expect(attention(page).locator("summary")).toContainText(
      `费用 ${trip.detail.budget_overview.unknown_count} 项未定`,
    );
    await expect(attention(page).locator("summary")).toContainText(
      "预算比较口径未定",
    );
    await attention(page).locator("summary").focus();
    await page.keyboard.press("Enter");
    const places = attention(page).getByRole("region", {
      name: "待明确地点",
      exact: true,
    });
    await expect(places).toContainText("第1天第1站");
    await expect(places).toContainText("第2天第1站");
    for (const excluded of [
      "already-done",
      "already-skipped",
      "free-time",
      "transfer",
    ])
      await expect(places).not.toContainText(excluded);
    const costs = attention(page).getByRole("region", {
      name: "待明确费用",
      exact: true,
    });
    await expect(costs).not.toContainText("已结算未估价项目");
    await expect(costs).not.toContainText("already-done"); // 零元费用保持已知。
    await expect(costs).toContainText("未定费用不等于零");
    await expect(attention(page)).toContainText("固定时间与前段路程冲突");
    await expect(attention(page)).toContainText("预留交通时间不足");
    await expect(attention(page)).toContainText("跨日衔接冲突");
    await expect(attention(page)).toContainText("交通耗时尚未明确");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await noOverflow(page);
    await page.screenshot({
      path: info.outputPath("attention.png"),
      fullPage: true,
    });
    expect(writes).toBe(0);
    expect(
      (await saved(page, trip.session_id)).versions[trip.branch_id],
    ).toHaveLength(1);
  });
}

test("跨天查看地点、费用和说明保持聊天与费用草稿，并将焦点送到目标", async ({
  page,
}) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  const budget = page.getByRole("region", { name: "费用与预算", exact: true });
  await budget
    .getByRole("button", { name: "添加自定义项", exact: true })
    .click();
  const draft = budget.getByRole("form", {
    name: "添加自定义预算",
    exact: true,
  });
  await draft
    .getByLabel("项目名称", { exact: true })
    .fill("保留未保存的预算草稿");
  await input(page).fill("保留未发送的聊天草稿");
  await attention(page).locator("summary").click();
  await attention(page)
    .getByRole("button", { name: "查看第2天第1站待明确地点", exact: true })
    .click();
  await expect(page.getByLabel("当前站点", { exact: true })).toContainText(
    "示例餐厅",
  );
  await expect(page.getByLabel("当前站点", { exact: true })).toBeFocused();
  await expect(
    page.getByRole("button", { name: "第 2 天", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  const row = trip.detail.budget_overview.rows.find(
    (item: { planned_cents: number | null; settled: boolean }) =>
      item.planned_cents === null && !item.settled,
  );
  await attention(page)
    .getByRole("button", { name: `查看待明确费用：${row.title}`, exact: true })
    .click();
  await expect(
    page.locator(`[id="journey-budget-row-${row.id}"]`),
  ).toBeFocused();
  await expect(
    page.getByLabel(`费用项目：${row.title}`, { exact: true }),
  ).toBeVisible();
  await attention(page)
    .getByRole("button", { name: /查看第1天提醒：.*预留交通时间不足/ })
    .click();
  await expect(page.locator("#journey-query-notes")).toBeFocused();
  await expect(page.locator("#journey-query-notes")).toHaveAttribute(
    "open",
    "",
  );
  await expect(
    page.getByRole("button", { name: "第 1 天", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(draft.getByLabel("项目名称", { exact: true })).toHaveValue(
    "保留未保存的预算草稿",
  );
  await expect(input(page)).toHaveValue("保留未发送的聊天草稿");
  expect(
    (await saved(page, trip.session_id)).versions[trip.branch_id],
  ).toHaveLength(1);
  await noOverflow(page);
});

test("地点确认后汇总更新，查看旧版本显示原快照并保持只读", async ({ page }) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  await attention(page).locator("summary").click();
  await attention(page)
    .getByRole("button", { name: "查看第2天第1站待明确地点", exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认第2天第1站的地点", exact: true })
    .click();
  const prompt = page.getByLabel("待补充的信息", { exact: true });
  await prompt.getByRole("button", { name: /西单店.*西单路2号/ }).click();
  await expect(prompt).toHaveCount(0);
  await expect(attention(page).locator("summary")).toContainText(
    "地点 1 处待明确",
  );
  const state = await saved(page, trip.session_id);
  expect(state.versions[trip.branch_id]).toHaveLength(2);
  const current = await (
    await page.request.get(
      `${proxy}/api/versions/${state.versions[trip.branch_id].at(-1).id}`,
    )
  ).json();
  expect(current.itinerary.expenses).toEqual(trip.detail.itinerary.expenses);
  await page.getByRole("button", { name: "完整路书", exact: true }).click();
  await page
    .locator(".journey-book ol button")
    .filter({ hasText: "v1" })
    .click();
  await expect(attention(page).locator("summary")).toContainText(
    "地点 2 处待明确",
  );
  await expect(
    page.getByRole("button", { name: "从此版本分叉后编辑", exact: true }),
  ).toBeVisible();
  await attention(page)
    .getByRole("button", { name: "查看第2天第1站待明确地点", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "确认第2天第1站的地点", exact: true }),
  ).toBeDisabled();
  expect(
    (await saved(page, trip.session_id)).versions[trip.branch_id],
  ).toHaveLength(2);
});

test("已知零元与已结算项目不显示待定，缺失费用汇总不冒充全部明确", async ({
  page,
}) => {
  const trip = await seed(page);
  let unavailable = false;
  await page.route(
    `**/api/backend/api/versions/${trip.version}`,
    async (route) => {
      const data = await (await route.fetch()).json();
      const place = data.itinerary.days[0].activities[1].place;
      for (const day of data.itinerary.days) {
        day.warnings = [];
        day.legs = [];
        for (const activity of day.activities) {
          activity.place_status = "matched";
          activity.place = place;
        }
      }
      data.budget_overview = unavailable
        ? null
        : {
            ...data.budget_overview,
            rows: data.budget_overview.rows.map((row: object) => ({
              ...row,
              planned_cents: 0,
              settled: true,
            })),
            unknown_count: 0,
            budget_cents: 200000,
          };
      await route.fulfill({ json: data });
    },
  );
  await page.goto(`/chat/${trip.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  await expect(attention(page)).toHaveCount(0);
  unavailable = true;
  await page.reload();
  await expect(attention(page)).toContainText("费用汇总未取得");
  await attention(page).locator("summary").click();
  await expect(attention(page)).toContainText("暂不能判断总价或预算余量");
});

test("从汇总跨天查看后保存预算草稿，仍归属开始编辑的日期", async ({ page }) => {
  const trip = await seed(page);
  await page.goto(`/chat/${trip.session_id}`);
  const budget = page.getByRole("region", { name: "费用与预算", exact: true });
  await budget
    .getByRole("button", { name: "添加自定义项", exact: true })
    .click();
  const form = budget.getByRole("form", {
    name: "添加自定义预算",
    exact: true,
  });
  await form.getByLabel("项目名称", { exact: true }).fill("第一天的预算草稿");
  await form.getByLabel("预估金额（元，全团）", { exact: true }).fill("30.50");
  await attention(page).locator("summary").click();
  await attention(page)
    .getByRole("button", { name: "查看第2天第1站待明确地点", exact: true })
    .click();
  await form.getByRole("button", { name: "保存自定义项", exact: true }).click();
  await expect(form).toHaveCount(0);
  const state = await saved(page, trip.session_id);
  const current = await (
    await page.request.get(
      `${proxy}/api/versions/${state.versions[trip.branch_id].at(-1).id}`,
    )
  ).json();
  expect(
    current.budget_overview.rows.find(
      (row: { title: string }) => row.title === "第一天的预算草稿",
    ).day,
  ).toBe(1);
  expect(current.itinerary.expenses).toEqual(trip.detail.itinerary.expenses);
});

test("空分支不显示旧分支提醒，旧版本迟到也不能带回旧警告", async ({ page }) => {
  const trip = await seed(page);
  await page.request.post(`${proxy}/api/sessions/${trip.session_id}/branches`, {
    data: { title: "独立需求" },
  });
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let waiting = false;
  await page.route(
    `**/api/backend/api/versions/${trip.version}`,
    async (route) => {
      const response = await route.fetch();
      waiting = true;
      await held;
      await route.fulfill({ response });
    },
  );
  await page.goto(`/chat/${trip.session_id}`);
  await expect.poll(() => waiting).toBe(true);
  await page.getByRole("button", { name: "独立需求", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "本次旅行条件", exact: true }),
  ).toBeVisible();
  const oldResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/versions/${trip.version}`),
  );
  release();
  await oldResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(attention(page)).toHaveCount(0);
});
