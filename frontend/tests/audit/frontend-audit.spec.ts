import { test, expect, type Page, type TestInfo } from "playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

// Keep the committed FE-0 baseline immutable; post-fix captures are ignored outputs.
const root = path.resolve(__dirname, "../../test-results/fe1-audit");
const proxy = "/api/backend";
const error = {
  error: {
    code: "BACKEND_UNREACHABLE",
    message: "盘点用服务暂时不可用，请重试",
  },
};
const directory = (info: TestInfo) => path.join(root, info.project.name);
async function evidence(
  page: Page,
  info: TestInfo,
  name: string,
  data?: unknown,
) {
  await mkdir(directory(info), { recursive: true });
  await page.screenshot({
    path: path.join(directory(info), `${name}.png`),
    animations: "disabled",
  });
  if (data !== undefined)
    await writeFile(
      path.join(directory(info), `${name}.json`),
      JSON.stringify(data, null, 2) + "\n",
    );
}
async function seed(page: Page, title: string, ended = false) {
  const response = await page.request.post(`${proxy}/test/moves`);
  expect(response.ok()).toBe(true);
  const trip = await response.json();
  await page.request.patch(`${proxy}/api/sessions/${trip.session_id}`, {
    data: { title },
  });
  const detail = await (
    await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
  ).json();
  let version = detail.versions[trip.branch_id].at(-1).id;
  if (ended) {
    const finish = await page.request.put(
      `${proxy}/api/versions/${version}/journey-status`,
      { data: { ended: true } },
    );
    expect(finish.ok()).toBe(true);
    version = (await finish.json()).id ?? version;
    const latest = await (
      await page.request.get(`${proxy}/api/sessions/${trip.session_id}`)
    ).json();
    version = latest.versions[trip.branch_id].at(-1).id;
  }
  return { ...trip, version };
}
async function ready(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}
async function metrics(page: Page) {
  return page.evaluate(() => {
    const visible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        getComputedStyle(element).visibility !== "hidden"
      );
    };
    const controls = Array.from(
      document.querySelectorAll(
        "button,a,input,select,textarea,summary,[role=button]",
      ),
    )
      .filter(visible)
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          name: (
            element.getAttribute("aria-label") ||
            element.textContent ||
            element.getAttribute("title") ||
            element.getAttribute("placeholder") ||
            ""
          )
            .replace(/\s+/g, " ")
            .trim()
            .slice(0, 100),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          disabled: element.matches(":disabled"),
          ariaBusy: element.getAttribute("aria-busy"),
        };
      });
    const nav = document.querySelector("header nav");
    return {
      viewport: { width: innerWidth, height: innerHeight },
      scrollY,
      focusedElement:
        document.activeElement?.getAttribute("aria-label") ||
        document.activeElement?.tagName,
      documentWidth: document.documentElement.scrollWidth,
      nav: nav
        ? {
            clientWidth: nav.clientWidth,
            scrollWidth: nav.scrollWidth,
            currentLinks: nav.querySelectorAll("[aria-current]").length,
          }
        : null,
      headings: Array.from(document.querySelectorAll("h1,h2,h3")).map(
        (element) => element.textContent,
      ),
      alerts: Array.from(document.querySelectorAll('[role="alert"]')).map(
        (element) => element.textContent,
      ),
      controls,
    };
  });
}

test.beforeEach(async ({ page }) => {
  // The audit config runs serially against browser_server.py's temporary DB.
  // Start each probe with its own fixtures so screenshots never accumulate
  // unrelated probe rows or duplicate titles across desktop/mobile projects.
  for (const archived of [false, true]) {
    const result = await page.request.get(
      `${proxy}/api/sessions?archived=${archived}`,
    );
    expect(result.ok()).toBe(true);
    for (const item of (await result.json()).items) {
      expect(
        (await page.request.delete(`${proxy}/api/sessions/${item.id}`)).ok(),
      ).toBe(true);
    }
  }
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("11 route templates: populated desktop/mobile baseline", async ({
  page,
}, info) => {
  const active = await seed(page, "北京三日 · 正在旅行");
  const finished = await seed(page, "北京三日 · 旅行回忆", true);
  await page.request.post(
    `${proxy}/api/sessions/${active.session_id}/branches`,
    { data: { title: "轻松备选", parent_branch_id: active.branch_id } },
  );
  const routes = [
    ["home", "/"],
    ["sessions", "/sessions"],
    ["chat", `/chat/${active.session_id}`],
    ["workflow", `/workflow/${active.session_id}?branch=${active.branch_id}`],
    ["settings", "/settings"],
    ["preferences", "/preferences"],
    ["career", "/career"],
    ["recaps", "/recaps"],
    ["recap-detail", `/recaps/${finished.version}`],
    ["compare", "/compare"],
    ["help", "/help"],
  ];
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => consoleErrors.push(err.message));
  for (const [name, url] of routes) {
    await page.goto(url);
    await ready(page);
    if (name === "compare") {
      await page.locator("main select").first().selectOption(active.session_id);
      await ready(page);
    }
    if (name === "chat")
      await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
    await evidence(page, info, name, await metrics(page));
  }
  await writeFile(
    path.join(directory(info), "page-errors.json"),
    JSON.stringify(consoleErrors, null, 2) + "\n",
  );
  expect(consoleErrors).toEqual([]);
});

test("session rename: pending and failed draft recovery", async ({
  page,
}, info) => {
  const trip = await seed(page, "盘点改名原文");
  await page.goto("/sessions");
  const row = page.getByRole("listitem", {
    name: "旅行：盘点改名原文",
    exact: true,
  });
  await row.getByRole("button", { name: "重命名", exact: true }).click();
  const input = row.getByRole("textbox", { name: "旅行名称", exact: true });
  const draft = "失败后应保留的新标题";
  await input.fill(draft);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requests = 0;
  await page.route(
    `**/api/backend/api/sessions/${trip.session_id}`,
    async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      requests++;
      await gate;
      await route.fulfill({ status: 503, json: error });
    },
  );
  await input.press("Enter");
  await expect.poll(() => requests).toBe(1);
  await evidence(page, info, "rename-pending", {
    editFields: await row.getByRole("textbox").count(),
    text: await row.innerText(),
    metrics: await metrics(page),
  });
  release();
  await expect(page.locator("main").getByRole("alert")).toContainText(
    error.error.message,
  );
  await expect(input).toHaveValue(draft);
  await evidence(page, info, "rename-failed", {
    entered: draft,
    recovered: await row
      .getByRole("textbox", { name: "旅行名称", exact: true })
      .inputValue(),
  });
});

test("session archive: pending duplicate submission", async ({
  page,
}, info) => {
  const trip = await seed(page, "盘点归档反馈");
  await page.goto("/sessions");
  const row = page.getByRole("listitem", {
    name: "旅行：盘点归档反馈",
    exact: true,
  });
  const button = row.getByRole("button", { name: "归档", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requests = 0;
  await page.route(
    `**/api/backend/api/sessions/${trip.session_id}`,
    async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      requests++;
      await gate;
      await route.fulfill({ status: 503, json: error });
    },
  );
  await button.click();
  await expect.poll(() => requests).toBe(1);
  const waiting = row.getByRole("button", { name: "归档中…", exact: true });
  await expect(waiting).toBeDisabled();
  const disabledAfterClick = await waiting.isDisabled();
  expect(requests).toBe(1);
  await evidence(page, info, "archive-pending", {
    requests,
    disabledAfterClick,
    buttonText: await waiting.innerText(),
    metrics: await metrics(page),
  });
  release();
  await expect(page.locator("main").getByRole("alert")).toContainText(
    error.error.message,
  );
});

test("compare: initial failure versus empty state", async ({ page }, info) => {
  await page.route(/\/api\/backend\/api\/sessions(?:\?.*)?$/, (route) =>
    route.fulfill({ status: 503, json: error }),
  );
  await page.goto("/compare");
  await ready(page);
  await expect(page.locator("main").getByRole("alert")).toContainText(
    error.error.message,
  );
  await expect(
    page.getByText("还没有可对比的旅行", { exact: true }),
  ).toHaveCount(0);
  await evidence(page, info, "compare-failed", await metrics(page));
});

test("home: keyboard entry and empty/error distinction", async ({
  page,
}, info) => {
  await seed(page, "盘点键盘入口");
  await page.goto("/");
  await ready(page);
  const card = page.getByRole("heading", { name: "盘点键盘入口", exact: true });
  const semantics = await card.evaluate((heading) => {
    const target = heading.parentElement!;
    return {
      tag: target.tagName,
      role: target.getAttribute("role"),
      tabIndex: target.tabIndex,
      nativeInteractiveAncestor: !!heading.closest("a,button"),
      hasKeyHandler: !!target.getAttribute("onkeydown"),
    };
  });
  expect(semantics.nativeInteractiveAncestor).toBe(true);
  await evidence(page, info, "home-keyboard", semantics);
  await page.route(/\/api\/backend\/api\/sessions(?:\?.*)?$/, (route) =>
    route.fulfill({ status: 503, json: error }),
  );
  await page.reload();
  await ready(page);
  await evidence(page, info, "home-failed", await metrics(page));
});

test("advanced studio: copy result location and working run feedback", async ({
  page,
}, info) => {
  const trip = await seed(page, "盘点工程反馈");
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async () => undefined },
    });
  });
  await page.goto(`/workflow/${trip.session_id}?branch=${trip.branch_id}`);
  const studio = page.getByRole("region", { name: "节点工程", exact: true });
  await page
    .getByRole("region", { name: "默认工程模板", exact: true })
    .getByRole("button")
    .filter({ hasText: "双方案比较" })
    .click();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let pending = false;
  await page.route("**/studio/runs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    pending = true;
    await gate;
    await route.continue();
  });
  const completed = page.waitForResponse(
    (response) =>
      response.url().endsWith("/studio/runs") &&
      response.request().method() === "POST",
  );
  await studio.getByRole("button", { name: "运行全部", exact: true }).click();
  await expect.poll(() => pending).toBe(true);
  await evidence(page, info, "studio-run-pending", {
    buttonText: await studio
      .getByRole("button", { name: "工程运行中…", exact: true })
      .innerText(),
    metrics: await metrics(page),
  });
  release();
  expect((await completed).ok()).toBe(true);
  await expect(
    studio.getByRole("button", { name: "运行全部", exact: true }),
  ).toBeEnabled();
  const inspector = page.getByRole("complementary", {
    name: "节点参数与产物",
    exact: true,
  });
  // The generated branch nodes have artifacts; the input node is selected initially.
  await inspector.getByLabel("当前节点", { exact: true }).selectOption("slow");
  const copy = inspector.getByRole("button", { name: "复制正文", exact: true });
  await copy.click();
  await expect(
    studio.getByRole("status").filter({ hasText: "节点正文已复制" }),
  ).toHaveCount(1);
  const position = await studio
    .getByRole("status")
    .filter({ hasText: "节点正文已复制" })
    .evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        top: rect.top,
        bottom: rect.bottom,
        inViewport: rect.bottom > 56 && rect.top < innerHeight,
        scrollY,
      };
    });
  expect(position.inViewport).toBe(true);
  await evidence(page, info, "studio-copy", position);
});

test("budget: inspect existing inline editing rather than duplicate a fixed issue", async ({
  page,
}, info) => {
  const trip = await seed(page, "盘点原位记账");
  await page.goto(`/chat/${trip.session_id}`);
  await expect(page.getByLabel("当天行程", { exact: true })).toBeVisible();
  const buttons = await page.getByRole("button").allTextContents();
  await writeFile(
    path.join(directory(info), "chat-actions.json"),
    JSON.stringify(buttons, null, 2) + "\n",
  );
  const budget = page.getByRole("region", { name: "费用与预算", exact: true });
  await budget
    .getByRole("button", { name: /更正实际总额|填实际总额/ })
    .first()
    .click();
  await evidence(page, info, "budget", await metrics(page));
  const form = budget.getByRole("form", { name: "填写实际总额", exact: true });
  await form.getByRole("textbox").fill("28.50");
  await page.route("**/api/backend/api/versions/*/expenses**", (route) =>
    ["POST", "PUT"].includes(route.request().method())
      ? route.fulfill({ status: 503, json: error })
      : route.continue(),
  );
  await form.getByRole("button", { name: "保存实际总额", exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText(
    error.error.message,
  );
  await evidence(page, info, "budget-failed", {
    retainedAmount: await form.getByRole("textbox").inputValue(),
    formPosition: await form.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        top: rect.top,
        bottom: rect.bottom,
        inViewport: rect.bottom > 56 && rect.top < innerHeight,
        scrollY,
      };
    }),
  });
});

test("empty and failed route states", async ({ page }, info) => {
  for (const [name, url] of [
    ["home", "/"],
    ["sessions", "/sessions"],
    ["compare", "/compare"],
    ["recaps", "/recaps"],
    ["career", "/career"],
  ]) {
    await page.goto(url);
    await ready(page);
    await evidence(page, info, `${name}-empty`, await metrics(page));
  }
  const trip = await seed(page, "盘点加载失败");
  await page.route("**/api/backend/api/**", (route) =>
    route.fulfill({ status: 503, json: error }),
  );
  for (const [name, url] of [
    ["sessions", "/sessions"],
    ["settings", "/settings"],
    ["preferences", "/preferences"],
    ["recaps", "/recaps"],
    ["recap-detail", `/recaps/${trip.version}`],
    ["career", "/career"],
    ["chat", `/chat/${trip.session_id}`],
    ["workflow", `/workflow/${trip.session_id}`],
  ]) {
    await page.goto(url);
    await ready(page);
    await evidence(page, info, `${name}-failed`, await metrics(page));
  }
});

test("preference draft survives pending and failed save", async ({
  page,
}, info) => {
  await page.goto("/preferences");
  const input = page.getByRole("textbox", {
    name: "编辑常用偏好",
    exact: true,
  });
  await input.fill("少走路\n喜欢博物馆");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let pending = false;
  await page.route("**/api/backend/api/travel-preferences", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    pending = true;
    await gate;
    await route.fulfill({ status: 503, json: error });
  });
  const save = page.getByRole("button", { name: "保存常用偏好", exact: true });
  await save.click();
  await expect.poll(() => pending).toBe(true);
  await evidence(page, info, "preferences-pending", {
    disabled: await save.isDisabled(),
    text: await save.innerText(),
    retained: await input.inputValue(),
  });
  release();
  await expect(page.locator("main").getByRole("alert")).toContainText(
    error.error.message,
  );
  await evidence(page, info, "preferences-save-failed", {
    retained: await input.inputValue(),
    metrics: await metrics(page),
  });
});

test("home preference entry under ready and unconfigured services", async ({
  page,
}, info) => {
  let configured = true;
  await page.route("**/api/backend/api/system/config", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.llm.ready = configured;
    data.search.ready = configured;
    data.warnings = configured ? [] : ["盘点用未配置状态"];
    await route.fulfill({ json: data });
  });
  await page.route("**/api/backend/api/travel-preferences", (route) =>
    route.fulfill({
      json: {
        preferences: ["少走路", "喜欢博物馆"],
        revision: 1,
        updated_at: null,
      },
    }),
  );
  await page.goto("/");
  await ready(page);
  await expect(
    page.getByRole("region", { name: "新旅行偏好", exact: true }),
  ).toBeVisible();
  await evidence(page, info, "home-configured", {
    preferenceEntryCount: await page
      .getByRole("region", { name: "新旅行偏好", exact: true })
      .count(),
    metrics: await metrics(page),
  });
  configured = false;
  await page.reload();
  await ready(page);
  await expect(
    page.getByRole("region", { name: "新旅行偏好", exact: true }),
  ).toBeVisible();
  await evidence(page, info, "home-unconfigured", {
    preferenceEntryCount: await page
      .getByRole("region", { name: "新旅行偏好", exact: true })
      .count(),
    metrics: await metrics(page),
  });
});
