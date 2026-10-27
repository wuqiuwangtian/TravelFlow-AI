(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const root = document.documentElement;
  const esc = (value) =>
    String(value).replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  const money = (cents) => `¥${(cents / 100).toFixed(2)}`;
  const responsive = matchMedia("(max-width: 900px)");
  const state = {
    device: responsive.matches ? "mobile" : "desktop",
    workspace: "trip",
    mode: "direct",
    panel: "route",
    changed: false,
    pending: false,
    selected: 1,
    done: [true, false, false],
    actual: {},
    custom: [],
    revision: 1,
    busy: false,
    editing: null,
  };
  function icons() {
    $$("[data-icon]").forEach((el) => {
      el.innerHTML = window.TravelFlowIcons[el.dataset.icon] || "";
    });
  }
  function view() {
    root.dataset.device = responsive.matches ? "mobile" : state.device;
    root.dataset.density =
      root.dataset.device === "mobile" ? "compact" : "airy";
    root.dataset.panel = state.panel;
    $("#trip-workspace").hidden = state.workspace !== "trip";
    $("#graph-workspace").hidden = state.workspace !== "graph";
    for (const [key, value] of [
      ["device", root.dataset.device],
      ["workspace", state.workspace],
      ["revision", state.mode],
      ["panel", state.panel],
      ["theme", root.dataset.theme],
      ["inspector", root.dataset.inspector],
    ]) {
      $$(`button[data-${key}]`).forEach((button) =>
        button.setAttribute(
          "aria-pressed",
          String(button.dataset[key] === value),
        ),
      );
    }
    $("#mode-description").textContent =
      state.mode === "direct"
        ? "R1 推荐：一句话改好行程、路线和预估费用，保留修改说明与撤销。"
        : "R2：先把改动放在对话中核对，采用后才更新行程与预估费用。";
  }
  function stops() {
    return [
      { title: "柳浪闻莺", time: "09:30", meta: "湖边散步 · 已完成" },
      { title: "午餐与休息", time: "12:00", meta: "留出午休，不赶路" },
      {
        title: state.changed ? "湖边喝茶与休息" : "湖边步道与街巷",
        time: "14:00",
        meta: state.changed
          ? "步行约 1.2km · 茶歇预估 ¥48"
          : "步行约 3.8km · 自由探索",
      },
    ];
  }
  function entries() {
    return [
      { id: "meal", title: "饮食", plan: 16000 },
      { id: "lodging", title: "住宿", plan: 68000 },
      { id: "transport", title: "交通", plan: 24000 },
      { id: "ticket", title: "门票", plan: 20000 },
      ...(state.changed || state.actual.tea !== undefined
        ? [{ id: "tea", title: "下午茶歇", plan: state.changed ? 4800 : 0 }]
        : []),
      ...state.custom,
    ];
  }
  function totals() {
    return entries().reduce(
      (all, row) => ({
        planned: all.planned + row.plan,
        actual: all.actual + (state.actual[row.id] ?? 0),
        projected: all.projected + (state.actual[row.id] ?? row.plan),
      }),
      { planned: 0, actual: 0, projected: 0 },
    );
  }
  function summary() {
    const sums = totals(),
      balance = 150000 - sums.projected;
    $("#budget-summary").innerHTML =
      `<span class="muted">预计总支出</span><strong data-projected>${money(sums.projected)}</strong><p>已记 ${money(sums.actual)} · 清单预估 ${money(sums.planned)}</p><p data-over="${balance < 0}" data-balance>${balance < 0 ? "预计超预算" : "预计预算余量"} ${money(Math.abs(balance))} · 总预算 ¥1,500</p>`;
    $("#trip-revision").textContent =
      `示例版本 ${state.revision} · 行程、费用与路书同步`;
    renderBook();
  }
  function renderRoute() {
    const items = stops();
    $("#task-route").innerHTML = items
      .map(
        (stop, index) =>
          `<li class="route-stop ${state.selected === index ? "selected" : ""} ${state.done[index] ? "done" : ""} ${index === 0 ? "has-photo" : ""}"><button class="stop-select" data-stop="${index}" aria-pressed="${state.selected === index}"><time>${stop.time}</time><span class="stop-index" aria-hidden="true"></span><span><span class="stop-title">${stop.title}</span><span class="stop-meta">${index === 0 && !state.done[index] ? "湖边散步" : stop.meta}</span></span></button>${index === 0 ? '<figure class="stop-visual"><button data-action="photo" aria-label="查看西湖参考照片，非该站实拍"><img src="./assets/west-lake-reference.jpg" alt="西湖参考照片"><span>查看图</span></button><figcaption>西湖参考图 · 非该站实拍</figcaption></figure>' : ""}${index < 2 ? '<div class="leg">沿路线接着走</div>' : ""}</li>`,
      )
      .join("");
    $("#task-route img").addEventListener("error", (event) => {
      const figure = event.target.closest("figure");
      figure.parentElement.classList.remove("has-photo");
      figure.remove();
    });
    const current = items[state.selected];
    $("#next-label").textContent =
      `${state.done.every(Boolean) ? "今日已完成，当前查看" : "当前站点"} · ${current.time}`;
    $("#next-title").textContent = current.title;
    $("#next-origin").textContent = state.selected
      ? `从${items[state.selected - 1].title}出发`
      : "首站可从当前位置出发";
    $('[data-action="done"]').textContent = state.done[state.selected]
      ? "恢复未完成"
      : "我已完成";
    $("#task-map").innerHTML =
      `<svg viewBox="0 0 350 170" preserveAspectRatio="none" aria-label="连续路线示意，不代表真实位置"><rect width="350" height="170" fill="var(--map)"/><path d="M0 0H135Q110 80 170 170H0Z" fill="var(--water)"/><path d="M155 33C280 23 90 110 260 135" fill="none" stroke="var(--accent)" stroke-width="3"/></svg>${items.map((stop, index) => `<button data-stop="${index}" aria-label="地图站点 ${index + 1}：${stop.title}" aria-pressed="${state.selected === index}" style="left:${[38, 55, 70][index]}%;top:${[8, 40, 65][index]}%">${index + 1}</button>`).join("")}`;
    renderBook();
  }
  function renderBook() {
    const sums = totals();
    $("#book-content").innerHTML =
      `<p>示例版本 ${state.revision} · 第 2 天摘要</p><ol>${stops()
        .map(
          (stop, index) =>
            `<li><strong>${stop.time} ${stop.title}</strong><br>${stop.meta}${state.done[index] ? " · 已完成" : ""}</li>`,
        )
        .join(
          "",
        )}</ol><p>已记消费 ${money(sums.actual)}；预计总支出 ${money(sums.projected)}。</p><p>${150000 - sums.projected < 0 ? "预计超预算" : "预计预算余量"} ${money(Math.abs(150000 - sums.projected))}。</p><p class="muted">这是一份旅行中同一份路书的节选，用于检查修改、执行与记账是否同步。</p>`;
  }
  function renderBudget() {
    $("#budget-rows").innerHTML = entries()
      .map(
        (row) =>
          `<div class="budget-row" data-budget-row="${row.id}"><strong>${esc(row.title)}</strong><span>${money(row.plan)}</span><button data-edit="${row.id}" aria-label="修改${esc(row.title)}实付">${state.actual[row.id] === undefined ? "填实付" : money(state.actual[row.id])}</button></div>`,
      )
      .join("");
    summary();
  }
  function changeResult(pending) {
    $("#change-result").hidden = false;
    $("#change-title").textContent = pending
      ? "准备这样调整，还未改动当前行程"
      : "已改好下午安排";
    $("#change-description").textContent =
      "下午改为湖边喝茶，步行约 3.8km → 1.2km；茶歇预估增加 ¥48。午餐、上午安排与已记消费保留。";
    $('[data-action="apply"]').hidden = !pending;
    $('[data-action="discard"]').hidden = !pending;
    $('[data-action="undo"]').hidden = pending;
  }
  function applyChange() {
    if (state.changed) {
      $("#chat-result").textContent =
        "当前已经是这份轻松路线，没有重复添加茶歇。";
      return;
    }
    state.changed = true;
    state.pending = false;
    state.revision++;
    renderRoute();
    renderBudget();
    changeResult(false);
    $("#chat-result").textContent =
      "已更新行程、地图说明、预估费用和路书（本地演示）。";
  }
  $("#task-chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.busy) return;
    if (state.editing) {
      $("#chat-result").textContent =
        "先保存或取消正在编辑的实付；对话和金额草稿均保留。";
      return;
    }
    const input = $("#task-message"),
      text = input.value.trim();
    if (!text) {
      input.focus();
      return;
    }
    if (state.pending) {
      $("#chat-result").textContent =
        "先采用或保留当前这份修改，再继续调整；你的输入已保留。";
      return;
    }
    const examples = [
      "下午少走一点，留出喝茶时间",
      "下午改成喝茶，别走太远",
      "下午改成喝茶",
    ];
    if (!examples.includes(text.replace(/[。！!？?]+$/g, ""))) {
      $("#chat-result").textContent =
        "这是本地任务样稿，当前可试“下午少走一点，留出喝茶时间”；输入已保留，未请求模型。";
      return;
    }
    state.busy = true;
    const submit = $('#task-chat-form button[type="submit"]');
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    submit.textContent = "整理中…";
    $('[data-action="try-change"]').disabled = true;
    $("#chat-result").textContent = "正在整理路线、费用与修改说明（演示）…";
    const fail = $("#fail-next").checked;
    $("#fail-next").checked = false;
    $$("[data-revision]").forEach((button) => {
      button.disabled = true;
    });
    setTimeout(() => {
      state.busy = false;
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.textContent = "发送";
      $('[data-action="try-change"]').disabled = false;
      $$("[data-revision]").forEach((button) => {
        button.disabled = false;
      });
      if (fail) {
        $("#chat-result").textContent =
          "这次未完成，原行程和输入已保留。可直接再次发送（失败演示）。";
        return;
      }
      const message = document.createElement("p");
      message.className = "user-message";
      message.textContent = text;
      $("#task-dialogue").append(message);
      // Preserve any new draft typed during the wait.
      if (input.value.trim() === text) input.value = "";
      if (state.mode === "review" && !state.changed) {
        state.pending = true;
        changeResult(true);
        $("#chat-result").textContent =
          "修改建议已准备好，当前行程仍保留原安排。";
      } else applyChange();
    }, 650);
  });
  function editBudget(id) {
    if (state.busy) {
      $("#budget-result").textContent =
        "行程正在整理，完成后即可填写；原费用保留。";
      return;
    }
    if (state.editing) {
      $("#budget-result").textContent =
        "先保存或取消正在编辑的费用，草稿仍在原位置。";
      return;
    }
    state.editing = id;
    const row = entries().find((item) => item.id === id),
      element = $(`[data-budget-row="${id}"]`);
    element.querySelector("button").hidden = true;
    const form = document.createElement("form");
    form.className = "budget-editor";
    form.innerHTML = `<label>${esc(row.title)}实付总额<input name="amount" inputmode="decimal" autocomplete="off" value="${state.actual[id] === undefined ? "" : state.actual[id] / 100}" placeholder="例如 68.50" aria-label="${esc(row.title)}实付金额"></label><div class="choice-group"><button type="submit" class="primary">保存实付</button><button type="button" data-cancel-budget>取消</button></div><p role="alert"></p>`;
    element.append(form);
    form.querySelector("input").focus();
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = form.elements.amount.value.trim();
      if (
        !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(value) ||
        Number(value) <= 0
      ) {
        form.querySelector("p").textContent =
          "填写大于 0、最多两位小数的金额。";
        return;
      }
      if (form.dataset.busy) return;
      form.dataset.busy = "true";
      form.querySelectorAll("button,input").forEach((control) => {
        control.disabled = true;
      });
      const save = form.querySelector('[type="submit"]');
      save.textContent = "保存中…";
      save.setAttribute("aria-busy", "true");
      setTimeout(() => {
        state.actual[id] = Math.round(Number(value) * 100);
        state.editing = null;
        state.revision++;
        renderBudget();
        $(`[data-edit="${id}"]`).focus();
        $("#budget-result").textContent =
          `${row.title}已按实付 ${money(state.actual[id])} 结算，差额与路书已同步（演示）。`;
      }, 400);
    });
    form.querySelector("[data-cancel-budget]").addEventListener("click", () => {
      state.editing = null;
      renderBudget();
      $(`[data-edit="${id}"]`).focus();
    });
  }
  function customForm() {
    if (state.editing) {
      $("#budget-result").textContent =
        "先保存或取消正在编辑的费用，草稿仍在原位置。";
      return;
    }
    if ($("#custom-area form")) {
      $("#custom-name").focus();
      return;
    }
    $("#custom-area").innerHTML =
      '<form class="custom-form"><label>项目名称<input id="custom-name" maxlength="40" placeholder="例如 寄存行李"></label><label>实付金额<input id="custom-amount" inputmode="decimal" placeholder="例如 20"></label><div class="choice-group"><button type="submit" class="primary">添加并记账</button><button type="button" data-action="cancel-custom">取消</button></div><p role="alert"></p></form>';
    $("#custom-name").focus();
    $("#custom-area form").addEventListener("submit", (event) => {
      event.preventDefault();
      const name = $("#custom-name").value.trim(),
        amount = $("#custom-amount").value.trim();
      if (
        !name ||
        !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(amount) ||
        Number(amount) <= 0
      ) {
        $("#custom-area [role=alert]").textContent =
          "填写名称及大于 0、最多两位小数的金额。";
        return;
      }
      const id = `custom-${state.custom.length + 1}`;
      if (state.editing) {
        $("#custom-area [role=alert]").textContent =
          "先保存或取消实付草稿，再添加此项；名称和金额已保留。";
        return;
      }
      state.custom.push({ id, title: name, plan: 0 });
      state.actual[id] = Math.round(Number(amount) * 100);
      state.revision++;
      $("#custom-area").innerHTML = "";
      renderBudget();
      $("#budget-result").textContent =
        "自定义费用已加入同一清单与路书（演示）。";
    });
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    button.classList.remove("clicked");
    void button.offsetWidth;
    button.classList.add("clicked");
    if (button.dataset.close)
      document.getElementById(button.dataset.close).close();
    if (button.dataset.device) {
      state.device = button.dataset.device;
      view();
    }
    if (button.dataset.workspace) {
      state.workspace = button.dataset.workspace;
      view();
      if (state.workspace === "graph") requestAnimationFrame(fitGraph);
    }
    if (button.dataset.revision) {
      if (state.pending) {
        $("#mode-description").textContent =
          "先采用或保留当前修改，再切换方式；建议和草稿仍保留。";
        return;
      }
      state.mode = button.dataset.revision;
      view();
    }
    if (button.dataset.theme) {
      root.dataset.theme = button.dataset.theme;
      view();
    }
    if (button.dataset.panel) {
      state.panel = button.dataset.panel;
      view();
      const section = $(`.task-${state.panel}`);
      if (root.dataset.device === "desktop")
        section?.scrollIntoView({ block: "center" });
      else $(".task-body").scrollTop = 0;
    }
    if (button.dataset.stop !== undefined) {
      state.selected = Number(button.dataset.stop);
      renderRoute();
    }
    if (button.dataset.edit) editBudget(button.dataset.edit);
    const action = button.dataset.action;
    if (action === "try-change") {
      $("#task-message").value = "下午少走一点，留出喝茶时间";
      $("#task-chat-form").requestSubmit();
    }
    if (action === "apply") {
      if (state.editing) {
        $("#chat-result").textContent =
          "先保存或取消正在编辑的实付，金额草稿已保留。";
      } else applyChange();
    }
    if (action === "discard") {
      state.pending = false;
      $("#change-result").hidden = true;
      $("#chat-result").textContent = "已保留原安排；费用没有改动。";
    }
    if (action === "undo") {
      if (state.editing) {
        $("#chat-result").textContent =
          "先保存或取消正在编辑的实付，金额草稿已保留。";
        return;
      }
      state.changed = false;
      state.revision++;
      renderRoute();
      renderBudget();
      $("#change-result").hidden = true;
      $("#chat-result").textContent =
        "已撤销这次行程修改；已记实付和自定义费用全部保留（演示）。";
    }
    if (action === "navigate") {
      $("#navigation-result").textContent =
        `${state.selected ? stops()[state.selected - 1].title : "当前位置"} → ${stops()[state.selected].title}（导航响应演示，未跳转）。`;
    }
    if (action === "done") {
      state.done[state.selected] = !state.done[state.selected];
      if (state.done[state.selected]) {
        const next = state.done.findIndex((done) => !done);
        if (next >= 0) state.selected = next;
      }
      state.revision++;
      renderRoute();
      summary();
      $("#navigation-result").textContent =
        "站点状态已同步到路线和路书（演示）。";
    }
    if (action === "settings") $("#task-settings").showModal();
    if (action === "book") {
      renderBook();
      $("#task-book").showModal();
    }
    if (action === "photo") $("#photo-dialog").showModal();
    if (action === "custom") customForm();
    if (action === "cancel-custom") {
      $("#custom-area").innerHTML = "";
      $('[data-action="custom"]').focus();
    }
  });

  // A small, local graph editor for comparing layout and task feedback. Node
  // kinds and ports mirror the existing studio vocabulary; execution is mocked.
  const kinds = {
    input: { title: "文本输入", inputs: [], out: "text" },
    candidates: {
      title: "候选景点池",
      inputs: [["context", "text", "需求"]],
      out: "candidates",
    },
    merge: {
      title: "候选合并",
      inputs: [["candidates", "candidates", "多路候选"]],
      out: "candidates",
    },
    allocate: {
      title: "分日分配",
      inputs: [["candidates", "candidates", "候选景点"]],
      out: "itinerary",
    },
    plan: {
      title: "景点与日程",
      inputs: [["context", "text", "需求"]],
      out: "itinerary",
    },
    budget: {
      title: "预算汇总",
      inputs: [["itinerary", "itinerary", "行程"]],
      out: "budget",
    },
    output: {
      title: "成稿输出",
      inputs: [
        ["itinerary", "itinerary", "行程"],
        ["budget", "budget", "预算"],
      ],
      out: null,
    },
  };
  const portNames = {
    text: "文本",
    candidates: "候选景点",
    itinerary: "行程",
    budget: "预算",
  };
  let graph = { nodes: [], edges: [] },
    selectedNode = null,
    pending = null,
    history = [],
    nodeSerial = 0,
    graphBusy = false;
  let viewport = { x: 20, y: 20, zoom: 0.8 },
    drag = null;
  const nodeDrafts = new Map();
  function node(kind, x, y, title) {
    return {
      id: `node-${++nodeSerial}`,
      kind,
      title: title || kinds[kind].title,
      x,
      y,
      prompt:
        kind === "input"
          ? "杭州，两人三天，希望少走路、安排休息。"
          : "保留具体地点与理由，节奏从容。",
      dirty: true,
      artifact: null,
    };
  }
  function edge(source, target, port) {
    return { source: source.id, target: target.id, port };
  }
  function snapshot() {
    history.push(structuredClone({ graph, selectedNode }));
    if (history.length > 30) history.shift();
  }
  function invalidate(id) {
    const seen = new Set();
    const visit = (id) => {
      if (seen.has(id)) return;
      seen.add(id);
      const item = graph.nodes.find((n) => n.id === id);
      if (item) item.dirty = true;
      graph.edges
        .filter((e) => e.source === id)
        .forEach((e) => visit(e.target));
    };
    visit(id);
  }
  function template(name, record = true) {
    if (record) snapshot();
    const input = node("input", 20, 145),
      a = node(name === "quick" ? "plan" : "candidates", 240, 145),
      alloc = node("allocate", 680, 145),
      budget = node("budget", 900, 145),
      output = node("output", 1120, 145);
    if (name === "quick") {
      budget.x = 460;
      output.x = 680;
      graph = {
        nodes: [input, a, budget, output],
        edges: [
          edge(input, a, "context"),
          edge(a, budget, "itinerary"),
          edge(a, output, "itinerary"),
          edge(budget, output, "budget"),
        ],
      };
    } else if (name === "branches") {
      const b = node("candidates", 240, 340, "街巷与茶歇"),
        merge = node("merge", 460, 220);
      a.y = 50;
      a.title = "湖景与自然";
      a.prompt = "优先湖景和轻松散步";
      b.prompt = "优先茶歇与街巷";
      graph = {
        nodes: [input, a, b, merge, alloc, budget, output],
        edges: [
          edge(input, a, "context"),
          edge(input, b, "context"),
          edge(a, merge, "candidates"),
          edge(b, merge, "candidates"),
          edge(merge, alloc, "candidates"),
          edge(alloc, budget, "itinerary"),
          edge(alloc, output, "itinerary"),
          edge(budget, output, "budget"),
        ],
      };
    } else {
      a.x = 240;
      alloc.x = 460;
      budget.x = 680;
      output.x = 900;
      graph = {
        nodes: [input, a, alloc, budget, output],
        edges: [
          edge(input, a, "context"),
          edge(a, alloc, "candidates"),
          edge(alloc, budget, "itinerary"),
          edge(alloc, output, "itinerary"),
          edge(budget, output, "budget"),
        ],
      };
    }
    selectedNode = input.id;
    pending = null;
    renderGraph();
    fitGraph();
  }
  function transform() {
    $("#graph-stage").style.transform =
      `translate(${viewport.x}px,${viewport.y}px) scale(${viewport.zoom})`;
    $("#graph-zoom").textContent = `${Math.round(viewport.zoom * 100)}%`;
  }
  function fitGraph() {
    if (!graph.nodes.length) return;
    const box = $("#graph-viewport").getBoundingClientRect();
    if (!box.width) return;
    const minX = Math.min(...graph.nodes.map((n) => n.x)),
      minY = Math.min(...graph.nodes.map((n) => n.y));
    const width = Math.max(...graph.nodes.map((n) => n.x)) + 180 - minX,
      height = Math.max(...graph.nodes.map((n) => n.y)) + 180 - minY;
    const zoom = Math.max(
      0.2,
      Math.min(1, (box.width - 50) / width, (box.height - 50) / height),
    );
    viewport = {
      zoom,
      x: (box.width - width * zoom) / 2 - minX * zoom,
      y: (box.height - height * zoom) / 2 - minY * zoom,
    };
    transform();
    wires();
  }
  function wires() {
    if (!$("#graph-viewport").clientWidth) return;
    const frame = $("#graph-viewport").getBoundingClientRect();
    $("#graph-wires").innerHTML = graph.edges
      .map((e) => {
        const from = $(
            `[data-output-node="${e.source}"]`,
          ).getBoundingClientRect(),
          to = $(
            `[data-input-node="${e.target}"][data-port="${e.port}"]`,
          ).getBoundingClientRect();
        const x = (from.right - frame.left - viewport.x) / viewport.zoom - 8,
          y =
            (from.top + from.height / 2 - frame.top - viewport.y) /
            viewport.zoom,
          tx = (to.left - frame.left - viewport.x) / viewport.zoom + 8,
          ty =
            (to.top + to.height / 2 - frame.top - viewport.y) / viewport.zoom,
          bend = Math.max(60, Math.abs(tx - x) * 0.45);
        return `<path d="M${x} ${y} C${x + bend} ${y},${tx - bend} ${ty},${tx} ${ty}"/>`;
      })
      .join("");
  }
  function renderGraph() {
    const active = document.activeElement;
    let restoreFocus = null;
    for (const attribute of [
      "data-select-node",
      "data-output-node",
      "data-input-node",
    ]) {
      if (active.hasAttribute(attribute)) {
        restoreFocus = `[${attribute}="${active.getAttribute(attribute)}"]${active.dataset.port ? `[data-port="${active.dataset.port}"]` : ""}`;
      }
    }
    if (active.matches('#node-form button[type="submit"]'))
      restoreFocus = '#node-form button[type="submit"]';
    $("#graph-nodes").innerHTML = graph.nodes
      .map(
        (n) =>
          `<article class="graph-node ${selectedNode === n.id ? "selected" : ""}" data-node="${n.id}" style="left:${n.x}px;top:${n.y}px"><button class="node-title" data-select-node="${n.id}" aria-pressed="${selectedNode === n.id}">${esc(n.title)}</button><p class="node-state">${n.dirty ? (n.artifact ? "上游或指令已变更" : "待生成") : "产物已就绪 · 模拟"}</p><div class="node-ports"><div class="node-inputs">${kinds[n.kind].inputs.map(([port, type, title]) => `<button class="port" data-input-node="${n.id}" data-port="${port}" data-type="${type}" aria-label="${esc(n.title)}输入：${title}">${title}</button>`).join("")}</div>${kinds[n.kind].out ? `<button class="port out-port" data-output-node="${n.id}" data-type="${kinds[n.kind].out}" aria-pressed="${pending === n.id}" aria-label="${esc(n.title)}输出：${portNames[kinds[n.kind].out]}">${portNames[kinds[n.kind].out]}</button>` : ""}</div></article>`,
      )
      .join("");
    $('[data-action="graph-undo"]').disabled = !history.length || graphBusy;
    $('[data-action="cancel-wire"]').hidden = !pending;
    $$(
      '[data-action="add-node"],[data-action="template"],#graph-kind,#graph-template',
    ).forEach((el) => {
      el.disabled = graphBusy;
    });
    transform();
    wires();
    inspector();
    if (restoreFocus) $(restoreFocus)?.focus({ preventScroll: true });
  }
  function inspector() {
    const n = graph.nodes.find((n) => n.id === selectedNode);
    if (!n) {
      $("#graph-inspector").innerHTML = "<p>选择节点查看参数和独立产物。</p>";
      return;
    }
    const draft = nodeDrafts.get(n.id) || n;
    $("#graph-inspector").innerHTML =
      `<div><h2>${esc(n.title)}</h2><form id="node-form"><label>节点名称<input name="title" maxlength="32" value="${esc(n.title)}" ${graphBusy ? "disabled" : ""}></label><label>${n.kind === "input" ? "旅行需求" : "这一步的指令"}<textarea name="prompt" ${graphBusy ? "disabled" : ""}>${esc(n.prompt)}</textarea></label><div class="choice-group"><button type="submit" class="primary" ${graphBusy ? "disabled" : ""}>保存参数</button><button type="button" data-action="delete-node" ${graphBusy ? "disabled" : ""}>删除节点</button></div></form><h3>输入连接</h3><div class="edge-list">${
        graph.edges
          .map((e, index) => ({ e, index }))
          .filter(({ e }) => e.target === n.id)
          .map(
            ({ e, index }) =>
              `<button data-delete-edge="${index}" ${graphBusy ? "disabled" : ""}>断开：${esc(graph.nodes.find((item) => item.id === e.source).title)} → ${esc(n.title)}</button>`,
          )
          .join("") || '<span class="muted">当前没有输入连接</span>'
      }</div></div><div><h3>独立产物</h3><p class="muted">${n.dirty ? "输入或上游已变化；旧产物不会当作新结果。" : "输入一致，可复用产物。"}</p><pre id="graph-artifact"></pre><p class="muted">本地模拟产物，只用于检查工程界面与依赖关系。</p></div>`;
    $("#node-form [name=title]").value = draft.title;
    $("#node-form [name=prompt]").value = draft.prompt;
    $("#node-form").addEventListener("input", (event) => {
      const form = event.currentTarget;
      nodeDrafts.set(n.id, {
        title: form.elements.title.value,
        prompt: form.elements.prompt.value,
      });
    });
    $("#graph-artifact").textContent =
      n.artifact || "尚未生成。运行工程后，可逐个节点检查结果。";
    $("#node-form").addEventListener("submit", (event) => {
      event.preventDefault();
      if (graphBusy) return;
      const title = event.currentTarget.elements.title.value.trim(),
        prompt = event.currentTarget.elements.prompt.value.trim();
      if (!title || !prompt) {
        $("#graph-status").textContent = "名称和指令需要有内容，输入仍保留。";
        return;
      }
      if (n.title === title && n.prompt === prompt) {
        nodeDrafts.delete(n.id);
        $("#graph-status").textContent = "参数没有变化，原产物仍可复用。";
        return;
      }
      snapshot();
      nodeDrafts.delete(n.id);
      n.title = title;
      n.prompt = prompt;
      invalidate(n.id);
      renderGraph();
      $("#graph-status").textContent =
        "参数已更新；本节点与下游等待重算，其他分支产物保留。";
    });
  }
  function connect(target, port) {
    if (!pending) {
      $("#graph-status").textContent = "先选择一个输出端口，再选择输入端口。";
      return;
    }
    const source = graph.nodes.find((n) => n.id === pending),
      dest = graph.nodes.find((n) => n.id === target),
      input = kinds[dest.kind].inputs.find((p) => p[0] === port);
    if (source.id === target || kinds[source.kind].out !== input[1]) {
      $("#graph-status").textContent =
        "无法连接：需要匹配的数据类型，且不能连回节点自身。";
      return;
    }
    const reachable = new Set();
    const visit = (id) => {
      if (reachable.has(id)) return;
      reachable.add(id);
      graph.edges
        .filter((e) => e.source === id)
        .forEach((e) => visit(e.target));
    };
    visit(target);
    if (reachable.has(source.id)) {
      $("#graph-status").textContent = "无法连接：这会产生循环依赖。";
      return;
    }
    if (
      graph.edges.some(
        (e) =>
          e.target === target &&
          e.port === port &&
          (e.source === source.id || dest.kind !== "merge"),
      )
    ) {
      $("#graph-status").textContent =
        "这个输入已有连接；请在检查区断开旧线后再接，候选合并允许多路不同输入。";
      return;
    }
    snapshot();
    graph.edges.push({ source: source.id, target, port });
    invalidate(target);
    pending = null;
    renderGraph();
    $("#graph-status").textContent = "已连接；受影响的下游等待重算。";
  }
  async function runGraph() {
    if (graphBusy) return;
    const draftNode = graph.nodes.find((n) => nodeDrafts.has(n.id));
    if (draftNode) {
      selectedNode = draftNode.id;
      renderGraph();
      $("#graph-status").textContent =
        "节点有未保存的参数，先保存再运行；切换节点时草稿已保留。";
      return;
    }
    const missing = graph.nodes.find(
      (n) =>
        n.kind !== "input" &&
        (n.kind === "output"
          ? !graph.edges.some((e) => e.target === n.id)
          : kinds[n.kind].inputs.some(
              (p) =>
                !graph.edges.some((e) => e.target === n.id && e.port === p[0]),
            )),
    );
    if (missing) {
      selectedNode = missing.id;
      renderGraph();
      $("#graph-status").textContent =
        `${missing.title}缺少输入连接；请连好再运行。`;
      return;
    }
    if (!graph.nodes.length) {
      $("#graph-status").textContent = "先添加节点或载入模板。";
      return;
    }
    graphBusy = true;
    pending = null;
    const run = $('[data-action="run"]');
    run.disabled = true;
    run.setAttribute("aria-busy", "true");
    run.textContent = "模拟运行中…";
    renderGraph();
    let completed = new Set(),
      generated = 0,
      reused = 0;
    while (completed.size < graph.nodes.length) {
      const n = graph.nodes.find(
        (n) =>
          !completed.has(n.id) &&
          graph.edges
            .filter((e) => e.target === n.id)
            .every((e) => completed.has(e.source)),
      );
      if (!n) {
        $("#graph-status").textContent = "工程包含循环，无法运行。";
        break;
      }
      if (!n.dirty && n.artifact) reused++;
      else {
        $("#graph-status").textContent = `正在模拟 ${n.title}…`;
        await new Promise((resolve) => setTimeout(resolve, 180));
        const upstream = graph.edges
          .filter((e) => e.target === n.id)
          .map((e) => {
            const source = graph.nodes.find((item) => item.id === e.source);
            return `${source.title}（产物 ${source.generation || 0}）`;
          })
          .join("、");
        n.generation = (n.generation || 0) + 1;
        const samples = {
          input: n.prompt,
          candidates: "景点样例：柳浪闻莺、湖边茶歇、街巷散步。",
          merge: "已合并多路候选，保留来源与不同理由。",
          allocate: "分日样例：第 1 天街巷，第 2 天西湖；留出午休。",
          plan: "日程样例：上午湖边散步，中午休息，下午喝茶。",
          budget: "预算样例：饮食 160、住宿 680、交通 240、门票 200。",
          output: "成稿样例：行程与预算已组合，可检查来源节点。",
        };
        n.artifact = `${samples[n.kind]}\n\n产物修订：${n.generation}\n本步指令：${n.prompt}\n上游：${upstream || "无，原始输入"}\n\n仅为本地模拟，未请求模型、地图或修改真实路书。`;
        n.dirty = false;
        generated++;
      }
      completed.add(n.id);
      renderGraph();
    }
    graphBusy = false;
    run.disabled = false;
    run.removeAttribute("aria-busy");
    run.textContent = "模拟运行工程";
    renderGraph();
    $("#graph-status").textContent =
      `模拟完成：重算 ${generated} 个节点，复用 ${reused} 个产物。点击节点检查独立结果。`;
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.inspector) {
      root.dataset.inspector = button.dataset.inspector;
      view();
      requestAnimationFrame(fitGraph);
    }
    if (button.dataset.selectNode) {
      if (drag?.moved) return;
      selectedNode = button.dataset.selectNode;
      renderGraph();
    }
    if (button.dataset.outputNode && !graphBusy) {
      pending =
        pending === button.dataset.outputNode
          ? null
          : button.dataset.outputNode;
      renderGraph();
      $("#graph-status").textContent = pending
        ? "已选择输出；点击匹配的输入完成连线。"
        : "已取消连线。";
    }
    if (button.dataset.inputNode && !graphBusy)
      connect(button.dataset.inputNode, button.dataset.port);
    if (button.dataset.deleteEdge !== undefined && !graphBusy) {
      snapshot();
      const [removed] = graph.edges.splice(
        Number(button.dataset.deleteEdge),
        1,
      );
      invalidate(removed.target);
      renderGraph();
      $("#graph-status").textContent =
        "连接已断开，可连接到其他节点；撤销编辑可恢复。";
    }
    const action = button.dataset.action;
    if (action === "template" && !graphBusy) {
      template($("#graph-template").value);
      $("#graph-status").textContent =
        "模板已载入；可自由改线、添加节点和修改指令，旧工程可撤销恢复。";
    }
    if (action === "add-node" && !graphBusy) {
      snapshot();
      const n = node(
        $("#graph-kind").value,
        180 + (graph.nodes.length % 4) * 220,
        380,
      );
      graph.nodes.push(n);
      selectedNode = n.id;
      renderGraph();
      fitGraph();
      $("#graph-status").textContent =
        "节点已加入，先连接输入再运行；模板没有限制节点结构。";
    }
    if (action === "delete-node" && !graphBusy) {
      snapshot();
      graph.edges
        .filter((e) => e.source === selectedNode)
        .forEach((e) => invalidate(e.target));
      graph.nodes = graph.nodes.filter((n) => n.id !== selectedNode);
      graph.edges = graph.edges.filter(
        (e) => e.source !== selectedNode && e.target !== selectedNode,
      );
      selectedNode = graph.nodes[0]?.id ?? null;
      pending = null;
      renderGraph();
      $("#graph-status").textContent = "节点及相关连接已移除，可以撤销编辑。";
    }
    if (action === "graph-undo" && !graphBusy && history.length) {
      const old = history.pop();
      graph = old.graph;
      selectedNode = old.selectedNode;
      pending = null;
      renderGraph();
      fitGraph();
      $("#graph-status").textContent = "已恢复上一次工程编辑。";
    }
    if (action === "run") void runGraph();
    if (action === "cancel-wire") {
      pending = null;
      renderGraph();
      $("#graph-status").textContent = "已取消连线。";
    }
    if (action === "fit") fitGraph();
    if (action === "zoom-in" || action === "zoom-out") {
      const old = viewport.zoom,
        next = Math.min(
          1.5,
          Math.max(0.2, old * (action === "zoom-in" ? 1.2 : 1 / 1.2)),
        ),
        box = $("#graph-viewport").getBoundingClientRect();
      viewport.x = box.width / 2 - ((box.width / 2 - viewport.x) * next) / old;
      viewport.y =
        box.height / 2 - ((box.height / 2 - viewport.y) * next) / old;
      viewport.zoom = next;
      transform();
    }
  });
  const canvas = $("#graph-viewport");
  canvas.addEventListener("pointerdown", (event) => {
    const title = event.target.closest("[data-select-node]");
    if (event.target.closest("button") && !title) return;
    if (title && graphBusy) return;
    const n = title
      ? graph.nodes.find((n) => n.id === title.dataset.selectNode)
      : null;
    drag = {
      id: n?.id ?? null,
      clientX: event.clientX,
      clientY: event.clientY,
      x: n?.x ?? viewport.x,
      y: n?.y ?? viewport.y,
      moved: false,
    };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const dx = event.clientX - drag.clientX,
      dy = event.clientY - drag.clientY;
    if (Math.abs(dx) + Math.abs(dy) < 3) return;
    if (drag.id && !drag.moved) {
      snapshot();
      $('[data-action="graph-undo"]').disabled = false;
    }
    drag.moved = true;
    if (drag.id) {
      const n = graph.nodes.find((n) => n.id === drag.id);
      n.x = Math.max(0, drag.x + dx / viewport.zoom);
      n.y = Math.max(0, drag.y + dy / viewport.zoom);
      const el = $(`[data-node="${n.id}"]`);
      el.style.left = `${n.x}px`;
      el.style.top = `${n.y}px`;
      wires();
    } else {
      viewport.x = drag.x + dx;
      viewport.y = drag.y + dy;
      transform();
    }
  });
  canvas.addEventListener("pointerup", () => {
    if (!drag) return;
    if (drag.id) {
      selectedNode = drag.id;
      inspector();
      $$(".graph-node").forEach((el) =>
        el.classList.toggle("selected", el.dataset.node === selectedNode),
      );
      $$("[data-select-node]").forEach((el) =>
        el.setAttribute(
          "aria-pressed",
          String(el.dataset.selectNode === selectedNode),
        ),
      );
    }
    setTimeout(() => {
      drag = null;
    }, 0);
  });
  canvas.addEventListener("pointercancel", () => {
    drag = null;
  });
  canvas.addEventListener("keydown", (event) => {
    const button = event.target.closest("[data-select-node]");
    if (
      !button ||
      graphBusy ||
      !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
    )
      return;
    event.preventDefault();
    snapshot();
    const n = graph.nodes.find((n) => n.id === button.dataset.selectNode);
    n.x = Math.max(0, n.x + ({ ArrowLeft: -8, ArrowRight: 8 }[event.key] || 0));
    n.y = Math.max(0, n.y + ({ ArrowUp: -8, ArrowDown: 8 }[event.key] || 0));
    renderGraph();
    $(`[data-select-node="${n.id}"]`).focus();
    $("#graph-status").textContent = `${n.title}已移动；输入与产物未改动。`;
  });
  responsive.addEventListener("change", () => {
    view();
    if (state.workspace === "graph") fitGraph();
  });
  new ResizeObserver(() => {
    if (state.workspace === "graph") fitGraph();
  }).observe(canvas);
  renderRoute();
  renderBudget();
  template("standard", false);
  icons();
  view();
})();
