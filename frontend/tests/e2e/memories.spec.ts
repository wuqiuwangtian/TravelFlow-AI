import { expect, test, type Page } from "playwright/test";

const proxy = "/api/backend";
const memories = (page: Page) =>
  page.getByRole("region", { name: "旅行照片回忆", exact: true });
const footprints = (page: Page) =>
  page.getByRole("region", { name: "城市足迹", exact: true });

async function latest(page: Page, sid: string, bid: string) {
  const session = await (
    await page.request.get(`${proxy}/api/sessions/${sid}`)
  ).json();
  return (
    await page.request.get(
      `${proxy}/api/versions/${session.versions[bid].at(-1).id}`,
    )
  ).json();
}
async function picture(page: Page, color: string, map = false) {
  return Buffer.from(
    await page.evaluate(
      ({ color, map }) => {
        const canvas = document.createElement("canvas");
        canvas.width = map ? 750 : 80;
        canvas.height = map ? 420 : 60;
        const context = canvas.getContext("2d")!;
        context.fillStyle = color;
        context.fillRect(0, 0, canvas.width, canvas.height);
        if (map) {
          context.strokeStyle = "#fff";
          for (let i = 0; i < 8; i++) {
            context.beginPath();
            context.moveTo(i * 100, 0);
            context.lineTo(i * 100, 420);
            context.stroke();
          }
          context.fillStyle = "#0f766e";
          context.beginPath();
          context.arc(375, 210, 15, 0, Math.PI * 2);
          context.fill();
          context.fillStyle = "white";
          context.font = "18px sans-serif";
          context.fillText("1", 370, 216);
          context.fillStyle = "#475569";
          context.fillText("TEST MAP", 20, 30);
        }
        return canvas.toDataURL("image/png").split(",")[1];
      },
      { color, map },
    ),
    "base64",
  );
}
async function seedTrip(
  page: Page,
  city: string,
  photoCount = 1,
  located = false,
  fork = false,
) {
  const seed = await (
    await page.request.post(
      `${proxy}/test/career?city=${encodeURIComponent(city)}`,
    )
  ).json();
  const title = `照片足迹 ${seed.session_id}`;
  await page.request.patch(`${proxy}/api/sessions/${seed.session_id}`, {
    data: { title },
  });
  let detail = await latest(page, seed.session_id, seed.branch_id);
  if (located) {
    const response = await page.request.patch(
      `${proxy}/api/versions/${detail.version.id}/days/2/activities/park/progress`,
      { data: { progress: "completed" } },
    );
    expect(response.ok()).toBe(true);
    detail = await response.json();
  }
  const photos = [];
  for (let i = 0; i <= photoCount; i++) {
    const response = await page.request.post(
      `${proxy}/api/versions/${detail.version.id}/travel-photos`,
      {
        headers: { "Content-Type": "image/png" },
        data: await picture(page, `rgb(${30 + i * 20},120,140)`),
      },
    );
    expect(response.ok()).toBe(true);
    const asset = await response.json();
    photos.push({
      id: asset.id,
      caption: `${i === photoCount ? "未选私密照片" : "旅行照片"}${i + 1} ${seed.session_id} <b>原文</b>`,
      day: i === 0 ? 2 : 1,
      activity_id: i === 0 ? "park" : null,
      included: i !== photoCount,
    });
  }
  const view = await (
    await page.request.get(`${proxy}/api/versions/${detail.version.id}/recap`)
  ).json();
  const response = await page.request.put(
    `${proxy}/api/versions/${detail.version.id}/recap`,
    {
      data: {
        title: view.title,
        body: view.body,
        source_fingerprint: view.source_fingerprint,
        photos,
        cover_photo_id: photos[0].id,
      },
    },
  );
  expect(response.ok()).toBe(true);
  const saved = await response.json();
  const ended = await page.request.put(
    `${proxy}/api/versions/${saved.version.id}/journey-status`,
    { data: { ended: true } },
  );
  expect(ended.ok()).toBe(true);
  if (fork) {
    const response = await page.request.post(
      `${proxy}/api/sessions/${seed.session_id}/branches`,
      { data: { title: "同旅备选", parent_branch_id: seed.branch_id } },
    );
    const branch = (await response.json()).items.find(
      (item: { title: string }) => item.title === "同旅备选",
    );
    const child = await latest(page, seed.session_id, branch.id);
    expect(
      (
        await page.request.put(
          `${proxy}/api/versions/${child.version.id}/journey-status`,
          { data: { ended: true } },
        )
      ).ok(),
    ).toBe(true);
  }
  return { ...seed, title, city, photos, ended: await ended.json() };
}
async function openCity(page: Page, city: string) {
  await page.goto("/career");
  await page.getByRole("button", { name: new RegExp(`^${city}`) }).click();
  await expect(
    page.getByRole("heading", { name: `${city}的旅行`, exact: true }),
  ).toBeVisible();
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("已选照片按来源旅行分页去重，未选照片不显示，标注地点不增加足迹且可只读追溯", async ({
  page,
}, info) => {
  const city = info.project.name === "desktop" ? "泉州" : "洛阳";
  const seed = await seedTrip(page, city, 7, false, true);
  await openCity(page, city);
  await expect(memories(page)).toContainText("共7张");
  await expect(memories(page).getByRole("article")).toHaveCount(6);
  await expect(memories(page)).not.toContainText("未选私密照片");
  await expect(footprints(page)).toContainText("已完成停留1项");
  await expect(footprints(page)).toContainText("坐标已确认0项");
  await expect(footprints(page)).not.toContainText("公园漫步");
  await memories(page)
    .getByRole("button", { name: "下一页照片", exact: true })
    .click();
  await expect(memories(page).getByRole("article")).toHaveCount(1);
  await expect(memories(page)).toContainText("第2页");
  await memories(page)
    .getByRole("button", { name: "上一页照片", exact: true })
    .click();
  await expect(memories(page).getByRole("article")).toHaveCount(6);
  const first = memories(page).getByRole("article").first();
  await expect(first.getByRole("img")).toHaveJSProperty("naturalWidth", 80);
  await expect(first).toContainText("<b>原文</b>");
  await expect(first.locator("b")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await page.screenshot({
    path: info.outputPath("career-memories.png"),
    fullPage: true,
  });
  await first.getByRole("link", { name: "阅读这段回忆", exact: true }).click();
  await expect(page).toHaveURL(`/recaps/${seed.ended.version.id}`);
  await expect(
    page.getByRole("heading", { name: "阅读回顾", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "旅行回顾", exact: true })
      .getByRole("textbox"),
  ).toHaveCount(0);
});

test("足迹地图失败保留确认点，重试与来源编号同步，活动更正后刷新去掉旧点", async ({
  page,
}, info) => {
  const city = info.project.name === "desktop" ? "绍兴" : "扬州";
  const seed = await seedTrip(page, city, 1, true);
  const mapImage = await picture(page, "#d1e5de", true);
  let fail = true;
  const snapshots: string[] = [];
  await page.route("**/api/backend/api/travel-career/map?*", async (route) => {
    if (new URL(route.request().url()).searchParams.get("city") !== city)
      return route.fulfill({ contentType: "image/png", body: mapImage });
    snapshots.push(
      new URL(route.request().url()).searchParams.get("snapshot")!,
    );
    if (fail) {
      fail = false;
      return route.fulfill({
        status: 503,
        json: {
          error: {
            code: "PROVIDER_ERROR",
            message: "测试地图暂时失败，记录仍可查看",
          },
        },
      });
    }
    return route.fulfill({ contentType: "image/png", body: mapImage });
  });
  await openCity(page, city);
  await expect(footprints(page)).toContainText("测试地图暂时失败");
  const legend = footprints(page).getByRole("list", {
    name: "地图标号对应地点",
    exact: true,
  });
  await expect(legend).toContainText("示例公园");
  await expect(legend.getByRole("listitem")).toHaveCount(1);
  await footprints(page)
    .getByRole("button", { name: "重试足迹地图", exact: true })
    .click();
  await expect(footprints(page).getByRole("img")).toHaveJSProperty(
    "naturalWidth",
    750,
  );
  const records = await (
    await page.request.get(
      `${proxy}/api/travel-career/footprints?city=${encodeURIComponent(city)}`,
    )
  ).json();
  expect(snapshots.at(-1)).toBe(records.map_fingerprint);
  await expect(legend.getByRole("link")).toHaveAttribute(
    "href",
    `/recaps/${seed.ended.version.id}`,
  );
  await page.screenshot({
    path: info.outputPath("career-footprints.png"),
    fullPage: true,
  });
  const changed = await page.request.patch(
    `${proxy}/api/versions/${seed.ended.version.id}/days/2/activities/park/progress`,
    { data: { progress: "skipped" } },
  );
  expect(changed.ok()).toBe(true);
  await footprints(page)
    .getByRole("button", { name: "刷新足迹记录", exact: true })
    .click();
  await expect(footprints(page)).toContainText("已完成停留1项");
  await expect(footprints(page).getByRole("img")).toHaveCount(0);
  await expect(footprints(page)).not.toContainText("示例公园");
  const staleMap = await page.request.get(
    `${proxy}/api/travel-career/map?snapshot=${records.map_fingerprint}&city=${encodeURIComponent(city)}`,
  );
  expect(staleMap.status()).toBe(409);
});

test("照片列表与图片失败均可恢复，重新开启后生涯移除而旧版本仍可阅读", async ({
  page,
}, info) => {
  const city = info.project.name === "desktop" ? "大理" : "青岛";
  const seed = await seedTrip(page, city);
  let failList = true;
  let failImage = true;
  await page.route("**/api/backend/api/travel-career/memories?*", (route) => {
    if (
      failList &&
      new URL(route.request().url()).searchParams.get("city") === city
    ) {
      failList = false;
      return route.fulfill({
        status: 503,
        json: {
          error: { code: "BACKEND_UNREACHABLE", message: "照片回忆暂时断开" },
        },
      });
    }
    return route.continue();
  });
  await page.route(
    `**/api/backend/api/versions/*/travel-photos/${seed.photos[0].id}*`,
    (route) => {
      if (new URL(route.request().url()).searchParams.has("retry"))
        failImage = false;
      if (failImage) {
        return route.abort();
      }
      return route.continue();
    },
  );
  await openCity(page, city);
  await expect(memories(page).getByRole("alert")).toContainText(
    "照片回忆暂时断开",
  );
  await expect(memories(page)).not.toContainText("还没有选入");
  await memories(page)
    .getByRole("button", { name: "重试加载照片回忆", exact: true })
    .click();
  const card = memories(page).getByRole("article");
  await card.scrollIntoViewIfNeeded();
  await expect(card).toContainText("照片暂时无法显示");
  await card.getByRole("button", { name: "重试照片", exact: true }).click();
  await expect(card.getByRole("img")).toHaveJSProperty("naturalWidth", 80);
  await page
    .getByRole("article", { name: `旅行：${seed.title}`, exact: true })
    .getByRole("link", { name: "返回原方案补记", exact: true })
    .click();
  const recap = page.getByRole("region", { name: "旅行回顾", exact: true });
  await recap
    .getByRole("button", { name: "重新开启旅行", exact: true })
    .click();
  await expect(
    recap.getByRole("button", { name: "结束旅行并收录回顾", exact: true }),
  ).toBeEnabled();
  await page.goto("/career");
  const rows = await (
    await page.request.get(
      `${proxy}/api/travel-career/memories?city=${encodeURIComponent(city)}`,
    )
  ).json();
  expect(rows.total).toBe(0);
  await expect(memories(page)).not.toContainText(seed.photos[0].caption);
  await page.goto(`/recaps/${seed.ended.version.id}`);
  await expect(
    page.getByRole("heading", { name: "阅读回顾", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "旅行回顾", exact: true }),
  ).toContainText(seed.photos[0].caption);
});
