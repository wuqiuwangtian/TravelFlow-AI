import { test, expect, type Page } from "playwright/test";

const proxy = "/api/backend";
const failure = {
  error: { code: "BACKEND_UNREACHABLE", message: "暂时无法保存，请重试" },
};
async function seed(page: Page) {
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  const title = `交互验收 ${crypto.randomUUID()}`;
  await page.request.patch(`${proxy}/api/sessions/${trip.session_id}`, {
    data: { title },
  });
  return { ...trip, title };
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("正常配置和配置读取失败均保留偏好入口，最近旅行支持键盘打开", async ({
  page,
}) => {
  const trip = await seed(page);
  let ready = true;
  await page.route("**/api/backend/api/system/config", async (route) => {
    if (!ready) return route.fulfill({ status: 503, json: failure });
    const data = await (await route.fetch()).json();
    data.llm.ready = data.search.ready = true;
    data.warnings = [];
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await expect(
    page.getByRole("region", { name: "新旅行偏好", exact: true }),
  ).toBeVisible();
  ready = false;
  await page.reload();
  await expect(
    page.getByRole("region", { name: "新旅行偏好", exact: true }),
  ).toBeVisible();
  const link = page.getByRole("link").filter({
    has: page.getByRole("heading", { name: trip.title, exact: true }),
  });
  await expect(link).toBeVisible();
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(`/chat/${trip.session_id}`);
  await expect(
    page.getByRole("textbox", { name: "旅行对话输入" }),
  ).toBeVisible();
});

test("配置请求仍在等待时，已有旅行和常用偏好先显示", async ({ page }) => {
  const trip = await seed(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let held = false;
  await page.route("**/api/backend/api/system/config", async (route) => {
    held = true;
    await gate;
    await route.fulfill({ status: 503, json: failure });
  });
  await page.goto("/");
  await expect.poll(() => held).toBe(true);
  await expect(
    page.getByRole("link").filter({
      has: page.getByRole("heading", { name: trip.title, exact: true }),
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "新旅行偏好", exact: true }),
  ).toBeVisible();
  release();
  await expect(page.locator("main").getByRole("alert")).toContainText(
    "服务配置暂时未能载入",
  );
  await expect(
    page.getByRole("heading", { name: trip.title, exact: true }),
  ).toBeVisible();
});

for (const view of ["sessions", "chat"] as const) {
  test(`${view} 改名等待阻止重复请求，失败保留输入，原位重试成功`, async ({
    page,
  }) => {
    const trip = await seed(page);
    await page.goto(
      view === "sessions" ? "/sessions" : `/chat/${trip.session_id}`,
    );
    const scope =
      view === "sessions"
        ? page.getByRole("listitem", {
            name: `旅行：${trip.title}`,
            exact: true,
          })
        : page.locator("main");
    await scope
      .getByRole("button", {
        name: view === "sessions" ? "重命名" : "重命名这个旅行",
        exact: true,
      })
      .click();
    const form = scope.getByRole("form", { name: "修改旅行名称", exact: true });
    const input = form.getByRole("textbox", { name: "旅行名称", exact: true });
    const draft = `保存后的旅行 ${crypto.randomUUID()}`;
    await input.fill(draft);
    let requests = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(
      `**/api/backend/api/sessions/${trip.session_id}`,
      async (route) => {
        if (route.request().method() !== "PATCH") return route.continue();
        requests++;
        if (requests > 1) return route.continue();
        await gate;
        await route.fulfill({ status: 503, json: failure });
      },
    );
    await input.press("Enter");
    await expect.poll(() => requests).toBe(1);
    await input.press("Enter");
    await expect(
      form.getByRole("button", { name: "正在保存…", exact: true }),
    ).toBeDisabled();
    expect(requests).toBe(1);
    release();
    await expect(form.getByRole("alert")).toContainText(failure.error.message);
    await expect(input).toHaveValue(draft);
    await expect(input).toBeFocused();
    await form.getByRole("button", { name: "保存名称", exact: true }).click();
    await expect(page.getByRole("form", { name: "修改旅行名称" })).toHaveCount(
      0,
    );
    const saved = await (
      await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
    ).json();
    expect(saved.session.title).toBe(draft);
    expect(saved.versions[trip.branch_id]).toHaveLength(1);
    if (view === "sessions")
      await expect(
        page.getByRole("listitem", { name: `旅行：${draft}` }),
      ).toBeVisible();
    else
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(draft);
    expect(requests).toBe(2);
  });
}

test("归档原位等待和失败重试，恢复后列表正常且手机名称不竖排", async ({
  page,
}, info) => {
  const trip = await seed(page);
  await page.goto("/sessions");
  const item = page.getByRole("listitem", {
    name: `旅行：${trip.title}`,
    exact: true,
  });
  await expect(item).toBeVisible();
  const title = item.getByRole("link", { name: trip.title, exact: true });
  expect((await title.boundingBox())!.width).toBeGreaterThan(200);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requests = 0;
  await page.route(
    `**/api/backend/api/sessions/${trip.session_id}`,
    async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      requests++;
      if (requests > 1) return route.continue();
      await gate;
      await route.fulfill({ status: 503, json: failure });
    },
  );
  await item.getByRole("button", { name: "归档", exact: true }).click();
  await expect.poll(() => requests).toBe(1);
  await expect(
    item.getByRole("button", { name: "归档中…", exact: true }),
  ).toBeDisabled();
  await expect(item.getByRole("status")).toContainText("正在归档旅行");
  await page.screenshot({ path: info.outputPath("sessions-pending.png") });
  release();
  await expect(item.getByRole("alert")).toContainText(failure.error.message);
  await item.getByRole("button", { name: "重试", exact: true }).click();
  await expect(item).toHaveCount(0);
  await page.getByRole("button", { name: "已归档", exact: true }).click();
  await expect(item).toBeVisible();
  await item.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(item).toHaveCount(0);
  await page.getByRole("button", { name: "活跃", exact: true }).click();
  await expect(item).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("旅行搜索忽略较早返回的旧查询，失败不冒充无数据", async ({ page }) => {
  const a = await seed(page),
    b = await seed(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let held = false;
  await page.route(/\/api\/backend\/api\/sessions(?:\?.*)?$/, async (route) => {
    const query = new URL(route.request().url()).searchParams.get("q");
    if (query !== a.title) return route.continue();
    const response = await route.fetch();
    held = true;
    await gate;
    await route.fulfill({ response });
  });
  await page.goto("/sessions");
  const search = page.getByRole("textbox", { name: "搜索旅行名称" });
  await search.fill(a.title);
  await expect.poll(() => held).toBe(true);
  await search.fill(b.title);
  await expect(
    page.getByRole("listitem", { name: `旅行：${b.title}` }),
  ).toBeVisible();
  const oldResponse = page.waitForResponse(
    (response) => new URL(response.url()).searchParams.get("q") === a.title,
  );
  release();
  await oldResponse;
  await expect(
    page.getByRole("listitem", { name: `旅行：${a.title}` }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("listitem", { name: `旅行：${b.title}` }),
  ).toBeVisible();
  await page.route(/\/api\/backend\/api\/sessions(?:\?.*)?$/, (route) =>
    route.fulfill({ status: 503, json: failure }),
  );
  await page.reload();
  await expect(page.locator("main").getByRole("alert")).toContainText(
    failure.error.message,
  );
  await expect(page.getByText("还没有旅行计划", { exact: true })).toHaveCount(
    0,
  );
});

test("对比初始加载失败可原页重试，不显示没有旅行", async ({ page }) => {
  await seed(page);
  let failed = true;
  await page.route(/\/api\/backend\/api\/sessions(?:\?.*)?$/, (route) =>
    failed ? route.fulfill({ status: 503, json: failure }) : route.continue(),
  );
  await page.goto("/compare");
  await expect(page.locator("main").getByRole("alert")).toContainText(
    failure.error.message,
  );
  await expect(
    page.getByText("还没有可对比的旅行", { exact: true }),
  ).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "分支对比", exact: true }),
  ).toBeVisible();
  await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
});

test("切换对比旅行后，延迟旧方案不能覆盖新选择", async ({ page }) => {
  const a = await seed(page),
    b = await seed(page);
  const fork = async (trip: typeof a, title: string) => {
    await page.request.post(
      `${proxy}/api/sessions/${trip.session_id}/branches`,
      { data: { title, parent_branch_id: trip.branch_id } },
    );
  };
  const aBranch = `旧方案 ${crypto.randomUUID()}`,
    bBranch = `新方案 ${crypto.randomUUID()}`;
  await fork(a, aBranch);
  await fork(b, bBranch);
  await page.route(/\/api\/backend\/api\/sessions(?:\?.*)?$/, async (route) => {
    const data = await (await route.fetch()).json();
    data.items = [a, b].map((trip) =>
      data.items.find((item: { id: string }) => item.id === trip.session_id),
    );
    await route.fulfill({ json: data });
  });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let held = false;
  await page.route(
    `**/api/backend/api/sessions/${a.session_id}/branches`,
    async (route) => {
      const response = await route.fetch();
      held = true;
      await gate;
      await route.fulfill({ response });
    },
  );
  await page.goto("/compare");
  await expect.poll(() => held).toBe(true);
  await page
    .getByRole("combobox", { name: "选择对比旅行" })
    .selectOption(b.session_id);
  await expect(
    page.getByRole("button").filter({ hasText: bBranch }),
  ).toBeVisible();
  const old = page.waitForResponse((response) =>
    response.url().endsWith(`/sessions/${a.session_id}/branches`),
  );
  release();
  await old;
  await expect(
    page.getByRole("button").filter({ hasText: aBranch }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button").filter({ hasText: bBranch }),
  ).toBeVisible();
});
