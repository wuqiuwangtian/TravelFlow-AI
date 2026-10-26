import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const directory = fileURLToPath(new URL("../../docs/design/", import.meta.url));
assert(
  !/\p{Extended_Pictographic}/u.test(
    await readFile(path.join(directory, "visual-directions.html"), "utf8"),
  ),
  "Authored prototype must not contain emoji",
);
const server = createServer(async (request, response) => {
  const name = new URL(request.url, "http://localhost").pathname;
  if (!["/visual-directions.html", "/icons.js"].includes(name)) {
    response.writeHead(404).end();
    return;
  }
  try {
    const data = await readFile(path.join(directory, name.slice(1)));
    response
      .writeHead(200, {
        "Content-Type": name.endsWith(".js")
          ? "text/javascript; charset=utf-8"
          : "text/html; charset=utf-8",
      })
      .end(data);
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
const browser = await chromium.launch();
const summary = {
  combinations: 0,
  screenshots: [],
  pageErrors: [],
  buttonsChecked: 0,
};
try {
  await mkdir(path.join(directory, "previews"), { recursive: true });
  for (const [device, viewport] of [
    ["desktop", { width: 1440, height: 1100 }],
    ["mobile", { width: 390, height: 844 }],
    ["small", { width: 320, height: 800 }],
  ]) {
    const context = await browser.newContext({
      viewport,
      isMobile: device !== "desktop",
      hasTouch: device !== "desktop",
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => summary.pageErrors.push(error.message));
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:)/, (route) => route.abort());
    await page.goto(`http://127.0.0.1:${port}/visual-directions.html`);
    for (const direction of ["paper", "guide", "night"]) {
      await page.locator(`[data-direction-choice="${direction}"]`).click();
      assert.equal(
        await page.locator("html").getAttribute("data-direction"),
        direction,
      );
      assert.equal(
        await page.locator('[role="tab"][aria-selected="true"]').count(),
        1,
      );
      for (const density of ["airy", "balanced", "compact"]) {
        await page.locator(`[data-density-choice="${density}"]`).click();
        assert.equal(
          await page.locator("html").getAttribute("data-density"),
          density,
        );
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          `${device}/${direction}/${density} overflow`,
        );
        summary.combinations++;
        if (density === "balanced" && device !== "small") {
          const name = `${device}-${direction}.png`;
          await page.locator("#sample").screenshot({
            path: path.join(directory, "previews", name),
            animations: "disabled",
          });
          summary.screenshots.push(name);
        }
      }
    }
    await page.locator('[data-density-choice="balanced"]').click();
    const buttons = await page
      .locator("button:visible")
      .evaluateAll((elements) =>
        elements.map((element) => {
          const style = getComputedStyle(element);
          return {
            text: element.textContent.trim(),
            transition: style.transitionProperty,
            duration: style.transitionDuration,
            height: element.offsetHeight,
          };
        }),
      );
    for (const button of buttons) {
      assert(
        button.transition.includes("scale") &&
          button.transition.includes("background-color"),
        button.text,
      );
      if (device !== "desktop") assert(button.height >= 44, button.text);
      summary.buttonsChecked++;
    }
    const save = page.locator('[data-action="save-plan"]');
    await save.scrollIntoViewIfNeeded();
    if (device === "desktop") {
      await save.hover();
      await page.waitForFunction(
        () =>
          getComputedStyle(document.querySelector('[data-action="save-plan"]'))
            .translate !== "none",
      );
      await page.mouse.down();
      await page.waitForFunction(
        () =>
          parseFloat(
            getComputedStyle(
              document.querySelector('[data-action="save-plan"]'),
            ).scale,
          ) < 1,
      );
      await page.mouse.up();
    } else await save.click();
    await page.waitForFunction(() =>
      document
        .getElementById("action-result")
        .textContent.includes("这次修改已保存"),
    );
    assert.equal(await save.isEnabled(), true);
    await page.locator('[data-action="adjust"]').click();
    await page.locator("#adjust-text").fill("下午少走一点，留出喝茶时间");
    await page.locator('#adjust-form button[type="submit"]').click();
    assert(
      (await page.locator("#day-summary").innerText()).includes("喝茶时间"),
    );
    await page.locator('[data-action="expense"]').click();
    await page.locator("#expense-title").fill("午餐");
    await page.locator("#expense-amount").fill("68.50");
    await page.locator('#expense-form button[type="submit"]').click();
    assert(
      (await page.locator("#expense-result").innerText()).includes("68.50"),
    );
    await page.emulateMedia({ reducedMotion: "reduce" });
    await save.scrollIntoViewIfNeeded();
    await save.hover();
    assert(
      ["none", "0px"].includes(
        await save.evaluate((element) => getComputedStyle(element).translate),
      ),
    );
    await context.close();
  }
  assert.deepEqual(summary.pageErrors, []);
  await writeFile(
    path.join(directory, "preview-check.json"),
    JSON.stringify(summary, null, 2) + "\n",
  );
  console.log(JSON.stringify(summary, null, 2));
} finally {
  await browser.close();
  server.close();
}
