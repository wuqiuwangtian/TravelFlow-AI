import { expect, test, type Page } from "playwright/test";
const proxy = "/api/backend";
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
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (r) =>
    r.abort(),
  );
  const seed = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/chat/${seed.session_id}`);
  await page.getByRole("button", { name: "打开旅行回顾", exact: true }).click();
});
test("事实模板可编辑保存，刷新保留，重复保存不增加版本，导出不包含票据和未保存文字", async ({
  page,
}) => {
  const recap = page.getByRole("region", { name: "旅行回顾", exact: true });
  await expect(recap.getByLabel("回顾事实汇总", { exact: true })).toContainText(
    "尚未确认到访：公园漫步",
  );
  await expect(
    recap.getByRole("textbox", { name: "我的回忆", exact: true }),
  ).not.toHaveValue(/公园漫步/);
  const original = await latest(page);
  expect(original.versions).toHaveLength(1);
  await recap
    .getByRole("textbox", { name: "回顾标题", exact: true })
    .fill("北京的旅行片段");
  await recap
    .getByRole("textbox", { name: "我的回忆", exact: true })
    .fill("故宫留下了美好回忆。<script>alert(1)</script>");
  await recap
    .getByRole("button", { name: "保存旅行回顾", exact: true })
    .click();
  await expect(recap).toContainText("回顾已随当前版本保存");
  await page.reload();
  await expect(
    recap.getByRole("textbox", { name: "回顾标题", exact: true }),
  ).toHaveValue("北京的旅行片段");
  const saved = await latest(page);
  expect(saved.versions).toHaveLength(2);
  expect(saved.detail.itinerary.days).toEqual(original.detail.itinerary.days);
  expect(saved.detail.itinerary.expenses).toEqual(
    original.detail.itinerary.expenses,
  );
  await recap
    .getByRole("button", { name: "保存旅行回顾", exact: true })
    .click();
  await expect(recap).toContainText("回顾已随当前版本保存");
  expect((await latest(page)).versions).toHaveLength(2);
  await recap
    .getByRole("textbox", { name: "我的回忆", exact: true })
    .fill("尚未保存的文字");
  await expect(recap).toContainText("文字尚未保存");
  const link = recap.getByRole("link", { name: "导出回顾 HTML", exact: true });
  const output = await page.request.get((await link.getAttribute("href"))!);
  const html = await output.text();
  expect(html).toContain("美好回忆");
  expect(html).not.toContain("尚未保存的文字");
  expect(html).not.toContain("<script>");
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("已付款项目不能自动精简");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
});
test("修改真实活动后回顾保留原文字并提示核对，旧版本不变", async ({ page }) => {
  const recap = page.getByRole("region", { name: "旅行回顾", exact: true });
  await recap
    .getByRole("textbox", { name: "我的回忆", exact: true })
    .fill("保留这段亲手写的回忆");
  await recap
    .getByRole("button", { name: "保存旅行回顾", exact: true })
    .click();
  await expect(recap).toContainText("回顾已随当前版本保存");
  const saved = await latest(page);
  const response = await page.request.patch(
    `${proxy}/api/versions/${saved.detail.version.id}/days/2/activities/park/progress`,
    { data: { progress: "completed" } },
  );
  expect(response.ok()).toBe(true);
  await page.reload();
  await expect(recap).toContainText("行程或账目已有变化");
  await expect(
    recap.getByRole("textbox", { name: "我的回忆", exact: true }),
  ).toHaveValue("保留这段亲手写的回忆");
  await expect(recap.getByLabel("回顾事实汇总", { exact: true })).toContainText(
    "已完成：公园漫步",
  );
  const old = await (
    await page.request.get(
      `${proxy}/api/versions/${saved.detail.version.id}/recap`,
    )
  ).json();
  expect(old.stale).toBe(false);
  expect(old.days[1].completed).toEqual([]);
});
