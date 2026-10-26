const root = document.documentElement;
const responsive = window.matchMedia("(max-width: 900px)");
let previewDevice = responsive.matches ? "mobile" : "desktop";
const settings = {
  theme: "paper",
  desktopDensity: "airy",
  mobileDensity: "compact",
};
const stops = [
  {
    id: "lake",
    time: "09:30",
    title: "柳浪闻莺",
    meta: "湖边散步 · 已完成",
    done: true,
    x: 49,
    y: 20,
  },
  {
    id: "lunch",
    time: "12:00",
    title: "午餐与休息",
    meta: "午餐后留出休息时间",
    done: false,
    x: 62,
    y: 44.4,
  },
  {
    id: "afternoon",
    time: "14:00",
    title: "湖边步道与街巷",
    meta: "自由探索，随时调整",
    done: false,
    x: 65,
    y: 77.8,
  },
];
let selected = "lunch";
const dayPlans = {
  1: [
    { time: "10:30", title: "抵达与入住", meta: "先安顿行李", done: false },
    {
      time: "12:30",
      title: "午餐与休息",
      meta: "按抵达时间从容安排",
      done: false,
    },
    {
      time: "15:00",
      title: "老街散步",
      meta: "留出熟悉城市的时间",
      done: false,
    },
  ],
  2: stops.map(({ time, title, meta, done }) => ({ time, title, meta, done })),
  3: [
    {
      time: "09:30",
      title: "早餐与自由安排",
      meta: "收尾前留一点空白",
      done: false,
    },
    {
      time: "12:00",
      title: "退房与午餐",
      meta: "核对行李和出发时间",
      done: false,
    },
    {
      time: "15:00",
      title: "从容返程",
      meta: "按实际车次安排出发",
      done: false,
    },
  ],
};
let recordedCents = 0;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
function icons() {
  $$("[data-icon]").forEach((element) => {
    element.innerHTML = window.TravelFlowIcons[element.dataset.icon] || "";
  });
}
function renderRoutes() {
  $$("[data-route-list]").forEach((list) => {
    list.innerHTML = stops
      .map(
        (stop, index) =>
          `<li class="route-stop${stop.id === selected ? " selected" : ""}${stop.done ? " done" : ""}" data-stop-row="${stop.id}"><button class="stop-select" data-stop="${stop.id}" aria-pressed="${stop.id === selected}"><time>${stop.time}</time><span class="stop-index" aria-hidden="true"></span><span><span class="stop-title" data-stop-title="${stop.id}"></span><span class="stop-meta"></span></span></button><div class="stop-tools"><button data-action="navigate"><span data-icon="navigation"></span>导航到这里</button><button data-action="done"><span data-icon="check"></span>${stop.done ? "恢复未完成" : "标记完成"}</button></div>${index < stops.length - 1 ? '<div class="leg">接着走，路线与下一站衔接</div>' : ""}</li>`,
      )
      .join("");
    stops.forEach((stop) => {
      list.querySelector(`[data-stop-title="${stop.id}"]`).textContent =
        stop.title;
      list.querySelector(
        `[data-stop-row="${stop.id}"] .stop-meta`,
      ).textContent = stop.meta;
    });
  });
  icons();
}
const mapSvg = `<svg viewBox="0 0 500 360" preserveAspectRatio="none" role="img" aria-label="路线位置示意，不代表真实地理坐标"><rect width="500" height="360" fill="var(--map)"/><path d="M0 0H250C210 75 250 100 206 160S222 290 170 360H0Z" fill="var(--water)"/><path d="M295 0H500V360H245L267 300L245 230L283 170L265 95Z" fill="var(--land)"/><g fill="none" stroke="var(--paper)" stroke-width="10"><path d="M285 35L500 65M275 107L500 143M260 225L500 260M260 300L500 325M385 0L345 360"/></g><path d="M245 72C236 109 300 114 310 160S300 236 325 280" fill="none" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/><text x="70" y="180" fill="var(--muted)" font-family="sans-serif" font-size="17">湖面</text></svg>`;
$$("[data-map]").forEach((map) => {
  map.innerHTML =
    mapSvg +
    stops
      .map(
        (stop, index) =>
          `<button class="map-pin" style="--x:${stop.x}%;--y:${stop.y}%" data-stop="${stop.id}" aria-label="地图站点 ${index + 1}" aria-pressed="${stop.id === selected}">${index + 1}</button>`,
      )
      .join("");
});
function updateSelection() {
  const stop = stops.find((item) => item.id === selected);
  $$("[data-stop]").forEach((button) =>
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.stop === selected),
    ),
  );
  $$("[data-stop-row]").forEach((row) =>
    row.classList.toggle("selected", row.dataset.stopRow === selected),
  );
  $$(".current-title").forEach((element) => {
    element.textContent = stop.title;
  });
  $(".current-time").textContent = stop.time;
}
function applyView() {
  const phone = previewDevice === "mobile" || responsive.matches;
  $("#desktop-app").hidden = phone;
  $("#phone-app").hidden = !phone;
  root.dataset.preview = phone ? "mobile" : "desktop";
  root.dataset.theme = settings.theme;
  root.dataset.density = phone
    ? settings.mobileDensity
    : settings.desktopDensity;
  $$("[data-layout-choice]").forEach((button) =>
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.layoutChoice === root.dataset.layout),
    ),
  );
  $$("[data-mobile-choice]").forEach((button) =>
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.mobileChoice === root.dataset.mobileLayout),
    ),
  );
  $$("[data-theme-choice]").forEach((button) =>
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.themeChoice === settings.theme),
    ),
  );
  const descriptions = {
    journey: "电脑 D1 · 行程主导，地图与调整在旁边。",
    map: "电脑 D2 · 地图主导，地点选择与路线直接对应。",
    conversation: "电脑 D3 · 对话主导，行程作为旁边的实时结果。",
  };
  $("#preview-description").textContent = phone
    ? root.dataset.mobileLayout === "today"
      ? "手机 M1 · 打开先看下一站与今日路线。"
      : "手机 M2 · 打开先看地图与接下来的导航。"
    : descriptions[root.dataset.layout];
}
function openDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog.open) dialog.showModal();
}
function feedback(element, text) {
  const panel = element.closest(".panel");
  const status = panel?.querySelector("[data-feedback]");
  if (status) status.textContent = text;
  else
    $$(".saved-state").forEach((node) => {
      node.textContent = text;
    });
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || button.disabled) return;
  button.classList.remove("clicked");
  void button.offsetWidth;
  button.classList.add("clicked");
  if (button.dataset.layoutChoice) {
    root.dataset.layout = button.dataset.layoutChoice;
    previewDevice = "desktop";
    applyView();
  }
  if (button.dataset.mobileChoice) {
    root.dataset.mobileLayout = button.dataset.mobileChoice;
    previewDevice = "mobile";
    applyView();
  }
  if (button.dataset.themeChoice) {
    settings.theme = button.dataset.themeChoice;
    applyView();
  }
  if (button.dataset.close)
    document.getElementById(button.dataset.close).close();
  if (button.dataset.stop) {
    selected = button.dataset.stop;
    updateSelection();
  }
  const action = button.dataset.action;
  if (action === "settings") openDialog("settings-dialog");
  if (action === "book") openDialog("book-dialog");
  if (action === "advanced") openDialog("advanced-dialog");
  if (action === "phone-chat") openDialog("phone-chat-dialog");
  if (action === "expense") {
    $("#expense-error").textContent = "";
    openDialog("expense-dialog");
  }
  if (action === "navigate")
    feedback(button, "从上一站导航的入口已响应（样稿，不申请定位或跳转）。");
  if (action === "show-journey") {
    root.dataset.layout = "journey";
    previewDevice = "desktop";
    applyView();
  }
  if (action === "toggle-mobile-layout") {
    root.dataset.mobileLayout =
      root.dataset.mobileLayout === "today" ? "map" : "today";
    applyView();
    $("#phone-app .phone-content").scrollTop = 0;
  }
  if (action === "day") {
    $$(".days button").forEach((item) =>
      item.setAttribute("aria-pressed", String(item === button)),
    );
    const day = button.dataset.day;
    stops.forEach((stop, index) => Object.assign(stop, dayPlans[day][index]));
    selected = "lunch";
    $(".daybar h1").textContent =
      `第 ${day} 天，${{ 1: "抵达与老街", 2: "西湖与街巷", 3: "自由安排与返程" }[day]}`;
    $(".phone-header h1").textContent = `今天，第 ${day} 天`;
    renderRoutes();
    updateSelection();
  }
  if (action === "done") {
    const stop = stops.find((item) => item.id === selected);
    stop.done = !stop.done;
    stop.meta = stop.done
      ? "已完成，记录已保留（样稿）"
      : "尚未完成，可继续安排";
    if (stop.done) selected = stops.find((item) => !item.done)?.id || selected;
    renderRoutes();
    updateSelection();
    feedback(button, "站点状态已更新（样稿）。");
  }
  if (action === "preview-change") {
    if ($("#phone-chat-dialog").open) $("#phone-chat-dialog").close();
    openDialog("change-dialog");
  }
  if (action === "apply-change") {
    stops.find((item) => item.id === "afternoon").title = "湖边喝茶与休息";
    renderRoutes();
    updateSelection();
    $("#change-dialog").close();
    $$(".saved-state").forEach((node) => {
      node.textContent = "下午安排已更新（样稿）";
    });
  }
});
$("#desktop-density").addEventListener("change", (event) => {
  settings.desktopDensity = event.target.value;
  applyView();
});
$("#mobile-density").addEventListener("change", (event) => {
  settings.mobileDensity = event.target.value;
  applyView();
});
responsive.addEventListener("change", applyView);
$$(".chat-form").forEach((form) =>
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = form.querySelector("textarea");
    const text = input.value.trim();
    if (!text) {
      input.focus();
      return;
    }
    const button = form.querySelector("button");
    if (button.disabled) return;
    const status = form.querySelector(".chat-status");
    const original = button.innerHTML;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "整理中…";
    status.textContent = "正在整理这次调整（样稿）…";
    window.setTimeout(() => {
      const dialogue = form.previousElementSibling;
      const message = document.createElement("p");
      message.className = "user-message";
      message.textContent = text;
      dialogue.append(message);
      input.value = "";
      button.innerHTML = original;
      button.disabled = false;
      button.removeAttribute("aria-busy");
      status.textContent = "调整建议已准备好，可核对前后变化（样稿）。";
      const preview = document.createElement("button");
      preview.dataset.action = "preview-change";
      preview.textContent = "核对这次调整";
      dialogue.append(preview);
    }, 650);
  }),
);
$("#expense-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("#expense-amount").value.trim();
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(value) || Number(value) <= 0) {
    $("#expense-error").textContent = "请填写大于 0、最多两位小数的金额。";
    return;
  }
  recordedCents += Math.round(Number(value) * 100);
  $$("[data-recorded]").forEach((element) => {
    element.textContent = `¥${(recordedCents / 100).toFixed(2)}`;
  });
  $("#expense-dialog").close();
  $("#expense-amount").value = "";
  $$(".saved-state").forEach((element) => {
    element.textContent = "消费已记录（样稿）";
  });
  $("#phone-app .phone-route [data-feedback]").textContent =
    "这笔消费已记录，账目与当前旅行同步（样稿）。";
  $("#phone-app .phone-next [data-feedback]").textContent =
    `已记消费 ¥${(recordedCents / 100).toFixed(2)}（样稿）。`;
  $("#desktop-app .journey-panel [data-feedback]").textContent =
    `已记消费 ¥${(recordedCents / 100).toFixed(2)}（样稿）。`;
});
const query = new URLSearchParams(location.search);
if (["journey", "map", "conversation"].includes(query.get("desktop")))
  root.dataset.layout = query.get("desktop");
if (["today", "map"].includes(query.get("mobile"))) {
  root.dataset.mobileLayout = query.get("mobile");
  previewDevice = "mobile";
}
renderRoutes();
updateSelection();
applyView();
icons();
