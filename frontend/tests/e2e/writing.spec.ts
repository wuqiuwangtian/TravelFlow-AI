import { expect, test, type Page } from "playwright/test";
const proxy = "/api/backend";
const draftPath = "**/api/backend/api/versions/*/recap/draft";
const recap = (page: Page) =>
  page.getByRole("region", { name: "旅行回顾", exact: true });
const draft = (page: Page) =>
  recap(page).getByRole("region", { name: "AI文字草稿", exact: true });
async function latest(page: Page) {
  const sid = page.url().split("/").at(-1);
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  const versions = session.versions[session.branches[0].id];
  const detail = await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1).id}`)
  ).json();
  return { versions, detail };
}
async function save(page: Page) {
  await recap(page)
    .getByRole("button", { name: "保存旅行回顾", exact: true })
    .click();
  await expect(recap(page)).toContainText("回顾已随当前版本保存");
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (r) =>
    r.abort(),
  );
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/chat/${seed.session_id}`);
  await page.getByRole("button", { name: "打开旅行回顾", exact: true }).click();
});
test("AI草稿先预览不覆盖，采用后可编辑保存，朋友圈文案刷新可复制", async ({
  page,
}) => {
  const original = await latest(page);
  const memory = recap(page).getByRole("textbox", {
    name: "我的回忆",
    exact: true,
  });
  await memory.fill("亲手写的回忆");
  await recap(page)
    .getByRole("button", { name: "AI写旅行回顾", exact: true })
    .click();
  await expect(draft(page)).toContainText("故宫的一段漫步");
  await expect(memory).toHaveValue("亲手写的回忆");
  expect((await latest(page)).versions).toHaveLength(1);
  await draft(page)
    .getByRole("button", { name: "丢弃草稿", exact: true })
    .click();
  await expect(draft(page)).toHaveCount(0);
  await expect(memory).toHaveValue("亲手写的回忆");
  await recap(page)
    .getByRole("button", { name: "AI写旅行回顾", exact: true })
    .click();
  await draft(page)
    .getByRole("button", { name: "采用这份草稿", exact: true })
    .click();
  await expect(memory).toHaveValue(/故宫的一段漫步/);
  await save(page);
  await recap(page)
    .getByRole("button", { name: "AI写朋友圈文案", exact: true })
    .click();
  await draft(page)
    .getByRole("button", { name: "采用这份草稿", exact: true })
    .click();
  const social = recap(page).getByRole("textbox", {
    name: "朋友圈文案",
    exact: true,
  });
  await social.fill("自己修改后的朋友圈短文");
  await save(page);
  await page.reload();
  await expect(social).toHaveValue("自己修改后的朋友圈短文");
  await page.evaluate(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          (window as unknown as { copied: string }).copied = value;
        },
      },
    }),
  );
  await recap(page)
    .getByRole("button", { name: "复制朋友圈文案", exact: true })
    .click();
  await expect(recap(page)).toContainText("已复制文案");
  expect(
    await page.evaluate(() => (window as unknown as { copied: string }).copied),
  ).toBe("自己修改后的朋友圈短文");
  const saved = await latest(page);
  expect(saved.versions).toHaveLength(3);
  expect(saved.detail.itinerary.days).toEqual(original.detail.itinerary.days);
  expect(saved.detail.itinerary.expenses).toEqual(
    original.detail.itinerary.expenses,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});
test("鉴权失败保留原文字，手动保存可用，重试与素材勾选生效，复制不可用时不崩溃", async ({
  page,
}) => {
  const memory = recap(page).getByRole("textbox", {
    name: "我的回忆",
    exact: true,
  });
  const social = recap(page).getByRole("textbox", {
    name: "朋友圈文案",
    exact: true,
  });
  await memory.fill("希望保留的手写文字");
  await social.fill("希望保留的短文");
  await page.route(draftPath, (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "LLM_AUTH_FAILED",
          message: "模型鉴权失败，请在设置中检查模型连接。",
        },
      }),
    }),
  );
  await recap(page)
    .getByRole("button", { name: "AI写旅行回顾", exact: true })
    .click();
  await expect(recap(page).getByRole("alert")).toContainText("模型鉴权失败");
  await expect(memory).toHaveValue("希望保留的手写文字");
  await expect(social).toHaveValue("希望保留的短文");
  expect((await latest(page)).versions).toHaveLength(1);
  await save(page);
  await page.unroute(draftPath);
  await recap(page)
    .getByRole("checkbox", { name: "使用「我的回忆」文字", exact: true })
    .check();
  await recap(page)
    .getByRole("checkbox", { name: "包含已记支出汇总", exact: true })
    .check();
  const request = page.waitForRequest((r) => r.url().endsWith("/recap/draft"));
  await recap(page)
    .getByRole("button", { name: "AI写朋友圈文案", exact: true })
    .click();
  expect((await request).postDataJSON()).toMatchObject({
    memory: "希望保留的手写文字",
    include_spending: true,
  });
  await expect(draft(page)).toBeVisible();
  await draft(page)
    .getByRole("button", { name: "丢弃草稿", exact: true })
    .click();
  await page.evaluate(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    }),
  );
  await recap(page)
    .getByRole("button", { name: "复制朋友圈文案", exact: true })
    .click();
  await expect(recap(page)).toContainText("复制未成功");
  await expect(social).toHaveValue("希望保留的短文");
});
test("生成前有并发修改会拒绝旧版本草稿，原输入仍保留", async ({ page }) => {
  const before = await latest(page);
  const memory = recap(page).getByRole("textbox", {
    name: "我的回忆",
    exact: true,
  });
  await memory.fill("尚未保存但不能被覆盖的回忆");
  await page.route(draftPath, async (route) => {
    const response = await page.request.patch(
      `${proxy}/api/versions/${before.detail.version.id}/days/2/activities/park/progress`,
      { data: { progress: "completed" } },
    );
    expect(response.ok()).toBe(true);
    await route.continue();
  });
  await recap(page)
    .getByRole("button", { name: "AI写旅行回顾", exact: true })
    .click();
  await expect(recap(page).getByRole("alert")).toContainText("历史版本");
  await expect(draft(page)).toHaveCount(0);
  await expect(memory).toHaveValue("尚未保存但不能被覆盖的回忆");
  expect((await latest(page)).versions).toHaveLength(2);
});
