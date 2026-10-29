import { expect, test, type Page } from "playwright/test";
import type { SettingsPayload, SettingsUpdate } from "../../lib/settings-types";

const proxy = "/api/backend";
const settingsUrl = "**/api/backend/api/system/settings";
const preferencesUrl = /\/api\/backend\/api\/travel-preferences(?:\?.*)?$/;
const stored: SettingsPayload = {
  llm_provider: "fixture",
  llm_model: "saved-model",
  llm_base_url: "http://127.0.0.1:9",
  llm_has_key: true,
  search_provider: "fixture",
  searxng_base_url: "",
  search_has_key: true,
  amap_has_key: true,
  default_detail_level: "L2_standard",
  env_path: "/isolated/config/.env",
};
const providers = {
  llm: [
    {
      id: "fixture",
      name: "很长的模型服务名称".repeat(10),
      requires_api_key: true,
      docs_url: "",
      default_base_url: null,
      note: "隔离界面验证",
    },
  ],
  search: [
    {
      id: "fixture",
      name: "隔离检索服务",
      requires_api_key: true,
      docs_url: "",
      default_base_url: null,
      note: "隔离界面验证",
    },
  ],
};
const failure = {
  error: { code: "BACKEND_UNREACHABLE", message: "隔离请求失败，输入仍保留。" },
};
async function mockSettings(page: Page) {
  await page.route("**/api/backend/api/system/providers", (route) =>
    route.fulfill({ json: providers }),
  );
  await page.route("**/api/backend/api/system/models", (route) =>
    route.fulfill({ json: { models: [], message: "隔离模型列表" } }),
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
  const trip = await (await page.request.post(`${proxy}/test/moves`)).json();
  const title = "界面旅行 " + crypto.randomUUID();
  await page.request.patch(`${proxy}/api/sessions/${trip.session_id}`, {
    data: { title },
  });
  return { ...trip, title };
}
test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1:3137(?:\/|$))/, (route) =>
    route.abort(),
  );
});

test("首页突出开始旅行，偏好默认不选，最近旅行链接可用且没有营销渐变", async ({
  page,
  isMobile,
}) => {
  const trip = await seed(page);
  await page.route(preferencesUrl, (route) =>
    route.fulfill({
      json: {
        preferences: ["慢节奏", "少走路"],
        revision: 1,
        updated_at: null,
      },
    }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "想去哪儿旅行？", exact: true }),
  ).toBeVisible();
  const start = page.getByRole("region", { name: "开始新的旅行", exact: true });
  await expect(
    start.getByRole("checkbox", { name: "本次使用常用偏好", exact: true }),
  ).not.toBeChecked();
  expect(
    await start.evaluate((el) => getComputedStyle(el).backgroundImage),
  ).toBe("none");
  const create = start.getByRole("button", { name: "新建旅行", exact: true });
  expect((await create.boundingBox())!.height).toBeGreaterThanOrEqual(
    isMobile ? 44 : 40,
  );
  await noOverflow(page);
  const recent = page.getByRole("list", { name: "最近旅行", exact: true });
  const link = recent.getByRole("link").filter({
    has: page.getByRole("heading", { name: trip.title, exact: true }),
  });
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(`/chat/${trip.session_id}`);
});

test("设置分区可直达且保留草稿，长服务名称不撑宽手机，产品标题没有 emoji", async ({
  page,
  isMobile,
}) => {
  await mockSettings(page);
  await page.route(settingsUrl, (route) => route.fulfill({ json: stored }));
  await page.goto("/settings");
  const model = page.getByLabel("模型名称", { exact: true });
  await model.fill("unsaved-model");
  await expect(page.getByText("有未保存的更改", { exact: true })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "设置分区", exact: true });
  await nav.getByRole("link", { name: "高德地图", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "高德地图", exact: true }),
  ).toBeInViewport();
  const map = page.getByRole("region", { name: "高德地图", exact: true });
  await expect(map.getByLabel("Web 服务 Key", { exact: true })).toHaveAttribute(
    "type",
    "password",
  );
  await nav.getByRole("link", { name: "模型服务", exact: true }).click();
  await expect(model).toHaveValue("unsaved-model");
  const inputSize = await model.evaluate((el) =>
    parseFloat(getComputedStyle(el).fontSize),
  );
  expect(inputSize).toBeGreaterThanOrEqual(isMobile ? 16 : 14);
  for (const text of await page.getByRole("heading").allTextContents())
    expect(text).not.toMatch(/\p{Extended_Pictographic}/u);
  await noOverflow(page);
});

test("保存失败保留密钥草稿，确认保存后清空，留空不覆盖已有密钥", async ({
  page,
}) => {
  await mockSettings(page);
  let current = { ...stored };
  const writes: SettingsUpdate[] = [];
  await page.route(settingsUrl, async (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({ json: current });
    const data = route.request().postDataJSON() as SettingsUpdate;
    writes.push(data);
    if (writes.length === 1)
      return route.fulfill({ status: 503, json: failure });
    current = {
      ...current,
      llm_model: data.llm_model!,
      amap_has_key: data.amap_api_key === "" ? false : current.amap_has_key,
    };
    await route.fulfill({
      json: {
        saved: true,
        changed: [],
        message: "隔离设置已保存",
        reloaded: true,
      },
    });
  });
  await page.goto("/settings");
  const model = page.getByRole("region", { name: "大模型", exact: true });
  const search = page.getByRole("region", { name: "信息检索", exact: true });
  const actions = page.getByRole("region", {
    name: "设置保存操作",
    exact: true,
  });
  await model
    .getByLabel("API Key", { exact: true })
    .fill("isolated-model-input");
  await search
    .getByLabel("API Key", { exact: true })
    .fill("isolated-search-input");
  await page
    .getByRole("checkbox", { name: "保存时移除高德 Key", exact: true })
    .check();
  await actions.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(actions.getByRole("alert")).toContainText("隔离请求失败");
  await expect(model.getByLabel("API Key", { exact: true })).toHaveValue(
    "isolated-model-input",
  );
  await expect(page.locator("body")).not.toContainText("isolated-model-input");
  await actions.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(actions.getByRole("status")).toContainText("隔离设置已保存");
  expect(writes[0]).toEqual(writes[1]);
  expect(writes[1]).toMatchObject({
    llm_api_key: "isolated-model-input",
    search_api_key: "isolated-search-input",
    amap_api_key: "",
  });
  await expect(model.getByLabel("API Key", { exact: true })).toHaveValue("");
  await expect(search.getByLabel("API Key", { exact: true })).toHaveValue("");
  await model.getByLabel("模型名称", { exact: true }).fill("updated-model");
  await actions.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(actions.getByRole("status")).toContainText("隔离设置已保存");
  expect(writes).toHaveLength(3);
  for (const key of ["llm_api_key", "search_api_key", "amap_api_key"])
    expect(writes[2]).not.toHaveProperty(key);
});

test("设置已保存后读取失败，刷新配置保留后来输入的新草稿且不重复写入", async ({
  page,
}) => {
  await mockSettings(page);
  let readsFail = false,
    writes = 0;
  await page.route(settingsUrl, async (route) => {
    if (route.request().method() === "GET") {
      if (readsFail) {
        readsFail = false;
        return route.fulfill({ status: 503, json: failure });
      }
      return route.fulfill({ json: stored });
    }
    writes++;
    readsFail = true;
    await route.fulfill({
      json: {
        saved: true,
        changed: [],
        message: "设置已确认保存",
        reloaded: true,
      },
    });
  });
  await page.goto("/settings");
  const actions = page.getByRole("region", {
    name: "设置保存操作",
    exact: true,
  });
  await page.getByLabel("模型名称", { exact: true }).fill("confirmed-input");
  await actions.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(actions.getByRole("alert")).toContainText(
    "设置已保存，但配置状态刷新失败",
  );
  await page.getByLabel("模型名称", { exact: true }).fill("new-unsaved-input");
  await actions.getByRole("button", { name: "重试", exact: true }).click();
  await expect(actions.getByRole("alert")).toHaveCount(0);
  await expect(page.getByLabel("模型名称", { exact: true })).toHaveValue(
    "new-unsaved-input",
  );
  expect(writes).toBe(1);
});

test("连接测试及模型列表结果留在对应分区，等待时不能重复发起", async ({
  page,
}) => {
  await mockSettings(page);
  await page.route(settingsUrl, (route) => route.fulfill({ json: stored }));
  let calls = 0,
    release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/backend/api/system/test-llm", async (route) => {
    calls++;
    await gate;
    await route.fulfill({
      json: { ok: false, provider: "fixture", message: "隔离鉴权失败" },
    });
  });
  await page.goto("/settings");
  const region = page.getByRole("region", { name: "大模型", exact: true });
  await region.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(
    region.getByRole("button", { name: "正在测试…", exact: true }),
  ).toBeDisabled();
  await expect(region.getByRole("status")).toContainText("正在测试已保存");
  expect(calls).toBe(1);
  release();
  await expect(region.getByRole("alert")).toContainText("隔离鉴权失败");
  await region
    .getByRole("button", { name: "获取可用模型", exact: true })
    .click();
  await expect(region.getByRole("status")).toContainText("隔离模型列表");
  await noOverflow(page);
});

test("偏好编辑与已保存内容两端均可读，删除等待不会误显示保存中", async ({
  page,
  isMobile,
}) => {
  let current = { preferences: ["慢节奏"], revision: 1, updated_at: null };
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(preferencesUrl, async (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({ json: current });
    await gate;
    current = { ...current, preferences: [], revision: 2 };
    await route.fulfill({ json: current });
  });
  await page.goto("/preferences");
  const form = page.getByRole("form", {
    name: "常用偏好编辑",
    exact: true,
  });
  const saved = page.getByRole("region", {
    name: "当前已保存偏好",
    exact: true,
  });
  const editor = form.getByLabel("编辑常用偏好", { exact: true });
  await expect(editor).toHaveValue("慢节奏");
  const boxes = await page.evaluate(() => ({
    editor: document
      .querySelector(".tf-preference-editor")!
      .getBoundingClientRect()
      .toJSON(),
    saved: document
      .querySelector(".tf-preference-saved")!
      .getBoundingClientRect()
      .toJSON(),
  }));
  if (isMobile)
    expect(boxes.editor.bottom).toBeLessThanOrEqual(boxes.saved.top);
  else expect(boxes.editor.right).toBeLessThan(boxes.saved.left);
  await form.getByRole("button", { name: "删除常用偏好", exact: true }).click();
  await expect(
    form.getByRole("button", { name: "删除常用偏好", exact: true }),
  ).toHaveAttribute("aria-busy", "true");
  await expect(
    form.getByRole("button", { name: "保存常用偏好", exact: true }),
  ).not.toHaveAttribute("aria-busy", "true");
  await expect(form.getByRole("status")).toContainText("正在删除常用偏好");
  release();
  await expect(form.getByRole("status")).toContainText("常用偏好已删除");
  await expect(saved).toContainText("尚无常用偏好");
  await noOverflow(page);
});

test("旅行管理长名称与筛选适配手机，列表操作链接具备焦点和按压反馈", async ({
  page,
  isMobile,
}) => {
  const trip = await seed(page);
  const title = "旅行名称".repeat(20) + trip.session_id;
  await page.request.patch(`${proxy}/api/sessions/${trip.session_id}`, {
    data: { title },
  });
  await page.goto("/sessions");
  await page
    .getByRole("textbox", { name: "搜索旅行名称", exact: true })
    .fill(title);
  const row = page.getByRole("listitem", {
    name: `旅行：${title}`,
    exact: true,
  });
  await expect(row).toBeVisible();
  const open = row.getByRole("link", { name: "打开", exact: true });
  expect((await open.boundingBox())!.height).toBeGreaterThanOrEqual(
    isMobile ? 44 : 40,
  );
  await page.keyboard.press("Tab");
  await open.focus();
  await expect(open).toHaveCSS("outline-width", "2px");
  if (!isMobile) {
    const before = await open.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );
    await open.hover();
    await expect
      .poll(() => open.evaluate((el) => getComputedStyle(el).backgroundColor))
      .not.toBe(before);
  }
  await noOverflow(page);
});

test("DeepSeek 检索可留空复用官方模型 Key，保存与测试保留现有流程", async ({
  page,
}) => {
  await mockSettings(page);
  await page.route("**/api/backend/api/system/providers", (route) =>
    route.fulfill({
      json: {
        ...providers,
        search: [
          ...providers.search,
          {
            id: "deepseek",
            name: "DeepSeek 网页搜索",
            requires_api_key: true,
            docs_url: "https://api-docs.deepseek.com/zh-cn/guides/search/",
            default_base_url: null,
            note: "独立网页搜索接口，可复用 DeepSeek 官方模型 Key；按次计费",
          },
        ],
      },
    }),
  );
  let current = { ...stored };
  const writes: SettingsUpdate[] = [];
  await page.route(settingsUrl, async (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({ json: current });
    const update = route.request().postDataJSON() as SettingsUpdate;
    writes.push(update);
    current = { ...current, search_provider: update.search_provider! };
    return route.fulfill({
      json: {
        saved: true,
        changed: ["SEARCH_PROVIDER"],
        message: "检索配置已保存",
        reloaded: true,
      },
    });
  });
  await page.route("**/api/backend/api/system/test-search", (route) =>
    route.fulfill({
      json: {
        ok: true,
        provider: "deepseek",
        message: "连接正常，返回 1 条结果",
      },
    }),
  );
  await page.goto("/settings");
  const search = page.getByRole("region", { name: "信息检索", exact: true });
  await search.getByLabel("检索服务", { exact: true }).selectOption("deepseek");
  await expect(search).toContainText("未单独配置时，可复用 DeepSeek");
  await expect(search).toContainText("网页搜索按次计费");
  await expect(search.getByLabel("API Key", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "设置保存操作", exact: true })
      .getByRole("status"),
  ).toContainText("检索配置已保存");
  expect(writes).toHaveLength(1);
  expect(writes[0].search_provider).toBe("deepseek");
  expect(writes[0]).not.toHaveProperty("llm_api_key");
  expect(writes[0]).not.toHaveProperty("search_api_key");
  await search.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(search).toContainText("连接正常，返回 1 条结果");
  await page.reload();
  await expect(search.getByLabel("检索服务", { exact: true })).toHaveValue(
    "deepseek",
  );
  await noOverflow(page);
});
