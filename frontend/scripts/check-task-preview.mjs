import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "playwright";

const directory = fileURLToPath(new URL("../../docs/design/", import.meta.url));
const assets = [
  "task-flows.html",
  "task-flows.css",
  "task-flows.js",
  "layout-options.css",
  "component-options.css",
  "icons.js",
  "assets/west-lake-reference.jpg",
];
for (const name of assets.filter((n) => !n.endsWith(".jpg")))
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
  const types = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".jpg": "image/jpeg",
  };
  response
    .writeHead(200, { "Content-Type": types[path.extname(name)] })
    .end(await readFile(path.join(directory, name)));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch();
const base = `http://127.0.0.1:${server.address().port}/task-flows.html`;
const output = path.join(directory, "tasks");
const summary = {
  widths: [1440, 390, 320],
  themeWorkspaceCombinations: 0,
  taskScenarios: [],
  screenshots: [],
  pageErrors: [],
  httpErrors: [],
  externalRequests: [],
};
async function textHas(locator, text) {
  assert(
    (await locator.textContent()).includes(text),
    `Expected ${text}, got ${await locator.textContent()}`,
  );
}
async function waitChat(page, text) {
  await page.waitForFunction(
    (text) => document.querySelector("#chat-result").textContent.includes(text),
    text,
  );
}
async function panel(page, name) {
  await page
    .locator(`#trip-workspace button[data-panel="${name}"]`)
    .filter({ visible: true })
    .first()
    .click();
}
async function run(page) {
  await page.locator('[data-action="run"]').click();
  await page.waitForFunction(() =>
    document.querySelector("#graph-status").textContent.startsWith("模拟完成"),
  );
}
async function shot(page, selector, name) {
  await page.mouse.move(0, 0);
  await page.waitForTimeout(180);
  if (
    selector === "#trip-workspace" &&
    (await page.locator("html").getAttribute("data-device")) === "mobile"
  ) {
    assert.equal(
      await page.locator(selector).evaluate((el) => el.scrollTop),
      0,
      "phone frame scrolled its header away",
    );
  }
  await page.locator(selector).screenshot({ path: path.join(output, name) });
  summary.screenshots.push(name);
}
try {
  await mkdir(output, { recursive: true });
  for (const width of summary.widths) {
    const mobile = width < 900;
    const context = await browser.newContext({
      viewport: { width, height: 1100 },
      isMobile: mobile,
      hasTouch: mobile,
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => summary.pageErrors.push(e.message));
    page.on("response", (response) => {
      if (response.status() >= 400) summary.httpErrors.push(response.url());
    });
    await page.route(/^https?:\/\/(?!127\.0\.0\.1:)/, (route) => {
      summary.externalRequests.push(route.request().url());
      return route.abort();
    });
    await page.goto(base);
    await page.waitForFunction(
      () => document.querySelectorAll("#task-route .route-stop").length === 3,
    );
    assert.equal(
      await page.locator("html").getAttribute("data-device"),
      mobile ? "mobile" : "desktop",
    );
    assert.equal(
      await page.locator("html").getAttribute("data-density"),
      mobile ? "compact" : "airy",
    );
    assert.equal(
      await page
        .locator('[data-action="try-change"]')
        .evaluate((el) => getComputedStyle(el).borderRadius),
      "8px",
    );
    assert(
      await page
        .locator("#task-route img")
        .evaluate((el) => el.complete && el.naturalWidth === 960),
    );
    for (const theme of ["paper", "guide", "night"]) {
      await page.locator('[data-action="settings"]').click();
      await page.locator(`button[data-theme="${theme}"]`).click();
      await page.locator('[data-close="task-settings"]').click();
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      summary.themeWorkspaceCombinations++;
    }
    await page.locator('[data-action="settings"]').click();
    await page.locator('button[data-theme="paper"]').click();
    await page.locator('[data-close="task-settings"]').click();
    if (width !== 320)
      await shot(
        page,
        "#trip-workspace",
        `${mobile ? "mobile" : "desktop"}-route.png`,
      );
    if (mobile) await panel(page, "chat");
    await page.locator("#task-message").fill("下午不要喝茶，改去机场");
    await page.locator('#task-chat-form button[type="submit"]').click();
    await textHas(page.locator("#chat-result"), "本地任务样稿");
    assert.equal(
      await page.locator("#task-message").inputValue(),
      "下午不要喝茶，改去机场",
    );
    await textHas(page.locator("#task-route"), "湖边步道与街巷");
    await page.locator('[data-action="try-change"]').click();
    assert(
      await page.locator('#task-chat-form button[type="submit"]').isDisabled(),
    );
    await waitChat(page, "已更新");
    await textHas(page.locator("#task-route"), "湖边喝茶与休息");
    await textHas(page.locator("[data-projected]"), "1328.00");
    if (mobile) {
      const send = await page
        .locator('#task-chat-form button[type="submit"]')
        .boundingBox();
      const navigation = await page.locator(".task-bottom").boundingBox();
      assert(
        send.y + send.height <= navigation.y + 1,
        "mobile composer is hidden below navigation",
      );
    }
    if (width !== 320)
      await shot(
        page,
        "#trip-workspace",
        `${mobile ? "mobile" : "desktop"}-change-direct.png`,
      );
    await panel(page, "budget");
    await page.locator('[data-edit="meal"]').click();
    const editor = page.locator('[data-budget-row="meal"] .budget-editor');
    assert.equal(
      await editor
        .locator("input")
        .evaluate((el) => el === document.activeElement),
      true,
    );
    await editor.locator("input").fill("-1");
    await editor.getByRole("button", { name: "保存实付" }).click();
    await textHas(editor.locator('[role="alert"]'), "大于 0");
    await editor.locator("input").fill("68.50");
    await editor.getByRole("button", { name: "保存实付" }).click();
    assert.equal(
      await editor.getByRole("button", { name: "保存中…" }).isDisabled(),
      true,
    );
    await page.waitForFunction(() => !document.querySelector(".budget-editor"));
    await textHas(page.locator("[data-projected]"), "1236.50");
    await page.locator('[data-action="custom"]').click();
    await page.locator("#custom-name").fill("寄存行李");
    await page.locator("#custom-amount").fill("20");
    await page.getByRole("button", { name: "添加并记账" }).click();
    await textHas(page.locator("[data-projected]"), "1256.50");
    if (width !== 320)
      await shot(
        page,
        "#trip-workspace",
        `${mobile ? "mobile" : "desktop"}-budget.png`,
      );
    if (mobile) await panel(page, "chat");
    await page.locator('[data-action="undo"]').click();
    await textHas(page.locator("[data-projected]"), "1208.50");
    await textHas(page.locator("#budget-summary"), "88.50");
    await page.locator('[data-revision="review"]').click();
    await page.locator('[data-action="try-change"]').click();
    await waitChat(page, "建议已准备");
    await textHas(page.locator("#task-route"), "湖边步道与街巷");
    await textHas(page.locator("[data-projected]"), "1208.50");
    if (!mobile)
      await shot(page, "#trip-workspace", "desktop-change-review.png");
    await page.locator('[data-action="discard"]').click();
    await textHas(page.locator("#chat-result"), "保留原安排");
    await page.locator('[data-action="try-change"]').click();
    await waitChat(page, "建议已准备");
    await page.locator('[data-action="apply"]').click();
    await textHas(page.locator("[data-projected]"), "1256.50");
    await page.locator('[data-action="undo"]').click();
    await page.locator('[data-revision="direct"]').click();
    await page.locator("#fail-next").check();
    await page.locator('[data-action="try-change"]').click();
    await waitChat(page, "这次未完成");
    assert.equal(
      await page.locator("#task-message").inputValue(),
      "下午少走一点，留出喝茶时间",
    );
    await textHas(page.locator("#task-route"), "湖边步道与街巷");
    const messages = await page.locator("#task-dialogue .user-message").count();
    await page.locator("#task-chat-form").evaluate((form) => {
      form.requestSubmit();
      form.requestSubmit();
    });
    await waitChat(page, "已更新");
    assert.equal(
      await page.locator("#task-dialogue .user-message").count(),
      messages + 1,
    );
    if (mobile) await panel(page, "route");
    await page.locator('[data-action="navigate"]').click();
    await textHas(page.locator("#navigation-result"), "柳浪闻莺 → 午餐与休息");
    await page.locator('[data-action="done"]').click();
    await textHas(page.locator("#next-title"), "湖边喝茶与休息");
    // Book is reachable on desktop; mobile uses the same single trip model.
    {
      await page
        .locator('[data-action="book"]')
        .filter({ visible: true })
        .first()
        .click();
      await textHas(page.locator("#book-content"), "湖边喝茶与休息");
      await textHas(page.locator("#book-content"), "88.50");
      await page.locator('[data-close="task-book"]').click();
    }
    await panel(page, "budget");
    await page.locator('[data-edit="lodging"]').click();
    await page.locator(".budget-editor input").fill("2000");
    await page.getByRole("button", { name: "保存实付" }).click();
    await page.waitForFunction(() => !document.querySelector(".budget-editor"));
    await textHas(page.locator("[data-balance]"), "超预算");
    await page.locator('[data-edit="tea"]').click();
    await page.locator(".budget-editor input").fill("25");
    await page.getByRole("button", { name: "保存实付" }).click();
    await page.waitForFunction(() => !document.querySelector(".budget-editor"));
    if (mobile) await panel(page, "chat");
    await page.locator('[data-action="undo"]').click();
    await textHas(page.locator('[data-edit="tea"]'), "25.00");
    await textHas(page.locator("#budget-summary"), "2113.50");
    await textHas(page.locator("#task-route"), "湖边步道与街巷");
    summary.taskScenarios.push(
      `${width}: R1 apply/undo preserves actuals; R2 preview/discard/apply; failed draft retry and duplicate-submit guard; inline actual/custom expenses and budget excess; previous-stop navigation and completion`,
    );
    await page
      .locator('[data-workspace="graph"]')
      .filter({ visible: true })
      .first()
      .click();
    await page.locator("#graph-template").selectOption("branches");
    await page.locator('[data-action="template"]').click();
    assert.equal(await page.locator(".graph-node").count(), 7);
    assert.equal(await page.locator("#graph-wires path").count(), 8);
    for (const theme of ["paper", "guide", "night"]) {
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      for (const layout of ["side", "bottom"]) {
        await page.locator(`button[data-inspector="${layout}"]`).click();
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        );
        summary.themeWorkspaceCombinations++;
      }
    }
    await page.evaluate(() => {
      document.documentElement.dataset.theme = "paper";
    });
    if (!mobile) {
      for (const layout of ["side", "bottom"]) {
        await page.locator(`button[data-inspector="${layout}"]`).click();
        const canvas = await page.locator(".canvas-panel").boundingBox(),
          inspector = await page.locator("#graph-inspector").boundingBox();
        assert(
          layout === "side"
            ? inspector.x >= canvas.x + canvas.width - 1
            : inspector.y >= canvas.y + canvas.height - 1,
        );
        await shot(page, "#graph-workspace", `desktop-graph-${layout}.png`);
      }
    }
    await page.locator('button[data-inspector="side"]').click();
    await run(page);
    await textHas(page.locator("#graph-status"), "重算 7 个节点，复用 0");
    await run(page);
    await textHas(page.locator("#graph-status"), "重算 0 个节点，复用 7");
    const branch = page
      .locator("[data-select-node]")
      .filter({ hasText: "湖景与自然" });
    await branch.click();
    const branchId = await branch.getAttribute("data-select-node");
    await branch.focus();
    await page.keyboard.press("Enter");
    assert(
      await branch.evaluate((el) => document.activeElement === el),
      "node keyboard focus lost after selection",
    );
    await page
      .locator('#node-form [name="prompt"]')
      .fill("优先自然风景，步行更少");
    await page
      .locator("[data-select-node]")
      .filter({ hasText: "文本输入" })
      .click();
    await branch.click();
    assert.equal(
      await page.locator('#node-form [name="prompt"]').inputValue(),
      "优先自然风景，步行更少",
    );
    await page.locator('[data-action="run"]').click();
    await textHas(page.locator("#graph-status"), "未保存");
    await page.getByRole("button", { name: "保存参数" }).click();
    await run(page);
    await textHas(page.locator("#graph-status"), "重算 5 个节点，复用 2");
    await page.locator(`[data-select-node="${branchId}"]`).focus();
    const left = await page
      .locator(`[data-node="${branchId}"]`)
      .evaluate((el) => parseFloat(el.style.left));
    await page.keyboard.press("ArrowRight");
    assert.equal(
      await page
        .locator(`[data-node="${branchId}"]`)
        .evaluate((el) => parseFloat(el.style.left)),
      left + 8,
    );
    const title = page.locator(`[data-select-node="${branchId}"]`),
      box = await title.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      box.x + box.width / 2 + 40,
      box.y + box.height / 2 + 25,
      { steps: 4 },
    );
    await page.mouse.up();
    assert(
      await page
        .locator(`[data-node="${branchId}"]`)
        .evaluate((el, left) => parseFloat(el.style.left) > left, left + 8),
    );
    await page.locator('[data-action="graph-undo"]').click();
    assert.equal(
      await page
        .locator(`[data-node="${branchId}"]`)
        .evaluate((el) => parseFloat(el.style.left)),
      left + 8,
    );
    await page.locator("#graph-kind").selectOption("merge");
    await page.locator('[data-action="add-node"]').click();
    assert.equal(await page.locator(".graph-node").count(), 8);
    const target = await page
      .locator(".graph-node.selected")
      .getAttribute("data-node");
    await page.locator('[data-action="run"]').click();
    await textHas(page.locator("#graph-status"), "缺少输入");
    const merges = page
      .locator("[data-select-node]")
      .filter({ hasText: "候选合并" });
    const source = await merges.first().getAttribute("data-select-node");
    await page.locator(`[data-output-node="${source}"]`).click();
    await page.locator(`[data-input-node="${target}"]`).click();
    assert.equal(await page.locator("#graph-wires path").count(), 9);
    await page.locator(`[data-output-node="${target}"]`).click();
    await page.locator(`[data-input-node="${source}"]`).click();
    await textHas(page.locator("#graph-status"), "循环依赖");
    const inputId = await page
      .locator("[data-select-node]")
      .filter({ hasText: "文本输入" })
      .getAttribute("data-select-node");
    await page.locator(`[data-output-node="${inputId}"]`).click();
    await page.locator(`[data-input-node="${target}"]`).click();
    await textHas(page.locator("#graph-status"), "匹配的数据类型");
    await page.locator('[data-action="cancel-wire"]').click();
    await page.locator(`[data-select-node="${target}"]`).click();
    await page.locator("[data-delete-edge]").click();
    assert.equal(await page.locator("#graph-wires path").count(), 8);
    await page.locator('[data-action="graph-undo"]').click();
    assert.equal(await page.locator("#graph-wires path").count(), 9);
    await page.locator('[data-action="delete-node"]').click();
    assert.equal(await page.locator(".graph-node").count(), 7);
    await page.locator('[data-action="graph-undo"]').click();
    assert.equal(await page.locator(".graph-node").count(), 8);
    if (mobile && width === 390)
      await shot(page, "#graph-workspace", "mobile-graph-inspection.png");
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert(
      await page
        .locator('[data-action="run"]')
        .evaluate(
          (el) => parseFloat(getComputedStyle(el).transitionDuration) < 0.001,
        ),
    );
    summary.taskScenarios.push(
      `${width}: editable branched template, real drag/keyboard move, add/delete/undo, typed connection/cycle guards, parameter draft preservation, downstream-only invalidation and cached rerun, both inspector layouts and reduced motion`,
    );
    await context.close();
  }
  assert.deepEqual(summary.pageErrors, []);
  assert.deepEqual(summary.httpErrors, []);
  assert.deepEqual(summary.externalRequests, []);
  await writeFile(
    path.join(directory, "task-check.json"),
    `${JSON.stringify(summary, null, 2)}\n`,
  );
  console.log(JSON.stringify(summary, null, 2));
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
