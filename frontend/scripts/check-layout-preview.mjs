import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "playwright";

const directory = fileURLToPath(new URL("../../docs/design/", import.meta.url));
const assets = [
  "layout-options.html",
  "layout-options.css",
  "layout-options.js",
  "icons.js",
];
for (const name of assets)
  assert(
    !/\p{Extended_Pictographic}/u.test(
      await readFile(path.join(directory, name), "utf8"),
    ),
    `${name}: emoji`,
  );
const server = createServer(async (request, response) => {
  const name = new URL(request.url, "http://localhost").pathname.slice(1);
  if (!assets.includes(name)) {
    response.writeHead(404).end();
    return;
  }
  const data = await readFile(path.join(directory, name));
  response
    .writeHead(200, {
      "Content-Type": `${name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html"}; charset=utf-8`,
    })
    .end(data);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch();
const summary = {
  combinations: 0,
  screenshots: [],
  pageErrors: [],
  defaultTheme: "paper",
  defaultDesktopLayout: "conversation",
  defaultMobileLayout: "today",
  desktopDensity: "airy",
  mobileDensity: "compact",
  linkedSelectionChecks: 0,
  densityChecks: 0,
  defaultLayoutChecks: 0,
  independentDeviceLayoutChecks: 0,
};
try {
  await mkdir(path.join(directory, "layouts"), { recursive: true });
  for (const width of [1440, 1100, 980, 390, 320]) {
    const mobile = width < 900;
    const context = await browser.newContext({
      viewport: { width, height: mobile ? 844 : 1100 },
      isMobile: mobile,
      hasTouch: mobile,
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => summary.pageErrors.push(error.message));
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:)/, (route) => route.abort());
    await page.goto(
      `http://127.0.0.1:${server.address().port}/layout-options.html`,
    );
    await page.waitForFunction(
      () => document.querySelectorAll(".route-list .route-stop").length === 6,
    );
    assert.equal(
      await page.locator("html").getAttribute("data-layout"),
      "conversation",
    );
    assert.equal(
      await page.locator("html").getAttribute("data-mobile-layout"),
      "today",
    );
    assert.equal(
      await page.locator(mobile ? "#phone-app" : "#desktop-app").isVisible(),
      true,
    );
    summary.defaultLayoutChecks++;
    const variants = mobile
      ? ["today", "map"]
      : ["journey", "map", "conversation"];
    for (const variant of variants) {
      await page.goto(
        `http://127.0.0.1:${server.address().port}/layout-options.html?${mobile ? "mobile" : "desktop"}=${variant}`,
      );
      await page.waitForFunction(
        () => document.querySelectorAll(".route-list .route-stop").length === 6,
      );
      assert.equal(
        await page.locator("html").getAttribute("data-theme"),
        "paper",
      );
      assert.equal(
        await page.locator("html").getAttribute("data-density"),
        mobile ? "compact" : "airy",
      );
      const app = page.locator(mobile ? "#phone-app" : "#desktop-app");
      if (!mobile) {
        const journey = await app.locator(".journey-panel").boundingBox();
        const map = await app.locator(".map-panel").boundingBox();
        const chat = await app.locator(".assistant-panel").boundingBox();
        if (variant === "journey") assert(journey.width > map.width);
        if (variant === "map") {
          assert(map.width > journey.width);
          assert((await app.locator(".map-view").boundingBox()).height > 550);
        }
        if (variant === "conversation") assert(chat.width > journey.width);
      } else {
        const map = await app.locator(".phone-map").boundingBox();
        const journey = await app.locator(".phone-route").boundingBox();
        if (variant === "today") assert(journey.y < map.y);
        else assert(map.y < journey.y);
      }
      if (width === 1440 || width === 390) {
        const name = `${mobile ? "mobile" : "desktop"}-${variant}.png`;
        await app.screenshot({
          path: path.join(directory, "layouts", name),
          animations: "disabled",
        });
        summary.screenshots.push(name);
      }
      for (const theme of ["paper", "guide", "night"]) {
        await app.locator('[data-action="settings"]').click();
        const dialog = page.locator("#settings-dialog");
        assert.equal(
          await dialog.locator("#desktop-density").inputValue(),
          "airy",
        );
        assert.equal(
          await dialog.locator("#mobile-density").inputValue(),
          "compact",
        );
        await dialog.locator(`[data-theme-choice="${theme}"]`).click();
        assert.equal(
          await page.locator("html").getAttribute("data-theme"),
          theme,
        );
        await dialog.getByRole("button", { name: "完成", exact: true }).click();
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          `${width}/${variant}/${theme} overflow`,
        );
        summary.combinations++;
      }
      await app.locator('[data-action="settings"]').click();
      const density = page.locator(
        mobile ? "#mobile-density" : "#desktop-density",
      );
      await density.selectOption("balanced");
      assert.equal(
        await page.locator("html").getAttribute("data-density"),
        "balanced",
      );
      await density.selectOption(mobile ? "compact" : "airy");
      assert.equal(
        await page.locator("html").getAttribute("data-density"),
        mobile ? "compact" : "airy",
      );
      await page
        .locator("#settings-dialog")
        .getByRole("button", { name: "完成", exact: true })
        .click();
      summary.densityChecks++;
      if (width !== 1440 && width !== 390) continue;
      const mapButton = app.locator('.map-pin[data-stop="afternoon"]');
      await mapButton.click();
      assert.equal(
        await app
          .locator('.stop-select[data-stop="afternoon"]')
          .getAttribute("aria-pressed"),
        "true",
      );
      assert.equal(
        await app.locator(".current-title").first().innerText(),
        "湖边步道与街巷",
      );
      summary.linkedSelectionChecks++;
      if (mobile) {
        await app.getByRole("button", { name: "对话", exact: true }).click();
        await page
          .locator("#phone-chat-dialog")
          .getByRole("button", { name: "预览下午改成喝茶", exact: true })
          .click();
      } else
        await app
          .getByRole("button", { name: "看看这次调整", exact: true })
          .click();
      await page
        .locator("#change-dialog")
        .getByRole("button", { name: "应用到行程", exact: true })
        .click();
      assert.equal(
        await app.locator('[data-stop-title="afternoon"]').innerText(),
        "湖边喝茶与休息",
      );
      await app.locator('[data-action="expense"]').first().click();
      await page.locator("#expense-amount").fill("68.50");
      await page
        .locator("#expense-dialog")
        .getByRole("button", { name: "保存这笔消费", exact: true })
        .click();
      if (!mobile) {
        await app.locator('[data-action="book"]').first().click();
        assert.equal(
          await page.locator("#book-dialog [data-recorded]").innerText(),
          "¥68.50",
        );
        await page.locator("#book-dialog [data-close]").click();
      } else
        assert.equal(
          await app.locator("[data-recorded]").innerText(),
          "¥68.50",
        );
      await page.emulateMedia({ reducedMotion: "reduce" });
      const control = app.locator('[data-action="settings"]');
      await control.hover();
      assert(
        ["none", "0px"].includes(
          await control.evaluate(
            (element) => getComputedStyle(element).translate,
          ),
        ),
      );
    }
    if (width === 1440) {
      await page.goto(
        `http://127.0.0.1:${server.address().port}/layout-options.html`,
      );
      await page.locator('.reviewbar [data-mobile-choice="today"]').click();
      const originalHeight = (
        await page.locator("#phone-app .map-view").boundingBox()
      ).height;
      await page.locator('[data-layout-choice="map"]').click();
      await page.locator('.reviewbar [data-mobile-choice="today"]').click();
      assert.equal(
        (await page.locator("#phone-app .map-view").boundingBox()).height,
        originalHeight,
      );
      assert.equal(
        await page.locator("html").getAttribute("data-density"),
        "compact",
      );
      summary.independentDeviceLayoutChecks++;
    }
    await context.close();
  }
  assert.deepEqual(summary.pageErrors, []);
  await writeFile(
    path.join(directory, "layout-check.json"),
    JSON.stringify(summary, null, 2) + "\n",
  );
  console.log(JSON.stringify(summary, null, 2));
} finally {
  await browser.close();
  server.close();
}
