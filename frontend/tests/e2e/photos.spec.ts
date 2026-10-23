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
const recap = (page: Page) =>
  page.getByRole("region", { name: "旅行回顾", exact: true });
const photo = (page: Page) =>
  recap(page).getByRole("article", { name: "旅行照片1", exact: true });
async function addPhoto(page: Page) {
  const encoded = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 400;
    canvas.height = 300;
    const ctx = canvas.getContext("2d")!;
    const pixels = ctx.createImageData(400, 300);
    let seed = 12345;
    for (let i = 0; i < pixels.data.length; i += 4) {
      for (let j = 0; j < 3; j++) {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        pixels.data[i + j] = seed >>> 24;
      }
      pixels.data[i + 3] = 255;
    }
    ctx.putImageData(pixels, 0, 0);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const largeImage = Buffer.from(encoded, "base64");
  expect(largeImage.length).toBeGreaterThan(256 * 1024);
  await recap(page).getByLabel("添加旅行照片", { exact: true }).setInputFiles({
    name: "travel.png",
    mimeType: "image/png",
    buffer: largeImage,
  });
  await expect(photo(page)).toBeVisible();
  await expect(photo(page).getByRole("img")).toHaveJSProperty(
    "naturalWidth",
    400,
  );
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

test("照片可标注日期地点、选封面、刷新恢复，图文HTML带图离线查看且不改变实际到访", async ({
  page,
}, info) => {
  const original = await latest(page);
  await addPhoto(page);
  expect((await latest(page)).versions).toHaveLength(1);
  await photo(page)
    .getByRole("textbox", { name: "照片说明", exact: true })
    .fill("旅行封面 <script>bad</script>");
  await photo(page)
    .getByRole("combobox", { name: "照片日期", exact: true })
    .selectOption("1");
  await photo(page)
    .getByRole("combobox", { name: "照片关联地点", exact: true })
    .selectOption("park");
  await photo(page)
    .getByRole("button", { name: "设为封面并选入", exact: true })
    .click();
  await expect(
    photo(page).getByRole("checkbox", { name: "选入图文回顾", exact: true }),
  ).toBeChecked();
  await save(page);
  await page.reload();
  await expect(
    photo(page).getByRole("textbox", { name: "照片说明", exact: true }),
  ).toHaveValue("旅行封面 <script>bad</script>");
  await expect(
    photo(page).getByRole("button", { name: "当前封面", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  const saved = await latest(page);
  expect(saved.versions).toHaveLength(2);
  expect(saved.detail.itinerary.days).toEqual(original.detail.itinerary.days);
  expect(saved.detail.itinerary.expenses).toEqual(
    original.detail.itinerary.expenses,
  );
  const link = recap(page).getByRole("link", {
    name: "导出图文 HTML",
    exact: true,
  });
  const exported = await page.request.get((await link.getAttribute("href"))!);
  const html = await exported.text();
  expect(html).toContain('src="data:image/jpeg;base64,');
  expect(html).not.toContain("<script>");
  const offline = await page.context().newPage();
  await offline.route("**/*", (r) => r.abort());
  await offline.setContent(html);
  await expect(offline.getByRole("img")).toHaveJSProperty("naturalWidth", 400);
  await expect(offline.locator("h1")).toContainText("旅行回顾");
  expect(
    await offline.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await offline.screenshot({
    path: info.outputPath("offline-photo-recap.png"),
    fullPage: true,
  });
  await offline.close();
  const old = await (
    await page.request.get(
      `${proxy}/api/versions/${original.detail.version.id}/recap`,
    )
  ).json();
  expect(old.photos).toEqual([]);
});

test("未选照片不进导出，取消选择清除封面，移除只影响新版本", async ({
  page,
}) => {
  await addPhoto(page);
  await photo(page)
    .getByRole("textbox", { name: "照片说明", exact: true })
    .fill("留在本机的照片说明");
  await save(page);
  await expect(
    recap(page).getByRole("link", { name: "导出图文 HTML", exact: true }),
  ).toHaveCount(0);
  const saved = await latest(page);
  const output = await page.request.get(
    `${proxy}/api/versions/${saved.detail.version.id}/recap/export?format=html&include_photos=true`,
  );
  expect(await output.text()).not.toContain('src="data:image');
  expect(await output.text()).not.toContain("留在本机的照片说明");
  await photo(page)
    .getByRole("button", { name: "设为封面并选入", exact: true })
    .click();
  await photo(page)
    .getByRole("checkbox", { name: "选入图文回顾", exact: true })
    .uncheck();
  await expect(
    photo(page).getByRole("button", { name: "当前封面", exact: true }),
  ).toHaveCount(0);
  await photo(page)
    .getByRole("button", { name: "移除照片", exact: true })
    .click();
  await save(page);
  await expect(photo(page)).toHaveCount(0);
  const newest = await latest(page);
  expect(newest.detail.itinerary.recap.photos).toEqual([]);
  const old = await (
    await page.request.get(
      `${proxy}/api/versions/${saved.detail.version.id}/recap`,
    )
  ).json();
  expect(old.photos).toHaveLength(1);
});

test("无效图片失败保留回顾文字，重新上传可恢复，重复上传不重复关联", async ({
  page,
}) => {
  await recap(page)
    .getByRole("textbox", { name: "我的回忆", exact: true })
    .fill("没有丢失的回忆");
  await recap(page)
    .getByLabel("添加旅行照片", { exact: true })
    .setInputFiles({
      name: "bad.png",
      mimeType: "image/png",
      buffer: Buffer.from("broken"),
    });
  await expect(recap(page).getByRole("alert")).toContainText("图片无法解码");
  await expect(
    recap(page).getByRole("textbox", { name: "我的回忆", exact: true }),
  ).toHaveValue("没有丢失的回忆");
  await addPhoto(page);
  await addPhoto(page);
  await expect(photo(page)).toHaveCount(1);
  await save(page);
  expect((await latest(page)).detail.itinerary.recap.photos).toHaveLength(1);
});
