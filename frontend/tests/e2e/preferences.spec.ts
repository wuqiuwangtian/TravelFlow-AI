import { mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test as base, type Page } from "playwright/test";

const proxy = "/api/backend";
// 两个尺寸共用一个本机偏好库；只串行化操作这个单例的场景，其余验收继续并行。
const test = base.extend<{ preferenceLock: void }>({
  preferenceLock: [
    async ({ request }, run) => {
      const lock = join(tmpdir(), "travelflow-e2e-preferences-3137.lock");
      const deadline = Date.now() + 30_000;
      while (true) {
        try {
          await mkdir(lock);
          break;
        } catch (err) {
          if (
            (err as NodeJS.ErrnoException).code !== "EEXIST" ||
            Date.now() > deadline
          )
            throw err;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
      }
      try {
        await run();
      } finally {
        try {
          const profile = await (
            await request.get(`${proxy}/api/travel-preferences`)
          ).json();
          await request.delete(
            `${proxy}/api/travel-preferences?revision=${profile.revision}`,
          );
        } finally {
          await rm(lock, { recursive: true, force: true });
        }
      }
    },
    { auto: true },
  ],
});

async function saveProfile(page: Page, preferences: string[]) {
  const current = await (
    await page.request.get(`${proxy}/api/travel-preferences`)
  ).json();
  const response = await page.request.put(`${proxy}/api/travel-preferences`, {
    data: { preferences, expected_revision: current.revision },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}
async function send(page: Page, text: string) {
  await page
    .getByRole("textbox", { name: "旅行对话输入", exact: true })
    .fill(text);
  const done = page.waitForResponse((response) =>
    response.url().endsWith("/api/chat/stream"),
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect(await (await done).text()).toContain("event: done");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
}
async function latest(page: Page, sid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[session.branches[0].id];
  const detail = versions.length
    ? await (
        await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
      ).json()
    : null;
  return { session, detail };
}
async function newTrip(page: Page, use = false) {
  await page.goto("/");
  if (use)
    await page
      .getByRole("checkbox", { name: "本次使用常用偏好", exact: true })
      .check();
  await page.getByRole("button", { name: "新建旅行", exact: true }).click();
  await expect(page).toHaveURL(/\/chat\//);
  return new URL(page.url()).pathname.split("/").at(-1)!;
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("主动保存和修改偏好，选用后对话可清空，删除不改已有旅行且新旅行默认不用", async ({
  page,
}, info) => {
  await page.goto("/preferences");
  const editor = page.getByRole("textbox", {
    name: "编辑常用偏好",
    exact: true,
  });
  await editor.fill(" 慢节奏 \n当地美食\n慢节奏");
  await page.getByRole("button", { name: "保存常用偏好", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("常用偏好已保存");
  await page.reload();
  await expect(editor).toHaveValue("慢节奏\n当地美食");
  await page.goto("/");
  await expect(
    page.getByRole("checkbox", { name: "本次使用常用偏好", exact: true }),
  ).not.toBeChecked();
  const sid = await newTrip(page, true);
  await expect(
    page.getByLabel("创建时选用的偏好", { exact: true }),
  ).toContainText("慢节奏 · 当地美食");
  await send(page, "北京三日游");
  const before = await latest(page, sid);
  expect(before.detail.requirements.preferences).toEqual([
    "慢节奏",
    "当地美食",
  ]);
  await send(page, "这次清空偏好");
  expect((await latest(page, sid)).detail.requirements.preferences).toEqual([]);
  await page.goto("/preferences");
  await expect(editor).toHaveValue("慢节奏\n当地美食");
  await editor.fill("摄影\n少走路");
  await page.getByRole("button", { name: "保存常用偏好", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "当前已保存偏好", exact: true }),
  ).toContainText("摄影");
  await page.screenshot({
    path: info.outputPath("travel-preferences.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "删除常用偏好", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("常用偏好已删除");
  const existing = await latest(page, sid);
  expect(existing.session.session.initial_preferences).toEqual([
    "慢节奏",
    "当地美食",
  ]);
  expect(existing.detail.requirements.preferences).toEqual([]);
  const untouched = await (
    await page.request.get(`${proxy}/api/versions/${before.detail.version.id}`)
  ).json();
  expect(untouched.requirements.preferences).toEqual(["慢节奏", "当地美食"]);
  const fresh = await newTrip(page);
  await expect(
    page.getByLabel("创建时选用的偏好", { exact: true }),
  ).toHaveCount(0);
  await send(page, "北京三日游");
  expect((await latest(page, fresh)).detail.requirements.preferences).toEqual(
    [],
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});

test("旧页面保存冲突保留输入，载入最新后重试回复丢失不会重复写入", async ({
  page,
}) => {
  await saveProfile(page, ["少走路"]);
  await page.goto("/preferences");
  const editor = page.getByRole("textbox", {
    name: "编辑常用偏好",
    exact: true,
  });
  await expect(editor).toHaveValue("少走路");
  await editor.fill("我的新偏好");
  await saveProfile(page, ["另一页面的偏好"]);
  await page.getByRole("button", { name: "保存常用偏好", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "常用偏好已在其他页面变化" }),
  ).toBeVisible();
  await expect(editor).toHaveValue("我的新偏好");
  await page
    .getByRole("button", { name: "载入最新偏好，保留我的输入", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "当前已保存偏好", exact: true }),
  ).toContainText("另一页面的偏好");
  await expect(editor).toHaveValue("我的新偏好");
  let lose = true;
  await page.route("**/api/backend/api/travel-preferences", async (route) => {
    if (route.request().method() !== "PUT" || !lose) return route.continue();
    lose = false;
    expect((await route.fetch()).ok()).toBe(true);
    await route.fulfill({
      status: 503,
      json: {
        error: { code: "BACKEND_UNREACHABLE", message: "保存回复丢失，请重试" },
      },
    });
  });
  await page.getByRole("button", { name: "保存常用偏好", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "保存回复丢失" }),
  ).toBeVisible();
  await expect(editor).toHaveValue("我的新偏好");
  const stored = await (
    await page.request.get(`${proxy}/api/travel-preferences`)
  ).json();
  await page.getByRole("button", { name: "保存常用偏好", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("常用偏好已保存");
  const retried = await (
    await page.request.get(`${proxy}/api/travel-preferences`)
  ).json();
  expect(retried).toEqual(stored);
  expect(retried.preferences).toEqual(["我的新偏好"]);
});

test("新建时拒绝悄悄使用变化后的偏好，可重新选择；首次对话可覆盖，加载失败仍能直接规划", async ({
  page,
}) => {
  await saveProfile(page, ["慢节奏"]);
  await page.goto("/");
  const checkbox = page.getByRole("checkbox", {
    name: "本次使用常用偏好",
    exact: true,
  });
  await checkbox.check();
  await saveProfile(page, ["博物馆"]);
  await page.getByRole("button", { name: "新建旅行", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "常用偏好已变化" }),
  ).toBeVisible();
  await expect(checkbox).not.toBeChecked();
  await expect(
    page.getByRole("region", { name: "新旅行偏好", exact: true }),
  ).toContainText("博物馆");
  await checkbox.check();
  await page.getByRole("button", { name: "新建旅行", exact: true }).click();
  await expect(page).toHaveURL(/\/chat\//);
  const sid = new URL(page.url()).pathname.split("/").at(-1)!;
  await send(page, "这次改成徒步");
  await send(page, "北京三日游");
  const record = await latest(page, sid);
  expect(record.session.session.initial_preferences).toEqual(["博物馆"]);
  expect(record.detail.requirements.preferences).toEqual(["徒步"]);
  await page.route("**/api/backend/api/travel-preferences", (route) =>
    route.request().method() === "GET" ? route.abort() : route.continue(),
  );
  const fresh = await newTrip(page);
  expect(
    (await latest(page, fresh)).session.session.initial_preferences,
  ).toEqual([]);
  await send(page, "北京三日游");
  expect((await latest(page, fresh)).detail.requirements.preferences).toEqual(
    [],
  );
});
