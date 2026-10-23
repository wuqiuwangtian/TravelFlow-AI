import { expect, test, type Page } from "playwright/test";
const proxy = "/api/backend";
const inspector = (page: Page) =>
  page.getByRole("region", { name: "节点详情", exact: true });
const graph = (page: Page) =>
  page.getByRole("region", { name: "规划流程节点", exact: true });

async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[bid];
  return {
    session,
    versions,
    detail: versions.length
      ? await (
          await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
        ).json()
      : null,
  };
}
async function say(page: Page, sid: string, bid: string, message: string) {
  const response = await page.request.post(`${proxy}/api/chat`, {
    data: { session_id: sid, branch_id: bid, message },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}
async function send(page: Page, message: string) {
  await page
    .getByRole("textbox", { name: "旅行对话输入", exact: true })
    .fill(message);
  const finished = page.waitForResponse((response) =>
    response.url().endsWith("/api/chat/stream"),
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect(await (await finished).text()).toContain("event: done");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("从原方案进入四个真实节点，查看需求预算不写入且手机无横向溢出", async ({
  page,
}, info) => {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  const before = await latest(page, seed.session_id, seed.branch_id);
  await page.goto(`/chat/${seed.session_id}`);
  const writes: string[] = [];
  page.on("request", (request) => {
    if (
      request.url().includes("/api/backend/api/") &&
      request.method() !== "GET"
    )
      writes.push(request.method());
  });
  await page.getByRole("link", { name: "高级流程", exact: true }).click();
  await expect(page).toHaveURL(
    `/workflow/${seed.session_id}?branch=${seed.branch_id}`,
  );
  await expect(graph(page).getByRole("button")).toHaveCount(4);
  await expect(
    graph(page).getByRole("button", { name: "行程、路线与预算", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(inspector(page)).toContainText("¥20.00");
  await expect(inspector(page)).toContainText("预估未知项");
  await graph(page)
    .getByRole("button", { name: "需求与城市推荐", exact: true })
    .click();
  await expect(inspector(page)).toContainText("北京");
  await expect(inspector(page)).toContainText("必填需求已齐全");
  await graph(page)
    .getByRole("button", { name: "资料检索", exact: true })
    .click();
  await expect(inspector(page)).toContainText("记录不足");
  await expect(inspector(page)).toContainText("不据此推断检索节点成功运行");
  await graph(page)
    .getByRole("button", { name: "行程、路线与预算", exact: true })
    .click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await page.screenshot({
    path: info.outputPath("planning-workflow.png"),
    fullPage: true,
  });
  const after = await latest(page, seed.session_id, seed.branch_id);
  expect(after.versions).toEqual(before.versions);
  expect(after.detail).toEqual(before.detail);
  expect(writes).toEqual([]);
  await page.getByRole("link", { name: "回到当前方案", exact: true }).click();
  await expect(page).toHaveURL(
    `/chat/${seed.session_id}?branch=${seed.branch_id}`,
  );
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
});

test("分支与版本严格对应，旧快照可导出，迟到响应不覆盖当前方案且错误版本可恢复", async ({
  page,
}) => {
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await say(page, seed.session_id, seed.branch_id, "第1天午餐花了86.35元");
  const parent = await latest(page, seed.session_id, seed.branch_id);
  const fork = await (
    await page.request.post(
      `${proxy}/api/sessions/${seed.session_id}/branches`,
      { data: { title: "节点备选", parent_branch_id: seed.branch_id } },
    )
  ).json();
  const child = fork.items.find(
    (item: { title: string }) => item.title === "节点备选",
  );
  const childFirst = await latest(page, seed.session_id, child.id);
  await say(page, seed.session_id, child.id, "第1天交通花了5元");
  await page.goto(`/workflow/${seed.session_id}?branch=${child.id}`);
  await expect(
    page.getByRole("combobox", { name: "选择方案", exact: true }),
  ).toHaveValue(child.id);
  await expect(inspector(page)).toContainText("¥111.35");
  const versions = page.getByRole("combobox", {
    name: "查看版本",
    exact: true,
  });
  await versions.selectOption(childFirst.detail.version.id);
  await expect(inspector(page)).toContainText("¥106.35");
  await expect(
    page.getByRole("region", { name: "流程数据来源", exact: true }),
  ).toContainText("第1版固定快照");
  const exported = inspector(page).getByRole("link", {
    name: "导出所选版本 HTML",
    exact: true,
  });
  expect(await exported.getAttribute("href")).toContain(
    childFirst.detail.version.id,
  );
  expect(
    await (
      await page.request.get((await exported.getAttribute("href"))!)
    ).text(),
  ).toContain("106.35");
  let release!: () => void;
  let entered!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route(
    `**/api/backend/api/branches/${seed.branch_id}/workflow`,
    async (route) => {
      entered();
      await delayed;
      await route.continue();
    },
  );
  const branches = page.getByRole("combobox", {
    name: "选择方案",
    exact: true,
  });
  await branches.selectOption(seed.branch_id);
  await started;
  await branches.selectOption(child.id);
  await expect(inspector(page)).toContainText("¥111.35");
  const lateResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/api/branches/${seed.branch_id}/workflow`),
  );
  release();
  await lateResponse;
  await expect(inspector(page)).toContainText("¥111.35");
  await expect(branches).toHaveValue(child.id);
  await page.goto(
    `/workflow/${seed.session_id}?branch=${child.id}&version=${parent.detail.version.id}`,
  );
  await expect(
    page.getByRole("alert").filter({ hasText: "所选版本不存在或不属于此方案" }),
  ).toBeVisible();
  await expect(graph(page)).toHaveCount(0);
  await versions.selectOption("");
  await expect(inspector(page)).toContainText("¥111.35");
});

test("真实节点中断与加载错误能只读查看，返回重试恢复，待澄清操作不会被节点视图应用", async ({
  page,
}) => {
  const created = await (
    await page.request.post(`${proxy}/api/sessions`, {
      data: { title: "节点中断验收" },
    })
  ).json();
  const sid = created.session.id;
  const bid = created.branches[0].id;
  const failed = await page.request.post(`${proxy}/api/chat`, {
    data: {
      session_id: sid,
      branch_id: bid,
      message: `北京流程中断验收${sid}`,
    },
  });
  expect(failed.status()).toBe(502);
  let offline = true;
  await page.route(`**/api/backend/api/branches/${bid}/workflow`, (route) => {
    if (offline) {
      offline = false;
      return route.fulfill({
        status: 503,
        json: {
          error: { code: "BACKEND_UNREACHABLE", message: "流程暂时断开" },
        },
      });
    }
    return route.continue();
  });
  await page.goto(`/workflow/${sid}?branch=${bid}`);
  await expect(
    page.getByRole("alert").filter({ hasText: "流程暂时断开" }),
  ).toBeVisible();
  await expect(graph(page)).toHaveCount(0);
  await page.getByRole("button", { name: "重新载入流程", exact: true }).click();
  await expect(inspector(page)).toContainText("记录中断");
  await expect(inspector(page)).toContainText("过程记录显示此节点曾中断");
  expect((await latest(page, sid, bid)).versions).toHaveLength(0);
  await inspector(page)
    .getByRole("link", { name: "回原对话继续", exact: true })
    .click();
  await send(page, "继续");
  await page.getByRole("link", { name: "高级流程", exact: true }).click();
  await expect(inspector(page)).toContainText("已有数据");
  await expect(graph(page)).not.toContainText("记录中断");
  expect((await latest(page, sid, bid)).versions).toHaveLength(1);
  await page.getByRole("link", { name: "回到当前方案", exact: true }).click();
  await send(page, "第2站已完成");
  await page.getByRole("link", { name: "高级流程", exact: true }).click();
  await expect(
    inspector(page).getByRole("heading", { name: "确认与修改", exact: true }),
  ).toBeVisible();
  await expect(inspector(page)).toContainText("存在待澄清或待应用的操作");
  const after = await latest(page, sid, bid);
  expect(after.versions).toHaveLength(1);
  expect(
    after.detail.itinerary.days
      .flatMap((day: { activities: { progress: string }[] }) => day.activities)
      .some(
        (activity: { progress: string }) => activity.progress === "completed",
      ),
  ).toBe(false);
});
