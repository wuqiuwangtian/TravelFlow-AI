import { expect, test, type Page, type Locator } from "playwright/test";

const proxy = "/api/backend",
  key = "travelflow.theme";
const choice = (page: Page, theme: string) =>
  page.locator(`[data-theme-option="${theme}"]`);
async function install(page: Page, theme: string) {
  await page.addInitScript(
    ({ key, theme }) => localStorage.setItem(key, theme),
    { key, theme },
  );
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
async function seed(page: Page) {
  const trip = await (
    await page.request.post(`${proxy}/test/career?city=主题验收`)
  ).json();
  let session = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  let version = session.versions[trip.branch_id].at(-1).id;
  const progress = await page.request.patch(
    `${proxy}/api/versions/${version}/days/2/activities/park/progress`,
    { data: { progress: "completed" } },
  );
  expect(progress.ok()).toBe(true);
  version = (await progress.json()).version.id;
  const view = await (
    await page.request.get(`${proxy}/api/versions/${version}/recap`)
  ).json();
  const recap = await page.request.put(
    `${proxy}/api/versions/${version}/recap`,
    {
      data: {
        title: "主题验收回顾",
        body: "用户原文 📷，保持原样。",
        social_copy: "一段文字",
        source_fingerprint: view.source_fingerprint,
        photos: [],
        cover_photo_id: null,
      },
    },
  );
  expect(recap.ok()).toBe(true);
  version = (await recap.json()).version.id;
  const ended = await page.request.put(
    `${proxy}/api/versions/${version}/journey-status`,
    { data: { ended: true } },
  );
  expect(ended.ok()).toBe(true);
  version = (await ended.json()).version.id;
  session = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  return { ...trip, version, versions: session.versions };
}
/** Composite inherited transparent backgrounds, using canvas to resolve CSS colors. */
async function readable(locator: Locator) {
  const failures = await locator.evaluateAll((elements) => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    const rgba = (color: string) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 1, 1);
      return Array.from(ctx.getImageData(0, 0, 1, 1).data).map((v, i) =>
        i === 3 ? v / 255 : v,
      );
    };
    const luminance = (rgb: number[]) => {
      const c = rgb.slice(0, 3).map((v) => {
        v /= 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      });
      return c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722;
    };
    return elements.flatMap((el) => {
      if (
        !el.getClientRects().length ||
        el.closest(
          "[disabled], [aria-disabled=true], .studio-port, .tf-theme-sample",
        ) ||
        !el.textContent?.trim()
      )
        return [];
      let node: Element | null = el;
      const chain: Element[] = [];
      while (node) {
        chain.unshift(node);
        node = node.parentElement;
      }
      let bg = [255, 255, 255];
      for (const ancestor of chain) {
        const color = rgba(getComputedStyle(ancestor).backgroundColor);
        bg = bg.map((v, i) => color[i] * color[3] + v * (1 - color[3]));
      }
      const fg = rgba(getComputedStyle(el).color);
      const rgb = bg.map((v, i) => fg[i] * fg[3] + v * (1 - fg[3]));
      const a = luminance(rgb),
        b = luminance(bg),
        ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      return ratio >= 4.5
        ? []
        : [
            {
              text: el.textContent.trim().slice(0, 42),
              ratio: Math.round(ratio * 100) / 100,
              color: getComputedStyle(el).color,
              background: bg,
            },
          ];
    });
  });
  expect(failures).toEqual([]);
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("默认 A 独立于系统暗色，切换不保存服务配置或丢掉密钥草稿", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  let writes = 0;
  page.on("request", (r) => {
    if (r.url().includes("/api/backend/") && r.method() !== "GET") writes++;
  });
  await page.goto("/settings");
  await expect(page.locator("#llm-key")).toBeVisible();
  await expect(choice(page, "paper")).toHaveAttribute("aria-pressed", "true");
  await page.locator("#llm-key").fill("fixture-unsaved-private-value");
  await choice(page, "night").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  await expect(page.locator("#llm-key")).toHaveValue(
    "fixture-unsaved-private-value",
  );
  await expect(
    page
      .getByRole("region", { name: "外观主题", exact: true })
      .getByRole("status"),
  ).toContainText("在此浏览器保存");
  expect(await page.evaluate((key) => localStorage.getItem(key), key)).toBe(
    "night",
  );
  expect(writes).toBe(0);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "帮助", exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
});

test("已保存 C 在正文首次出现前生效，非法存储值回退 A，刷新无水合错误", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error" && /hydration|hydrate/i.test(msg.text()))
      errors.push(msg.text());
  });
  await install(page, "night");
  await page.addInitScript(() => {
    const observer = new MutationObserver(() => {
      if (document.body) {
        (window as Window & { __firstTheme?: string }).__firstTheme =
          document.documentElement.dataset.theme;
        observer.disconnect();
      }
    });
    observer.observe(document, { childList: true, subtree: true });
  });
  await page.goto("/help");
  expect(
    await page.evaluate(
      () => (window as Window & { __firstTheme?: string }).__firstTheme,
    ),
  ).toBe("night");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  expect(errors).toEqual([]);
  // A separate fresh page has no initialization script that overwrites the invalid stored value.
  const other = await page.context().newPage();
  await other.goto("/help");
  await other.evaluate(
    (key) => localStorage.setItem(key, 'night";window.themeInjected=true;//'),
    key,
  );
  await other.reload();
  await expect(other.locator("html")).toHaveAttribute("data-theme", "paper");
  expect(
    await other.evaluate(() => Object.hasOwn(window, "themeInjected")),
  ).toBe(false);
  await other.close();
});

test("标签页同步、移除与清空均有一致主题，并保留本页服务草稿", async ({
  page,
}) => {
  await page.goto("/settings");
  await page.locator("#llm-model").fill("fixture-unsaved-model");
  await choice(page, "night").click();
  const other = await page.context().newPage();
  await other.goto("/settings");
  await expect(choice(other, "night")).toHaveAttribute("aria-pressed", "true");
  await choice(other, "guide").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "guide");
  await expect(page.locator("#llm-model")).toHaveValue("fixture-unsaved-model");
  await expect(
    page
      .getByRole("region", { name: "外观主题", exact: true })
      .getByRole("status"),
  ).toHaveCount(0);
  await other.evaluate(() =>
    localStorage.setItem("unrelated-fixture", "night"),
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "guide");
  await other.evaluate((key) => localStorage.removeItem(key), key);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "paper");
  await choice(other, "night").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  await other.evaluate(() => localStorage.clear());
  await expect(page.locator("html")).toHaveAttribute("data-theme", "paper");
  await expect(page.locator("#llm-model")).toHaveValue("fixture-unsaved-model");
  await other.close();
});

for (const denied of ["unavailable", "quota"]) {
  test(`浏览器存储 ${denied} 时仍可切换，明确未保存且刷新回退`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.addInitScript(
      ({ key, denied }) => {
        localStorage.setItem(key, "guide");
        if (denied === "quota")
          Storage.prototype.setItem = () => {
            throw new DOMException("fixture quota", "QuotaExceededError");
          };
        else
          Object.defineProperty(window, "localStorage", {
            get() {
              throw new DOMException("fixture unavailable", "SecurityError");
            },
          });
      },
      { key, denied },
    );
    await page.goto("/settings");
    await expect(page.locator("html")).toHaveAttribute(
      "data-theme",
      denied === "quota" ? "guide" : "paper",
    );
    await choice(page, "night").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
    await expect(
      page
        .getByRole("region", { name: "外观主题", exact: true })
        .getByRole("status"),
    ).toContainText("浏览器未允许保存");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute(
      "data-theme",
      denied === "quota" ? "guide" : "paper",
    );
    expect(errors).toEqual([]);
  });
}

test("服务配置读取失败时，外观仍可用且不会触发服务保存", async ({ page }) => {
  let writes = 0;
  page.on("request", (r) => {
    if (r.url().includes("/system/settings") && r.method() !== "GET") writes++;
  });
  await page.route("**/api/backend/api/system/settings", (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: {
          code: "BACKEND_UNREACHABLE",
          message: "隔离服务配置暂时不可读",
        },
      },
    }),
  );
  await page.goto("/settings");
  await expect(page.locator("main").getByRole("alert")).toContainText(
    "隔离服务配置",
  );
  await choice(page, "night").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  expect(writes).toBe(0);
  await noOverflow(page);
});

for (const theme of ["paper", "guide", "night"]) {
  test(`${theme} 正式页面、路书、回顾与流程检查可读，查看不改版本`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await install(page, theme);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const trip = await seed(page);
    const routes = [
      "/",
      "/sessions",
      "/preferences",
      "/settings",
      "/compare",
      "/help",
      "/recaps",
      `/recaps/${trip.version}`,
      "/career",
      `/chat/${trip.session_id}?branch=${trip.branch_id}`,
      `/workflow/${trip.session_id}?branch=${trip.branch_id}`,
      `/workflow/${trip.session_id}?branch=${trip.branch_id}&view=process`,
    ];
    for (const route of routes) {
      await page.goto(route);
      await expect(page.locator("main h1").first()).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      if (route.startsWith("/chat/"))
        await expect(
          page.locator('[aria-label="旅行路线与执行"]'),
        ).toBeVisible();
      if (route.startsWith("/workflow/") && !route.includes("view=process"))
        await expect(
          page.getByRole("region", { name: "默认工程模板", exact: true }),
        ).toBeVisible();
      if (route === "/settings")
        await expect(page.locator("#llm-key")).toBeVisible();
      if (route.startsWith("/recaps/"))
        await expect(page.locator("main")).toContainText(
          "用户原文 📷，保持原样。",
        );
      if (route.includes("view=process"))
        await expect(
          page.getByRole("region", { name: "规划流程节点", exact: true }),
        ).toBeVisible();
      await noOverflow(page);
      await readable(
        page.locator(
          "main h1, main h2, main h3, main button, main summary, main label, main .tf-field-help, main .prose-report p, main [role=alert]",
        ),
      );
    }
    const after = await (
      await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
    ).json();
    expect(after.versions).toEqual(trip.versions);
  });
}

test("C 下高级工程主按钮悬停仍可读，画布空间不偏移且模板弹窗保持可用", async ({
  page,
  isMobile,
}) => {
  await install(page, "night");
  await page.emulateMedia({ reducedMotion: "reduce" });
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  await page.goto(`/workflow/${trip.session_id}?branch=${trip.branch_id}`);
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "结构化行程" })
    .click();
  const save = page.getByRole("button", { name: "保存工程", exact: true });
  await save.hover();
  await readable(save);
  const node = page.getByRole("button", {
    name: "选择节点 旅行需求",
    exact: true,
  });
  await page.getByRole("button", { name: "聚焦节点", exact: true }).click();
  const position = () =>
    node
      .locator("..")
      .evaluate((el) => [
        (el as HTMLElement).style.left,
        (el as HTMLElement).style.top,
      ]);
  const before = await position();
  await node.hover();
  await expect(node).toHaveCSS("translate", "none");
  await expect(node).toHaveCSS("scale", "1");
  expect(await position()).toEqual(before);
  await readable(node);
  const templates = page.getByRole("button", {
    name: "保存为我的模板",
    exact: true,
  });
  await templates.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await noOverflow(page);
  await readable(dialog.locator("button,label,h2"));
  const bounds = await dialog.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    await page.evaluate(() => document.documentElement.clientWidth),
  );
  await dialog
    .getByRole("button", { name: "关闭模板编辑", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  if (isMobile)
    expect((await save.boundingBox())!.height).toBeGreaterThanOrEqual(44);
});
