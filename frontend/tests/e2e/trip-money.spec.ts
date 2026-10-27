import { expect, test, type Page } from "playwright/test";
import type { VersionDetail } from "../../lib/types";

const proxy = "/api/backend";
const failure = {
  error: {
    code: "BACKEND_UNREACHABLE",
    message: "隔离服务暂时无法保存，请原位重试。",
  },
};
const budget = (page: Page) =>
  page.getByRole("region", { name: "费用与预算", exact: true });
const ledger = (page: Page) =>
  page.getByRole("region", { name: "旅行记账", exact: true });
async function seed(page: Page) {
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/chat/${trip.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  return trip;
}
async function latest(page: Page) {
  const id = page.url().split("/").at(-1);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${id}`)
  ).json();
  const versions = session.versions[session.branches[0].id] as { id: string }[];
  const detail = (await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1)!.id}`)
  ).json()) as VersionDetail;
  return { versions, detail };
}
async function openLedger(page: Page) {
  await page.getByText("消费明细、图片凭证与退款报销", { exact: true }).click();
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

test("自定义项紧接入口，等待不重复提交，失败保留草稿和请求标识", async ({
  page,
}) => {
  await seed(page);
  const hierarchy = await budget(page)
    .getByLabel("预算推算汇总", { exact: true })
    .evaluate((el) => ({
      primary: parseFloat(
        getComputedStyle(el.querySelector("[data-primary] p:last-child")!)
          .fontSize,
      ),
      secondary: parseFloat(
        getComputedStyle(el.querySelector("div:nth-child(3) p:last-child")!)
          .fontSize,
      ),
      first: el.firstElementChild?.hasAttribute("data-primary"),
    }));
  expect(hierarchy.primary).toBeGreaterThan(hierarchy.secondary * 1.25);
  expect(hierarchy.first).toBe(true);
  await budget(page)
    .getByRole("button", { name: "添加自定义项", exact: true })
    .click();
  const form = budget(page).getByRole("form", {
    name: "添加自定义预算",
    exact: true,
  });
  await expect(form.getByLabel("项目名称", { exact: true })).toBeFocused();
  const order = await budget(page).evaluate((el) => {
    const f = el.querySelector("form")!,
      summary = el.querySelector('[aria-label="预算推算汇总"]')!;
    return !!(
      f.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING
    );
  });
  expect(order).toBe(true);
  await form.getByLabel("项目名称", { exact: true }).fill("行李寄存");
  await form.getByLabel("预估金额（元，全团）", { exact: true }).fill("30.50");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const ids: string[] = [];
  await page.route(
    "**/api/backend/api/versions/*/budget-items",
    async (route) => {
      ids.push(route.request().postDataJSON().request_id);
      if (ids.length > 1) return route.continue();
      await gate;
      await route.fulfill({ status: 503, json: failure });
    },
  );
  const save = form.getByRole("button", { name: "保存自定义项", exact: true });
  await save.click();
  await expect(save).toHaveAttribute("aria-busy", "true");
  await expect(save).toBeDisabled();
  await expect(form.getByRole("status")).toContainText("正在保存");
  expect(ids).toHaveLength(1);
  release();
  await expect(form.getByRole("alert")).toContainText("隔离服务");
  await expect(form.locator(".trip-action-feedback")).toBeFocused();
  await expect(form.getByLabel("项目名称", { exact: true })).toHaveValue(
    "行李寄存",
  );
  await expect(
    form.getByLabel("预估金额（元，全团）", { exact: true }),
  ).toHaveValue("30.50");
  await save.click();
  await expect(form).toHaveCount(0);
  await expect(budget(page).getByRole("status")).toContainText(
    "自定义项已添加",
  );
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
  const saved = await latest(page);
  expect(saved.versions).toHaveLength(2);
  expect(
    saved.detail.budget_overview!.rows.filter(
      (row) => row.title === "行李寄存",
    ),
  ).toHaveLength(1);
  await noOverflow(page);
});

test("实际总额响应丢失时原位保留草稿，重试只记一笔并更新预算差额", async ({
  page,
}) => {
  await seed(page);
  const before = await latest(page);
  const rowData = before.detail.budget_overview!.rows.find(
    (row) => !row.expense_ids.length && row.planned_cents !== null,
  )!;
  const row = budget(page).getByLabel(`费用项目：${rowData.title}`, {
    exact: true,
  });
  const category = row.locator("xpath=ancestor::details[1]");
  await category.evaluate((el) => {
    (el as HTMLDetailsElement).open = true;
  });
  await row.getByRole("button", { name: "填实际总额", exact: true }).click();
  const form = row.getByRole("form", { name: "填写实际总额", exact: true });
  await form.getByRole("textbox").fill("88.25");
  const ids: string[] = [];
  await page.route("**/api/backend/api/versions/*/expenses", async (route) => {
    ids.push(route.request().postDataJSON().request_id);
    if (ids.length !== 1) return route.continue();
    const saved = await route.fetch();
    expect(saved.ok()).toBe(true);
    await route.abort("connectionfailed");
  });
  await form.getByRole("button", { name: "保存实际总额", exact: true }).click();
  await expect(row.getByRole("alert")).toContainText("操作结果尚未确认");
  await expect(form.getByRole("textbox")).toHaveValue("88.25");
  await expect(
    page
      .getByRole("region", { name: "当前站点", exact: true })
      .getByRole("alert"),
  ).toHaveCount(0);
  await form.getByRole("button", { name: "保存实际总额", exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect(row.getByRole("status")).toContainText("实际总额已保存");
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
  const after = await latest(page);
  expect(after.versions).toHaveLength(2);
  expect(
    after.detail.itinerary!.expenses.filter(
      (entry) => entry.budget_item_id === rowData.id,
    ),
  ).toHaveLength(1);
  expect(
    after.detail.budget_overview!.rows.find((item) => item.id === rowData.id)!
      .actual_cents,
  ).toBe(8825);
  await noOverflow(page);
});

test("消费在原明细展开更正，失败保留金额，保存保留付款日与行程关联", async ({
  page,
}) => {
  await seed(page);
  await openLedger(page);
  await ledger(page).getByText("消费明细（1 笔）", { exact: true }).click();
  const row = ledger(page)
    .getByRole("list", { name: "消费明细", exact: true })
    .getByRole("listitem")
    .first();
  await row
    .getByRole("button", { name: "更正预付公园门票", exact: true })
    .click();
  const form = row.getByRole("form", { name: "更正消费", exact: true });
  await expect(form.getByLabel("消费内容", { exact: true })).toBeFocused();
  await form.getByLabel("实际金额（元，全团）", { exact: true }).fill("17.50");
  let fail = true;
  await page.route("**/api/backend/api/versions/*/expenses/*", (route) =>
    fail ? route.fulfill({ status: 503, json: failure }) : route.continue(),
  );
  await form.getByRole("button", { name: "保存更正", exact: true }).click();
  await expect(row.getByRole("alert")).toContainText("隔离服务");
  await expect(
    form.getByLabel("实际金额（元，全团）", { exact: true }),
  ).toHaveValue("17.50");
  await noOverflow(page);
  fail = false;
  await form.getByRole("button", { name: "保存更正", exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect(row.getByRole("status")).toContainText("消费已更正");
  expect((await latest(page)).detail.itinerary!.expenses[0]).toMatchObject({
    day: 2,
    activity_id: "park",
    budget_item_id: "stay-park",
    amount_cents: 1750,
  });
});

test("待到账、标记到账和原位更正回款同步净消费，删除失败留在原条目", async ({
  page,
}) => {
  await seed(page);
  await openLedger(page);
  const returns = page.getByRole("region", { name: "退款与报销", exact: true });
  await returns.getByRole("button", { name: "记回款", exact: true }).click();
  const form = returns.getByRole("form", { name: "回款记录", exact: true });
  await form.getByLabel("到账状态", { exact: true }).selectOption("pending");
  await form.getByLabel("回款金额（元，全团）", { exact: true }).fill("5.25");
  await form.getByRole("button", { name: "保存回款", exact: true }).click();
  await expect(form).toHaveCount(0);
  const pending = await latest(page);
  expect(pending.detail.itinerary!.ledger_summary.pending_refund_cents).toBe(
    525,
  );
  expect(pending.detail.itinerary!.ledger_summary.refunded_cents).toBe(0);
  await returns.getByText("回款明细（1 笔）", { exact: true }).click();
  const row = returns.getByRole("listitem");
  await row.getByRole("button", { name: "标记到账", exact: true }).click();
  await expect(row.getByRole("status")).toContainText("已标记到账");
  await row.getByRole("button", { name: "更正回款", exact: true }).click();
  const editor = row.getByRole("form", { name: "回款记录", exact: true });
  await editor.getByLabel("回款金额（元，全团）", { exact: true }).fill("6.50");
  await editor.getByRole("button", { name: "保存回款", exact: true }).click();
  await expect(editor).toHaveCount(0);
  const received = (await latest(page)).detail.itinerary!.ledger_summary;
  expect(received.refunded_cents).toBe(650);
  expect(received.net_expense_cents).toBe(received.paid_cents - 650);
  await row.getByRole("button", { name: "移除回款", exact: true }).click();
  let fail = true;
  await page.route(
    "**/api/backend/api/versions/*/expense-adjustments/*",
    (route) =>
      fail ? route.fulfill({ status: 503, json: failure }) : route.continue(),
  );
  await row.getByRole("button", { name: "确认移除回款", exact: true }).click();
  await expect(row.getByRole("alert")).toContainText("隔离服务");
  await noOverflow(page);
  fail = false;
  await row.getByRole("button", { name: "确认移除回款", exact: true }).click();
  await expect(row).toHaveCount(0);
  await expect(
    returns.getByRole("status").filter({ hasText: "回款记录已移除" }),
  ).toBeVisible();
  const deleted = await latest(page);
  expect(deleted.detail.itinerary!.adjustments).toHaveLength(0);
  expect(deleted.detail.itinerary!.expenses).toHaveLength(1);
});

test("跨午夜调整失败就地提示并保留时间，成功后展示真实保存反馈", async ({
  page,
}) => {
  await seed(page);
  await page
    .getByRole("button", { name: "从这里调整后续", exact: true })
    .click();
  const form = page.getByRole("form", { name: "调整后续安排", exact: true });
  await expect(
    form.getByLabel("从此处出发的时间", { exact: true }),
  ).toHaveValue("23:30");
  await form.getByLabel("结束日期", { exact: true }).selectOption("1");
  await form.getByLabel("最晚结束时间", { exact: true }).fill("02:30");
  let fail = true;
  await page.route("**/api/backend/api/versions/*/days/*/replan", (route) =>
    fail ? route.fulfill({ status: 503, json: failure }) : route.continue(),
  );
  await form.getByRole("button", { name: "更新后续安排", exact: true }).click();
  const controls = form.locator("..");
  await expect(controls.getByRole("alert")).toContainText("隔离服务");
  await expect(form.getByLabel("最晚结束时间", { exact: true })).toHaveValue(
    "02:30",
  );
  expect((await latest(page)).versions).toHaveLength(1);
  fail = false;
  await form.getByRole("button", { name: "更新后续安排", exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: "后续安排已更新" }),
  ).toBeVisible();
  const saved = await latest(page);
  expect(saved.detail.itinerary!.days[0].replan_context).toMatchObject({
    time: "23:30",
    end_time: "02:30",
    end_day_offset: 1,
  });
  expect(saved.detail.itinerary!.expenses).toHaveLength(1);
});

test("长消费选项不撑宽手机，定位失败保留导航入口，控件支持触屏与减少动画", async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await seed(page);
  await page.getByRole("button", { name: "第 2 天", exact: true }).click();
  const locate = page.getByRole("button", {
    name: "用我的位置导航",
    exact: true,
  });
  await page.evaluate(() => {
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition(_ok: unknown, reject: (e: unknown) => void) {
          reject({ code: 1 });
        },
      },
    });
  });
  const navigation = page.getByRole("link", {
    name: "打开地点导航",
    exact: true,
  });
  const href = await navigation.getAttribute("href");
  expect(new URL(href!).searchParams.get("to")).toContain("示例公园");
  await locate.click();
  await expect(
    page.getByRole("status").filter({ hasText: "未获定位授权" }),
  ).toBeVisible();
  await expect(navigation).toHaveAttribute("href", href!);
  await openLedger(page);
  await ledger(page).getByText("消费明细（1 笔）", { exact: true }).click();
  await ledger(page)
    .getByRole("button", { name: "更正预付公园门票", exact: true })
    .click();
  const edit = ledger(page).getByRole("form", {
    name: "更正消费",
    exact: true,
  });
  const longTitle = "长消费项目：" + "接送交通与行李寄存服务".repeat(7);
  await edit.getByLabel("消费内容", { exact: true }).fill(longTitle);
  await edit.getByRole("button", { name: "保存更正", exact: true }).click();
  await expect(edit).toHaveCount(0);
  const returns = page.getByRole("region", { name: "退款与报销", exact: true });
  await returns.getByRole("button", { name: "记回款", exact: true }).click();
  const form = returns.getByRole("form", { name: "回款记录", exact: true });
  await expect(form.getByLabel("对应原始消费", { exact: true })).toBeFocused();
  await noOverflow(page);
  const save = form.getByRole("button", { name: "保存回款", exact: true });
  const metrics = await save.evaluate((el) => ({
    height: el.getBoundingClientRect().height,
    duration: getComputedStyle(el).transitionDuration,
    bg: getComputedStyle(el).backgroundColor,
  }));
  expect(metrics.height).toBeGreaterThanOrEqual(
    testInfo.project.name === "mobile" ? 44 : 40,
  );
  expect(parseFloat(metrics.duration)).toBeLessThan(0.02);
  await save.focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  await expect(save).toBeFocused();
  expect(
    await save.evaluate((el) => getComputedStyle(el).outlineStyle),
  ).not.toBe("none");
});

test("凭证上传和识别只填草稿，保存失败保留图片，实付更正保留凭证关联", async ({
  page,
}) => {
  await seed(page);
  await openLedger(page);
  await ledger(page)
    .getByRole("button", { name: "记一笔", exact: true })
    .click();
  const form = ledger(page).getByRole("form", {
    name: "新增消费",
    exact: true,
  });
  const png = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 480;
    canvas.height = 320;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, 480, 320);
    ctx.fillStyle = "black";
    ctx.fillText("QA receipt 26.50", 10, 30);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  await form.getByLabel("添加凭证图片", { exact: true }).setInputFiles({
    name: "receipt.png",
    mimeType: "image/png",
    buffer: Buffer.from(png, "base64"),
  });
  const image = form.getByRole("img", { name: "消费凭证1", exact: true });
  await expect(image).toBeVisible();
  await expect(form.getByRole("status")).toContainText("图片已保存在本机");
  let recogniseFail = true;
  await page.route(
    "**/api/backend/api/versions/*/receipts/*/recognise",
    (route) =>
      recogniseFail
        ? route.fulfill({ status: 503, json: failure })
        : route.fulfill({
            json: {
              title: "寄存小票",
              amount: "26.50",
              kind: "expense",
              category: "other",
              currency: "CNY",
              warnings: [],
            },
          }),
  );
  await form
    .getByRole("button", { name: "识别第1张凭证", exact: true })
    .click();
  await expect(form.getByRole("alert")).toContainText("隔离服务");
  await expect(image).toBeVisible();
  recogniseFail = false;
  await form
    .getByRole("button", { name: "识别第1张凭证", exact: true })
    .click();
  await expect(
    form.getByLabel("实际金额（元，全团）", { exact: true }),
  ).toHaveValue("26.50");
  expect((await latest(page)).versions).toHaveLength(1);
  let fail = true;
  await page.route("**/api/backend/api/versions/*/expenses", (route) =>
    fail ? route.fulfill({ status: 503, json: failure }) : route.continue(),
  );
  await form.getByRole("button", { name: "保存消费", exact: true }).click();
  await expect(ledger(page).getByRole("alert")).toContainText("隔离服务");
  await expect(image).toBeVisible();
  fail = false;
  await form.getByRole("button", { name: "保存消费", exact: true }).click();
  await expect(form).toHaveCount(0);
  const saved = await latest(page);
  const entry = saved.detail.itinerary!.expenses.find(
    (e) => e.title === "寄存小票",
  )!;
  expect(entry.receipt_ids).toHaveLength(1);
  const row = budget(page).getByLabel("费用项目：寄存小票", { exact: true });
  await row.locator("xpath=ancestor::details[1]").evaluate((el) => {
    (el as HTMLDetailsElement).open = true;
  });
  await row.getByRole("button", { name: "更正实际总额", exact: true }).click();
  const actual = row.getByRole("form", { name: "填写实际总额", exact: true });
  await actual.getByRole("textbox").fill("27.50");
  await actual
    .getByRole("button", { name: "保存实际总额", exact: true })
    .click();
  await expect(actual).toHaveCount(0);
  const corrected = (await latest(page)).detail.itinerary!.expenses.find(
    (e) => e.id === entry.id,
  )!;
  expect(corrected.amount_cents).toBe(2750);
  expect(corrected.receipt_ids).toEqual(entry.receipt_ids);
  await noOverflow(page);
});
