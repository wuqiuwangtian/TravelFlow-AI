import { expect, test } from "playwright/test";

const proxy = "/api/backend";

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
  const created = await (
    await page.request.post(`${proxy}/api/sessions`, {
      data: { title: "消息恢复验收" },
    })
  ).json();
  const response = await page.request.post(`${proxy}/api/chat`, {
    data: {
      session_id: created.session.id,
      branch_id: created.branches[0].id,
      message: "北京三日游",
    },
  });
  expect(response.ok()).toBe(true);
  await page.goto(`/chat/${created.session.id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
});

test("已记账但回复丢失时恢复输入，刷新后重试不会重复记账", async ({ page }) => {
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
  const input = page.getByRole("textbox", {
    name: "旅行对话输入",
    exact: true,
  });
  await input.fill("第1天午餐花了86.35元");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByLabel("消息发送未完成", { exact: true }),
  ).toContainText("回复中断");
  await expect(input).toHaveValue("第1天午餐花了86.35元");
  await page.reload();
  await expect(input).toHaveValue("第1天午餐花了86.35元");
  await page.getByRole("button", { name: "重试这条消息", exact: true }).click();
  await expect(page.getByLabel("消息发送未完成", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
  await expect(input).toHaveValue("");
  const sid = page.url().split("/").at(-1);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[session.branches[0].id];
  expect(versions).toHaveLength(2);
  const current = await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
  ).json();
  expect(current.itinerary.expenses).toHaveLength(1);
  expect(current.itinerary.expenses[0].amount_cents).toBe(8635);
});

test("后端不可达和鉴权失败时保留输入，快捷修改也保留未发送草稿", async ({
  page,
}) => {
  let failure = "network";
  await page.route("**/api/backend/api/chat/stream", async (route) => {
    if (failure === "network") return route.abort("connectionfailed");
    if (failure === "timeout")
      return route.fulfill({
        status: 504,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "BACKEND_TIMEOUT",
            message: "服务处理超时，已保存的行程不受影响，请重试。",
          },
        }),
      });
    if (failure === "auth")
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: error\ndata: {"code":"LLM_AUTH_FAILED","message":"模型服务鉴权失败，请检查 API Key"}\n\n',
      });
    return route.continue();
  });
  const input = page.getByRole("textbox", {
    name: "旅行对话输入",
    exact: true,
  });
  await input.fill("第1天地铁花了5元");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByLabel("消息发送未完成", { exact: true }),
  ).toBeVisible();
  await expect(input).toHaveValue("第1天地铁花了5元");
  failure = "timeout";
  await page.getByRole("button", { name: "重试这条消息", exact: true }).click();
  await expect(
    page.getByLabel("消息发送未完成", { exact: true }),
  ).toContainText("超时");
  await expect(input).toHaveValue("第1天地铁花了5元");
  failure = "auth";
  await page.getByRole("button", { name: "重试这条消息", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "检查模型设置", exact: true }),
  ).toBeVisible();
  await expect(input).toHaveValue("第1天地铁花了5元");
  failure = "none";
  await page.getByRole("button", { name: "重试这条消息", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("消息发送未完成", { exact: true })).toHaveCount(
    0,
  );
  await input.fill("这是还没发送的旅行想法");
  await page.getByRole("button", { name: "轻松一点", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
  await expect(input).toHaveValue("这是还没发送的旅行想法");
  await page.reload();
  await expect(input).toHaveValue("这是还没发送的旅行想法");
});

test("切换分支分别恢复各自的未发送草稿", async ({ page }) => {
  const sid = page.url().split("/").at(-1);
  const initial = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const branches = await page.request.post(
    `${proxy}/api/sessions/${sid}/branches`,
    { data: { title: "隔离方案", parent_branch_id: initial.branches[0].id } },
  );
  expect(branches.ok()).toBe(true);
  const input = page.getByRole("textbox", {
    name: "旅行对话输入",
    exact: true,
  });
  await input.fill("主方案的未发送草稿");
  await page.reload();
  await expect(input).toHaveValue("主方案的未发送草稿");
  await page.getByRole("button", { name: "隔离方案", exact: true }).click();
  await expect(input).toHaveValue("");
  await input.fill("另一个方案的草稿");
  await page.getByRole("button", { name: "主方案", exact: true }).click();
  await expect(input).toHaveValue("主方案的未发送草稿");
  await page.getByRole("button", { name: "隔离方案", exact: true }).click();
  await expect(input).toHaveValue("另一个方案的草稿");
});

for (const interruptShortcut of [false, true]) {
  test(`失败记账后使用快捷建议${interruptShortcut ? "并再次断流" : ""}，保留原请求以防重复记账`, async ({
    page,
  }) => {
    let count = 0;
    const ids: string[] = [];
    await page.route("**/api/backend/api/chat/stream", async (route) => {
      const payload = route.request().postDataJSON();
      ids.push(payload.request_id);
      count++;
      if (count !== 1 && !(interruptShortcut && count === 2))
        return route.continue();
      const response = await route.fetch();
      expect(await response.text()).toContain("event: done");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "",
      });
    });
    const input = page.getByRole("textbox", {
      name: "旅行对话输入",
      exact: true,
    });
    await input.fill("第1天午餐花了86.35元");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(
      page.getByLabel("消息发送未完成", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "轻松一点", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "发送", exact: true }),
    ).toBeEnabled();
    await page.reload();
    await expect(input).toHaveValue("第1天午餐花了86.35元");
    if (interruptShortcut) {
      await page
        .getByRole("button", { name: "重试这条消息", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "发送", exact: true }),
      ).toBeEnabled();
    }
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "发送", exact: true }),
    ).toBeEnabled();
    await expect(
      page.getByLabel("消息发送未完成", { exact: true }),
    ).toHaveCount(0);
    expect(ids.at(-1)).toBe(ids[0]);
    const sid = page.url().split("/").at(-1);
    const session = await (
      await page.request.get(`${proxy}/api/sessions/${sid}`)
    ).json();
    const versions = session.versions[session.branches[0].id];
    const current = await (
      await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
    ).json();
    expect(current.itinerary.expenses).toHaveLength(1);
    expect(current.itinerary.expenses[0].amount_cents).toBe(8635);
  });
}
