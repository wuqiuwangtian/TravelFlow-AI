import { expect, test, type Page } from "playwright/test";
import type { VersionDetail, RecapView } from "../../lib/types";

const proxy = "/api/backend";
const recap = (page: Page) =>
  page.getByRole("region", { name: "旅行回顾", exact: true });
const failure = {
  error: {
    code: "BACKEND_UNREACHABLE",
    message: "隔离保存失败，请在这里重试。",
  },
};
type Trip = {
  session_id: string;
  branch_id: string;
  city: string;
  title: string;
  detail: VersionDetail;
  photoId: string;
};
async function picture(
  page: Page,
  width = 160,
  height = 360,
  color = "#d6c8a8",
) {
  const encoded = await page.evaluate(
    ({ width, height, color }) => {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d")!;
      context.fillStyle = color;
      context.fillRect(0, 0, width, height);
      context.fillStyle = "#355744";
      context.fillRect(10, 10, Math.min(width - 20, 80), 40);
      return canvas.toDataURL("image/png").split(",")[1];
    },
    { width, height, color },
  );
  return Buffer.from(encoded, "base64");
}
async function latest(
  page: Page,
  trip: Pick<Trip, "session_id" | "branch_id">,
) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  const versions = session.versions[trip.branch_id] as { id: string }[];
  const detail = (await (
    await page.request.get(`${proxy}/api/versions/${versions.at(-1)!.id}`)
  ).json()) as VersionDetail;
  return { versions, detail };
}
async function seed(page: Page, ended = true): Promise<Trip> {
  const city = "回忆样本" + crypto.randomUUID().slice(0, 8);
  const trip = await (
    await page.request.post(
      `${proxy}/test/career?city=${encodeURIComponent(city)}`,
    )
  ).json();
  const title = "旅程记录 " + trip.session_id;
  await page.request.patch(`${proxy}/api/sessions/${trip.session_id}`, {
    data: { title },
  });
  let { detail } = await latest(page, trip);
  const progress = await page.request.patch(
    `${proxy}/api/versions/${detail.version.id}/days/2/activities/park/progress`,
    { data: { progress: "completed" } },
  );
  expect(progress.ok()).toBe(true);
  detail = await progress.json();
  const upload = await page.request.post(
    `${proxy}/api/versions/${detail.version.id}/travel-photos`,
    { headers: { "Content-Type": "image/png" }, data: await picture(page) },
  );
  expect(upload.ok()).toBe(true);
  const photoId = (await upload.json()).id as string;
  const view = (await (
    await page.request.get(`${proxy}/api/versions/${detail.version.id}/recap`)
  ).json()) as RecapView;
  const saved = await page.request.put(
    `${proxy}/api/versions/${detail.version.id}/recap`,
    {
      data: {
        title: "一段亲手记录的旅程",
        body: "保留原来的回忆文字。",
        social_copy: "一段短文",
        source_fingerprint: view.source_fingerprint,
        photos: [
          {
            id: photoId,
            caption: "原文 <b>旅程</b> 📷",
            day: 2,
            activity_id: "park",
            included: true,
          },
        ],
        cover_photo_id: photoId,
      },
    },
  );
  expect(saved.ok()).toBe(true);
  detail = await saved.json();
  if (ended) {
    const response = await page.request.put(
      `${proxy}/api/versions/${detail.version.id}/journey-status`,
      { data: { ended: true } },
    );
    expect(response.ok()).toBe(true);
    detail = await response.json();
  }
  return { ...trip, city, title, detail, photoId };
}
async function openEditor(page: Page, trip: Trip) {
  await page.goto(`/chat/${trip.session_id}`);
  await expect(
    recap(page).getByRole("textbox", { name: "我的回忆", exact: true }),
  ).toHaveValue("保留原来的回忆文字。");
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

test("回顾保存等待防重，失败原位保留文字与照片，成功回执跨版本更新仍可见", async ({
  page,
}) => {
  const trip = await seed(page);
  await openEditor(page, trip);
  const before = await latest(page, trip);
  const memory = recap(page).getByRole("textbox", {
    name: "我的回忆",
    exact: true,
  });
  await memory.fill("新的手写回忆，保留照片和实际账目。");
  let release!: () => void,
    calls = 0;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/backend/api/versions/*/recap", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    calls++;
    if (calls > 1) return route.continue();
    await gate;
    await route.fulfill({ status: 503, json: failure });
  });
  const save = recap(page).getByRole("button", {
    name: "保存旅行回顾",
    exact: true,
  });
  await save.click();
  await expect(save).toHaveAttribute("aria-busy", "true");
  await expect(save).toBeDisabled();
  expect(calls).toBe(1);
  release();
  const alert = recap(page).getByRole("alert");
  await expect(alert).toContainText("隔离保存失败");
  await expect(alert).toBeInViewport();
  await expect(memory).toHaveValue("新的手写回忆，保留照片和实际账目。");
  expect((await latest(page, trip)).versions).toHaveLength(
    before.versions.length,
  );
  await save.click();
  await expect(
    recap(page).getByRole("status").filter({ hasText: "旅行回顾已保存" }),
  ).toBeVisible();
  await expect(memory).toHaveValue("新的手写回忆，保留照片和实际账目。");
  const after = await latest(page, trip);
  expect(after.versions).toHaveLength(before.versions.length + 1);
  expect(after.detail.itinerary!.expenses).toEqual(
    before.detail.itinerary!.expenses,
  );
  expect(after.detail.itinerary!.recap!.photos?.[0]?.id).toBe(trip.photoId);
  await noOverflow(page);
});

test("照片完整查看支持键盘关闭与焦点恢复，损坏可重试且查看不产生写入", async ({
  page,
}) => {
  const trip = await seed(page);
  let failImage = true;
  await page.route(/\/travel-photos\/[^/?]+(?:\?.*)?$/, (route) =>
    failImage ? route.abort() : route.continue(),
  );
  const writes: string[] = [];
  page.on("request", (request) => {
    if (
      request.url().includes("/api/backend/api/") &&
      request.method() !== "GET"
    )
      writes.push(request.method());
  });
  await page.goto(`/recaps/${trip.detail.version.id}`);
  await expect(recap(page)).toContainText("照片暂时无法显示");
  failImage = false;
  await recap(page)
    .getByRole("button", { name: "重试照片", exact: true })
    .click();
  const trigger = recap(page).getByRole("button", {
    name: "放大照片：原文 <b>旅程</b> 📷",
    exact: true,
  });
  await expect(trigger).toBeEnabled();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await trigger.focus();
  await page.keyboard.down("Space");
  expect(["none", "1"]).toContain(
    await trigger.evaluate((el) => getComputedStyle(el).scale),
  );
  await page.keyboard.up("Space");
  const dialog = page.getByRole("dialog", {
    name: "查看完整旅行照片",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("img")).toHaveJSProperty("naturalHeight", 360);
  await expect(dialog.getByRole("img")).toHaveCSS("object-fit", "contain");
  const bounds = await dialog.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    await page.evaluate(() => document.documentElement.clientWidth),
  );
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(writes).toEqual([]);
  await noOverflow(page);
});

test("部分照片上传失败保留已上传内容，横竖图保持同样预览尺寸且保存前不改版本", async ({
  page,
}) => {
  const trip = await seed(page, false);
  await openEditor(page, trip);
  const before = await latest(page, trip);
  const landscape = await picture(page, 360, 160, "#b9c8a3"),
    portrait = await picture(page, 160, 360, "#afc5d3");
  let calls = 0;
  await page.route("**/api/backend/api/versions/*/travel-photos", (route) => {
    calls++;
    return calls === 2
      ? route.fulfill({ status: 503, json: failure })
      : route.continue();
  });
  const file = recap(page).getByLabel("添加旅行照片", { exact: true });
  await file.setInputFiles([
    { name: "landscape.png", mimeType: "image/png", buffer: landscape },
    { name: "portrait.png", mimeType: "image/png", buffer: portrait },
  ]);
  const group = recap(page).getByRole("group", {
    name: "旅行照片",
    exact: true,
  });
  await expect(group.getByRole("alert")).toContainText("隔离保存失败");
  await expect(
    recap(page).getByRole("article", { name: "旅行照片2", exact: true }),
  ).toBeVisible();
  expect((await latest(page, trip)).versions).toHaveLength(
    before.versions.length,
  );
  await file.setInputFiles({
    name: "portrait.png",
    mimeType: "image/png",
    buffer: portrait,
  });
  const second = recap(page).getByRole("article", {
      name: "旅行照片2",
      exact: true,
    }),
    third = recap(page).getByRole("article", {
      name: "旅行照片3",
      exact: true,
    });
  await expect(third).toBeVisible();
  await second.getByRole("img").scrollIntoViewIfNeeded();
  await expect(second.getByRole("img")).toHaveJSProperty("naturalWidth", 360);
  await third.getByRole("img").scrollIntoViewIfNeeded();
  await expect(third.getByRole("img")).toHaveJSProperty("naturalHeight", 360);
  await third
    .getByRole("textbox", { name: "照片说明", exact: true })
    .fill("最后一张照片的说明");
  await expect(
    recap(page).getByRole("button", { name: "保存旅行回顾", exact: true }),
  ).toBeInViewport();
  const sizes = await page.evaluate(() =>
    [
      ...document.querySelectorAll(
        ".history-photo-list .history-photo-trigger",
      ),
    ].map((el) => el.getBoundingClientRect().height),
  );
  expect(new Set(sizes).size).toBe(1);
  await noOverflow(page);
});

test("AI生成和采用有独立反馈，原文字先保留，保存才写入版本", async ({
  page,
}) => {
  const trip = await seed(page, false);
  await openEditor(page, trip);
  const before = await latest(page, trip);
  const writer = recap(page).getByRole("region", {
    name: "AI回顾写作",
    exact: true,
  });
  let release!: () => void,
    calls = 0;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    "**/api/backend/api/versions/*/recap/draft",
    async (route) => {
      calls++;
      await gate;
      await route.fulfill({
        json: { kind: "recap", title: "样本草稿", text: "一段新的生成草稿。" },
      });
    },
  );
  const memory = recap(page).getByRole("textbox", {
    name: "我的回忆",
    exact: true,
  });
  await writer
    .getByRole("button", { name: "AI写旅行回顾", exact: true })
    .click();
  await expect(
    writer.getByRole("button", { name: "AI写旅行回顾", exact: true }),
  ).toHaveAttribute("aria-busy", "true");
  expect(calls).toBe(1);
  release();
  const draft = writer.getByRole("region", { name: "AI文字草稿", exact: true });
  await expect(draft).toBeVisible();
  await expect(memory).toHaveValue("保留原来的回忆文字。");
  await draft
    .getByRole("button", { name: "采用这份草稿", exact: true })
    .click();
  await expect(writer.getByRole("status")).toContainText("草稿已填入编辑区");
  await expect(memory).toHaveValue("一段新的生成草稿。");
  expect((await latest(page, trip)).versions).toHaveLength(
    before.versions.length,
  );
});

test("结束和重新开启的反馈不会随版本切换丢失，已有账目与历史回顾保留", async ({
  page,
}) => {
  const trip = await seed(page, false);
  await openEditor(page, trip);
  const before = await latest(page, trip);
  await recap(page)
    .getByRole("button", { name: "结束旅行并收录回顾", exact: true })
    .click();
  await expect(
    recap(page).getByRole("status").filter({ hasText: "旅行已结束并收录回顾" }),
  ).toBeVisible();
  const ended = await latest(page, trip);
  await expect(
    recap(page).getByRole("button", { name: "重新开启旅行", exact: true }),
  ).toBeEnabled();
  await recap(page)
    .getByRole("button", { name: "重新开启旅行", exact: true })
    .click();
  await expect(
    recap(page).getByRole("status").filter({ hasText: "旅行已重新开启" }),
  ).toBeVisible();
  const reopened = await latest(page, trip);
  expect(reopened.versions).toHaveLength(before.versions.length + 2);
  expect(reopened.detail.itinerary!.ended_at).toBeNull();
  expect(reopened.detail.itinerary!.expenses).toEqual(
    before.detail.itinerary!.expenses,
  );
  const history = (await (
    await page.request.get(`${proxy}/api/versions/${ended.detail.version.id}`)
  ).json()) as VersionDetail;
  expect(history.itinerary!.ended_at).toBeTruthy();
});

test("保存后的延迟读取保留用户后来输入的新回忆，不隐式再保存", async ({
  page,
}) => {
  const trip = await seed(page, false);
  await openEditor(page, trip);
  const before = await latest(page, trip);
  let held = false,
    writes = 0,
    release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/backend/api/versions/*/recap", async (route) => {
    if (route.request().method() === "PUT") {
      writes++;
      return route.continue();
    }
    const response = await route.fetch();
    held = true;
    await gate;
    await route.fulfill({ response });
  });
  const save = recap(page).getByRole("button", {
    name: "保存旅行回顾",
    exact: true,
  });
  await save.click();
  await expect.poll(() => held).toBe(true);
  await expect(save).toBeEnabled();
  const memory = recap(page).getByRole("textbox", {
    name: "我的回忆",
    exact: true,
  });
  await memory.fill("保存后接着输入的新草稿。");
  const loaded = page.waitForResponse(
    (response) =>
      response.url().endsWith("/recap") &&
      response.request().method() === "GET",
  );
  release();
  await loaded;
  await expect(memory).toHaveValue("保存后接着输入的新草稿。");
  expect(writes).toBe(1);
  expect((await latest(page, trip)).versions).toHaveLength(
    before.versions.length,
  );
});

test("回顾列表与生涯使用同一套照片查看，城市筛选与历史来源仍正确", async ({
  page,
}) => {
  const trip = await seed(page);
  await page.goto("/recaps");
  await expect(
    page.getByLabel("已结束旅行回顾", { exact: true }),
  ).toBeVisible();
  let held = false,
    release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/backend/api/recap-archive?*", async (route) => {
    if (new URL(route.request().url()).searchParams.get("q") !== trip.title)
      return route.continue();
    const response = await route.fetch();
    held = true;
    await gate;
    await route.fulfill({ response });
  });

  await page
    .getByRole("textbox", { name: "搜索回顾", exact: true })
    .fill(trip.title);
  await expect.poll(() => held).toBe(true);
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("正在整理回顾");
  release();
  const card = page.getByRole("article").filter({ hasText: trip.title });
  await expect(card).toHaveCount(1);
  await expect(
    card.getByRole("link", { name: "阅读回顾", exact: true }),
  ).toHaveAttribute("href", `/recaps/${trip.detail.version.id}`);
  await noOverflow(page);
  await page.goto("/career");
  await page.getByRole("button", { name: new RegExp(trip.city) }).click();
  const memories = page.getByRole("region", {
    name: "旅行照片回忆",
    exact: true,
  });
  await expect(memories.getByRole("article")).toHaveCount(1);
  const trigger = memories.getByRole("button", { name: /放大照片：/ });
  await expect(trigger).toBeEnabled();
  await trigger.click();
  const dialog = page.getByRole("dialog", {
    name: "查看完整旅行照片",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await dialog
    .getByRole("button", { name: "关闭完整照片", exact: true })
    .click();
  await expect(trigger).toBeFocused();
  await expect(
    memories.getByRole("link", { name: "阅读这段回忆", exact: true }),
  ).toHaveAttribute("href", `/recaps/${trip.detail.version.id}`);
  await noOverflow(page);
});
