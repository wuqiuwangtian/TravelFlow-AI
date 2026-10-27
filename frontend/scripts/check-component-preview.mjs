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
  "component-options.js",
  "component-options.css",
  "assets/west-lake-reference.jpg",
];
for (const name of assets.filter((name) => !name.endsWith(".jpg"))) {
  assert(
    !/\p{Extended_Pictographic}/u.test(
      await readFile(path.join(directory, name), "utf8"),
    ),
    `${name}: emoji`,
  );
}
const server = createServer(async (request, response) => {
  const name = new URL(request.url, "http://localhost").pathname.slice(1);
  if (!assets.includes(name)) {
    response.writeHead(404).end();
    return;
  }
  const types = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".jpg": "image/jpeg",
  };
  response
    .writeHead(200, { "Content-Type": types[path.extname(name)] })
    .end(await readFile(path.join(directory, name)));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch();
const base = `http://127.0.0.1:${server.address().port}/layout-options.html?round=components`;
const summary = {
  combinations: 0,
  screenshots: [],
  pageErrors: [],
  httpErrors: [],
  externalRequests: [],
  checks: [],
};
const output = path.join(directory, "components");
try {
  await mkdir(output, { recursive: true });
  for (const width of [1440, 390, 320]) {
    const mobile = width < 900;
    const context = await browser.newContext({
      viewport: { width, height: mobile ? 1000 : 1200 },
      isMobile: mobile,
      hasTouch: mobile,
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => summary.pageErrors.push(error.message));
    page.on("response", (response) => {
      if (response.status() >= 400) summary.httpErrors.push(response.url());
    });
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:)/, (route) => {
      summary.externalRequests.push(route.request().url());
      return route.abort();
    });
    await page.goto(base);
    await page.waitForFunction(
      () => document.documentElement.dataset.componentReady === "true",
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
      await page.locator("html").getAttribute("data-density"),
      mobile ? "compact" : "airy",
    );
    const app = page.locator(mobile ? "#phone-app" : "#desktop-app");
    const radius = {};
    for (const component of ["quiet", "precise", "soft"]) {
      await page.locator(`[data-component-choice="${component}"]`).click();
      radius[component] = await page
        .locator('[data-lab="success"]')
        .evaluate((el) => getComputedStyle(el).borderRadius);
      for (const photos of ["small", "large", "on-demand"]) {
        await page.locator(`[data-photo-choice="${photos}"]`).click();
        for (const theme of ["paper", "guide", "night"]) {
          await app.locator('[data-action="settings"]').click();
          await page.locator(`[data-theme-choice="${theme}"]`).click();
          await page.locator('[data-close="settings-dialog"]').first().click();
          assert(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth,
            ),
            `${width}/${component}/${photos}/${theme}: horizontal overflow`,
          );
          assert(
            await app.evaluate((el) => el.scrollWidth <= el.clientWidth),
            "app overflow",
          );
          const figure = app.locator(".stop-visual");
          assert.equal(await figure.count(), 1);
          assert(
            await figure
              .locator("img")
              .evaluate((el) => el.complete && el.naturalWidth === 960),
          );
          const box = await figure.locator("button").boundingBox();
          assert(box.width >= 44 && box.height >= 44);
          if (photos === "small") assert(box.width <= 65 && box.height <= 57);
          if (photos === "large") assert(box.width > 140 && box.height >= 144);
          if (photos === "on-demand")
            assert.equal(await figure.locator("img").isVisible(), false);
          assert.equal(
            await page
              .locator(`[data-component-choice="${component}"]`)
              .getAttribute("aria-pressed"),
            "true",
          );
          assert.equal(
            await page
              .locator(`[data-photo-choice="${photos}"]`)
              .getAttribute("aria-pressed"),
            "true",
          );
          summary.combinations++;
          if (
            theme === "paper" &&
            width !== 320 &&
            (photos === "small" || component === "quiet")
          ) {
            const name = `${mobile ? "mobile" : "desktop"}-${component}-${photos}.png`;
            if (mobile)
              await app.locator(".phone-content").evaluate((el) => {
                el.scrollTop = 0;
              });
            await page.mouse.move(0, 0);
            await page.waitForTimeout(300);
            await app.screenshot({ path: path.join(output, name) });
            summary.screenshots.push(name);
          }
        }
      }
    }
    assert.deepEqual(radius, { quiet: "8px", precise: "3px", soft: "14px" });
    summary.checks.push(
      `${width}: default layouts/density, three distinct radii, photograph sizing, theme compatibility`,
    );
    await app.locator("[data-reference-photo]").click();
    assert.equal(await page.locator("#photo-dialog").isVisible(), true);
    assert.equal(await page.locator("#photo-dialog a").count(), 2);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#photo-dialog").isVisible(), false);
    assert.equal(
      await app
        .locator("[data-reference-photo]")
        .evaluate((el) => document.activeElement === el),
      true,
    );
    summary.checks.push(
      `${width}: photograph dialog, attribution, Escape and focus return`,
    );
    // Re-render the actual route and make sure optional photos survive the update.
    await app.locator('[data-stop="lake"]').first().click();
    await app
      .locator('[data-action="done"]')
      .filter({ visible: true })
      .first()
      .click();
    assert.equal(await app.locator(".stop-visual").count(), 1);
    if (!mobile) {
      await app.locator('[data-day="1"]').click();
      assert.equal(await app.locator(".stop-visual").count(), 0);
      await app.locator('[data-day="2"]').click();
      assert.equal(await app.locator(".stop-visual").count(), 1);
      await page.locator('[data-device-choice="mobile"]').click();
      assert.equal(await page.locator("#phone-app").isVisible(), true);
      await page.locator('[data-device-choice="desktop"]').click();
      assert.equal(await page.locator("#desktop-app").isVisible(), true);
    }
    summary.checks.push(
      `${width}: route update/photo preservation${mobile ? "" : ", no false photos on other days and device switching"}`,
    );
    // Two real local asynchronous outcomes, with repeated submission blocked.
    for (const result of ["success", "failure"]) {
      const button = page.locator(`[data-lab="${result}"]`);
      await button.click();
      assert.equal(await button.getAttribute("aria-busy"), "true");
      assert.equal(await page.locator("[data-lab]:disabled").count(), 2);
      await page.waitForFunction(
        (expected) =>
          document.querySelector("#lab-status").dataset.result === expected,
        result,
      );
      assert.equal(await button.isEnabled(), true);
    }
    summary.checks.push(
      `${width}: waiting, duplicate-submit guard, success and recoverable failure`,
    );
    if (!mobile) {
      // Check computed effects, not simply the presence of CSS selectors.
      for (const component of ["quiet", "precise", "soft"]) {
        await page.locator(`[data-component-choice="${component}"]`).click();
        const button = page.locator('[data-lab="success"]');
        await page.mouse.move(0, 0);
        await page.waitForTimeout(300);
        const normal = await button.evaluate((el) => {
          const s = getComputedStyle(el);
          return [s.translate, s.boxShadow, s.borderColor].join("|");
        });
        await button.hover();
        await page.waitForTimeout(300);
        const hover = await button.evaluate((el) => {
          const s = getComputedStyle(el);
          return [s.translate, s.boxShadow, s.borderColor].join("|");
        });
        assert.notEqual(hover, normal, `${component}: missing hover`);
        await page.mouse.down();
        await page.waitForTimeout(300);
        assert.notEqual(
          await button.evaluate((el) => getComputedStyle(el).scale),
          "1",
          `${component}: missing press`,
        );
        await page.mouse.move(0, 0);
        await page.mouse.up();
        await page.keyboard.press("Tab");
        await button.focus();
        assert.equal(
          await button.evaluate((el) => el.matches(":focus-visible")),
          true,
        );
      }
      summary.checks.push(
        "desktop: actual hover/press/focus in all three variants",
      );
    }
    await page.emulateMedia({ reducedMotion: "reduce" });
    const button = page.locator('[data-lab="success"]');
    await button.hover();
    assert.equal(
      await button.evaluate((el) => getComputedStyle(el).translate),
      "none",
    );
    assert(
      await button.evaluate(
        (el) => parseFloat(getComputedStyle(el).transitionDuration) < 0.001,
      ),
    );
    summary.checks.push(`${width}: reduced-motion feedback`);
    await context.close();
  }
  // A missing image collapses cleanly to the existing text route.
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.route("**/west-lake-reference.jpg", (route) => route.abort());
  await page.goto(base);
  await page.waitForFunction(
    () =>
      document.documentElement.dataset.componentReady === "true" &&
      !document.querySelector(".has-photo"),
  );
  assert.equal(await page.locator("#phone-app .route-stop").count(), 3);
  summary.checks.push(
    "missing image: removed photo and reserved space, text route intact",
  );
  await page.close();
  assert.deepEqual(summary.pageErrors, []);
  assert.deepEqual(summary.httpErrors, []);
  assert.deepEqual(summary.externalRequests, []);
  await writeFile(
    path.join(directory, "component-check.json"),
    `${JSON.stringify(summary, null, 2)}\n`,
  );
  console.log(JSON.stringify(summary, null, 2));
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
