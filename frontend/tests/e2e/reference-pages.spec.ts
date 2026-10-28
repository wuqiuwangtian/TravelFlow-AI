import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const failure = {
  error: {
    code: "BACKEND_UNREACHABLE",
    message: "隔离方案读取失败，可保留其他方案重试。",
  },
};
async function seed(page: Page, extra = 2) {
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  for (let i = 1; i <= extra; i++) {
    const response = await page.request.post(
      `${proxy}/api/sessions/${trip.session_id}/branches`,
      {
        data: {
          title: `方案${i} ${crypto.randomUUID().slice(0, 6)}`,
          parent_branch_id: trip.branch_id,
        },
      },
    );
    expect(response.ok()).toBe(true);
  }
  const detail = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  return {
    ...trip,
    branches: detail.branches as {
      id: string;
      title: string;
      is_primary: boolean;
    }[],
    versions: detail.versions,
  };
}
async function onlyTrips(page: Page, ids: string[]) {
  await page.route(/\/api\/backend\/api\/sessions(?:\?.*)?$/, async (route) => {
    const response = await route.fetch(),
      data = await response.json();
    data.items = ids.map((id) =>
      data.items.find((item: { id: string }) => item.id === id),
    );
    data.total = data.items.length;
    await route.fulfill({ json: data });
  });
}
function gate() {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { wait, release };
}
function column(page: Page, title: string) {
  return page.getByRole("region", { name: `对比方案：${title}`, exact: true });
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

test("比较各列独立加载，失败只重试本列且保留已读位置", async ({
  page,
  isMobile,
}) => {
  const trip = await seed(page),
    [first, slow, broken] = trip.branches;
  await onlyTrips(page, [trip.session_id]);
  const held = gate();
  let waiting = false,
    brokenCalls = 0,
    firstCalls = 0;
  await page.route(
    `**/api/backend/api/branches/${first.id}/versions`,
    (route) => {
      firstCalls++;
      return route.continue();
    },
  );
  await page.route(
    `**/api/backend/api/branches/${slow.id}/versions`,
    async (route) => {
      const response = await route.fetch();
      waiting = true;
      await held.wait;
      await route.fulfill({ response });
    },
  );
  await page.route(
    `**/api/backend/api/branches/${broken.id}/versions`,
    (route) => {
      brokenCalls++;
      return brokenCalls === 1
        ? route.fulfill({ status: 503, json: failure })
        : route.continue();
    },
  );
  await page.goto("/compare");
  await expect.poll(() => waiting).toBe(true);
  await expect(
    column(page, first.title).locator(".prose-report"),
  ).toBeVisible();
  await expect(column(page, slow.title).getByRole("status")).toContainText(
    "正在加载最新路书",
  );
  await expect(column(page, broken.title).getByRole("alert")).toContainText(
    failure.error.message,
  );
  const body = column(page, first.title).locator(".tf-compare-content");
  if (!isMobile)
    await body.evaluate((el) => {
      el.scrollTop = 35;
    });
  const position = await body.evaluate((el) => el.scrollTop);
  await column(page, broken.title)
    .getByRole("button", { name: "重试", exact: true })
    .click();
  await expect(
    column(page, broken.title).locator(".prose-report"),
  ).toBeVisible();
  expect(firstCalls).toBe(1);
  expect(brokenCalls).toBe(2);
  expect(await body.evaluate((el) => el.scrollTop)).toBe(position);
  held.release();
  await expect(column(page, slow.title).locator(".prose-report")).toBeVisible();
  await noOverflow(page);
});

test("三方案上限可理解，取消全部有入口，设为当前不刷新其他正文或新增版本", async ({
  page,
}) => {
  const trip = await seed(page, 3),
    [first, second, third, fourth] = trip.branches;
  await onlyTrips(page, [trip.session_id]);
  let firstCalls = 0;
  await page.route(
    `**/api/backend/api/branches/${first.id}/versions`,
    (route) => {
      firstCalls++;
      return route.continue();
    },
  );
  await page.goto("/compare");
  const choices = page.getByRole("group", { name: "选择对比方案" });
  const choice = (title: string) =>
    choices.getByRole("button").filter({ hasText: title });
  await expect(choice(fourth.title)).toBeDisabled();
  await expect(page.getByText(/已选 3 \/ 3 个方案/)).toContainText("取消一个");
  await expect(
    column(page, first.title).locator(".prose-report"),
  ).toBeVisible();
  await column(page, second.title)
    .getByRole("button", { name: "设为当前", exact: true })
    .click();
  await expect(column(page, second.title).getByRole("status")).toContainText(
    "已设为当前方案",
  );
  expect(firstCalls).toBe(1);
  const saved = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  expect(
    saved.branches.find((b: { is_primary: boolean }) => b.is_primary).id,
  ).toBe(second.id);
  expect(saved.versions).toEqual(trip.versions);
  for (const branch of [first, second, third])
    await choice(branch.title).click();
  await expect(
    page.getByText("选择想比较的方案", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".tf-compare-column")).toHaveCount(0);
  await expect(choice(fourth.title)).toBeEnabled();
  await choice(fourth.title).click();
  await expect(
    column(page, fourth.title).locator(".prose-report"),
  ).toBeVisible();
  await expect(
    column(page, fourth.title).getByRole("link", {
      name: `继续编辑：${fourth.title}`,
      exact: true,
    }),
  ).toHaveAttribute("href", `/chat/${trip.session_id}?branch=${fourth.id}`);
});

test("切换旅行后迟到的路书和设为当前回执不会污染新旅行", async ({ page }) => {
  const a = await seed(page, 1),
    b = await seed(page, 0);
  await onlyTrips(page, [a.session_id, b.session_id]);
  const versionGate = gate(),
    primaryGate = gate();
  let versionWaiting = false,
    primaryWaiting = false;
  const branch = a.branches[1];
  await page.route(
    `**/api/backend/api/versions/${a.versions[branch.id].at(-1).id}`,
    async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      data.markdown = "迟到旧正文";
      versionWaiting = true;
      await versionGate.wait;
      await route.fulfill({ json: data });
    },
  );
  await page.route(
    `**/api/backend/api/branches/${branch.id}/primary`,
    async (route) => {
      const response = await route.fetch();
      primaryWaiting = true;
      await primaryGate.wait;
      await route.fulfill({ response });
    },
  );
  await page.goto("/compare");
  await expect.poll(() => versionWaiting).toBe(true);
  await column(page, branch.title)
    .getByRole("button", { name: "设为当前", exact: true })
    .click();
  await expect.poll(() => primaryWaiting).toBe(true);
  await page
    .getByRole("combobox", { name: "选择对比旅行" })
    .selectOption(b.session_id);
  await expect(
    column(page, b.branches[0].title).locator(".prose-report"),
  ).toBeVisible();
  await expect(page.locator("main").getByRole("status")).toContainText(
    "正在为上一份旅行设置当前方案",
  );
  const responses = Promise.all([
    page.waitForResponse((r) =>
      r.url().endsWith(`/versions/${a.versions[branch.id].at(-1).id}`),
    ),
    page.waitForResponse((r) =>
      r.url().endsWith(`/branches/${branch.id}/primary`),
    ),
  ]);
  versionGate.release();
  primaryGate.release();
  await responses;
  await expect(page.getByText("迟到旧正文", { exact: true })).toHaveCount(0);
  await expect(page.getByText("已设为当前方案。", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.locator(".tf-compare-column")).toHaveCount(1);
  await expect(
    page.getByRole("combobox", { name: "选择对比旅行" }),
  ).toHaveValue(b.session_id);
});

test("长名称和原文保留，手机纵向比较可跳转，继续编辑定位正确分支", async ({
  page,
  isMobile,
}) => {
  const trip = await seed(page, 1),
    branch = trip.branches[1];
  const title = "海边慢游".repeat(20) + " 📷 <b>原名</b>";
  await onlyTrips(page, [trip.session_id]);
  await page.route(
    `**/api/backend/api/sessions/${trip.session_id}/branches`,
    async (route) => {
      const data = await (await route.fetch()).json();
      data.items.find((b: { id: string }) => b.id === branch.id).title = title;
      await route.fulfill({ json: data });
    },
  );
  await page.route(
    `**/api/backend/api/versions/${trip.versions[branch.id].at(-1).id}`,
    async (route) => {
      const data = await (await route.fetch()).json();
      data.markdown =
        "# 阅读测试\n\n" +
        "https://example.invalid/" +
        "longsegment".repeat(60) +
        "\n\n```text\n" +
        "long-code".repeat(60) +
        "\n```\n\n用户原文 📷 <b>保留</b>";
      await route.fulfill({ json: data });
    },
  );
  await page.goto("/compare");
  const target = column(page, title);
  await expect(target.locator(".prose-report")).toContainText("用户原文 📷");
  await expect(target.getByRole("heading", { level: 2 })).toHaveText(title);
  await noOverflow(page);
  if (isMobile) {
    const jumps = page.getByRole("navigation", { name: "跳转到对比方案" });
    await jumps.getByRole("link", { name: title, exact: true }).click();
    await expect(target.getByRole("heading", { level: 2 })).toBeInViewport();
    expect(
      await target
        .locator(".tf-compare-content")
        .evaluate((el) => getComputedStyle(el).maxHeight),
    ).toBe("none");
    await target
      .getByRole("link", { name: "返回方案选择", exact: true })
      .click();
    await expect(
      page.getByRole("combobox", { name: "选择对比旅行" }),
    ).toBeInViewport();
  } else {
    expect(
      await page
        .locator(".tf-compare-grid")
        .evaluate(
          (el) => getComputedStyle(el).gridTemplateColumns.split(" ").length,
        ),
    ).toBe(2);
  }
  await page.unroute(
    `**/api/backend/api/versions/${trip.versions[branch.id].at(-1).id}`,
  );
  await target
    .getByRole("link", { name: `继续编辑：${title}`, exact: true })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/chat/${trip.session_id}\\?branch=${branch.id}`),
  );
  await expect(
    page.getByRole("button").filter({ hasText: branch.title }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("没有路书是正常空态，分支列表失败可重试且不残留旧正文", async ({
  page,
}) => {
  const trip = await seed(page, 0),
    branch = trip.branches[0];
  await onlyTrips(page, [trip.session_id]);
  let requests = 0;
  await page.route(
    `**/api/backend/api/sessions/${trip.session_id}/branches`,
    (route) => {
      requests++;
      return requests === 1
        ? route.fulfill({ status: 503, json: failure })
        : route.continue();
    },
  );
  await page.route(
    `**/api/backend/api/branches/${branch.id}/versions`,
    (route) => route.fulfill({ json: { items: [] } }),
  );
  await page.goto("/compare");
  await expect(page.locator("main").getByRole("alert")).toContainText(
    failure.error.message,
  );
  await expect(page.locator(".tf-compare-column")).toHaveCount(0);
  await page
    .locator("main")
    .getByRole("button", { name: "重试", exact: true })
    .click();
  await expect(
    column(page, branch.title).getByText("该分支还没有路书", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
  await expect(
    column(page, branch.title).getByRole("link", {
      name: `继续编辑：${branch.title}`,
    }),
  ).toBeVisible();
});

test("帮助按旅行阶段直达，问题支持键盘展开和减少动画，外链明确新窗口", async ({
  page,
  isMobile,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/help");
  await expect(
    page.getByRole("heading", { name: "使用指南", exact: true }),
  ).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "指南章节" });
  await navigation
    .getByRole("link", { name: "回来后，留下自己的旅行记录", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "回来后，留下自己的旅行记录",
      exact: true,
    }),
  ).toBeInViewport();
  const summary = page.locator("#guide-branches summary");
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#guide-branches")).toHaveAttribute("open", "");
  await expect(page.locator("#guide-branches")).toContainText(
    "节点、端口和连线",
  );
  expect(
    await summary.evaluate((el) =>
      parseFloat(getComputedStyle(el).transitionDuration),
    ),
  ).toBeLessThanOrEqual(0.001);
  await page.keyboard.press("Enter");
  await expect(page.locator("#guide-branches")).not.toHaveAttribute("open", "");
  await page.locator("#guide-privacy summary").click();
  await expect(page.locator("#guide-privacy")).toContainText(
    "运行 TravelFlow 服务的机器上",
  );
  await page.locator("#guide-services summary").click();
  const external = page.locator(".tf-help-external a");
  await expect(external).toHaveCount(4);
  for (const link of await external.all()) {
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await expect(link).toContainText("新窗口");
    if (isMobile)
      expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await noOverflow(page);
  await expect(
    page.locator("main").getByRole("link", { name: "配置服务", exact: true }),
  ).toHaveAttribute("href", "/settings");
});
