(() => {
  if (new URLSearchParams(location.search).get("round") !== "components")
    return;
  const root = document.documentElement;
  root.dataset.round = "components";
  root.dataset.component = "quiet";
  root.dataset.photos = "small";
  root.dataset.layout = "conversation";
  root.dataset.mobileLayout = "today";
  document.title = "TravelFlow · 第三轮组件与图文比较";
  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = "./component-options.css";
  style.onload = () => {
    root.dataset.componentReady = "true";
  };
  document.head.append(style);
  const bar = document.querySelector(".reviewbar");
  bar.innerHTML = `
    <div class="review-title"><strong>第三轮：手感与图片</strong><span>固定 D3 / M1 · 本地演示，不写入旅行</span></div>
    <div class="review-groups">
      <div class="choice-group" aria-label="组件手感"><span>手感</span><button data-component-choice="quiet">C1 轻圆角短反馈</button><button data-component-choice="precise">C2 方正干脆</button><button data-component-choice="soft">C3 柔和回弹</button></div>
      <div class="choice-group" aria-label="图片比例"><span>图片</span><button data-photo-choice="small">P1 小图辅助</button><button data-photo-choice="large">P2 景点大图</button><button data-photo-choice="on-demand">P3 按需看图</button></div>
    </div>
    <div class="component-tools"><div class="choice-group" aria-label="预览设备"><button data-device-choice="desktop">电脑 D3</button><button data-device-choice="mobile">手机 M1</button></div><p id="preview-description"></p></div>
    <p id="component-description"></p>
    <div class="feedback-lab" aria-label="按钮反馈试用"><span>试试手感</span><button class="primary" data-lab="success">试一次保存</button><button data-lab="failure">试一次失败</button><button disabled>暂无可撤销内容</button><span id="lab-status" role="status" aria-live="polite">悬停、按住、松开，或用 Tab / Enter 操作。</span></div>
    <p class="reference-note">路线中配有一张西湖参考照片，用于比较图文比例，非该站实拍。点图可查看署名与来源。主题仍可在工作区右上角设置中切换。</p>`;
  const dialog = document.createElement("dialog");
  dialog.id = "photo-dialog";
  dialog.setAttribute("aria-labelledby", "photo-title");
  dialog.innerHTML = `<div class="dialog-heading"><h2 id="photo-title">西湖参考照片</h2><button data-close="photo-dialog" aria-label="关闭参考照片">关闭</button></div><img src="./assets/west-lake-reference.jpg" alt="西湖湖面与远处山景，仅作比例演示，非柳浪闻莺该站实拍"><p>仅用于图文比例比较，非该站实拍。</p><p>摄影：Takashishin · <a href="https://commons.wikimedia.org/wiki/File:West_Lake,_Hangzhou_2025.jpg" target="_blank" rel="noreferrer">原始图片与作者</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">CC BY 4.0</a>。下载预览未编辑；版面展示有裁切。</p>`;
  document.body.append(dialog);
  const descriptions = {
    quiet: "C1：8px 轻圆角、细分隔线、160ms 短反馈。适合频繁对话与修改。",
    precise: "C2：3px 小圆角、明确边界、120ms 按压。工具感更强。",
    soft: "C3：14px 圆角、柔和面板、260ms 轻回弹。氛围更放松。",
    small: "P1：小图辅助辨认，保留连续路线和文字。",
    large: "P2：较大照片展示目的地，路线需要更多滚动。",
    "on-demand": "P3：文字优先，需要时打开参考图。",
  };
  function update() {
    for (const [selector, value] of [
      ["component", root.dataset.component],
      ["photo", root.dataset.photos],
      ["device", root.dataset.preview],
    ]) {
      document
        .querySelectorAll(`[data-${selector}-choice]`)
        .forEach((button) =>
          button.setAttribute(
            "aria-pressed",
            String(button.dataset[`${selector}Choice`] === value),
          ),
        );
    }
    document.querySelector("#component-description").textContent =
      `${descriptions[root.dataset.component]} ${descriptions[root.dataset.photos]}`;
  }
  function photos() {
    document.querySelectorAll("[data-stop-row=lake]").forEach((row) => {
      row.querySelector(".stop-visual")?.remove();
      row.classList.remove("has-photo");
      if (row.querySelector(".stop-title").textContent !== "柳浪闻莺") return;
      row.classList.add("has-photo");
      const figure = document.createElement("figure");
      figure.className = "stop-visual";
      figure.innerHTML = `<button data-reference-photo aria-label="查看西湖参考照片，非该站实拍"><img src="./assets/west-lake-reference.jpg" alt="西湖参考照片"><span>查看图</span></button><figcaption>西湖参考图 · 非该站实拍</figcaption>`;
      figure.querySelector("img").addEventListener("error", () => {
        figure.remove();
        row.classList.remove("has-photo");
      });
      row.querySelector(".stop-select").after(figure);
    });
  }
  document.addEventListener("travelflow:routes-rendered", photos);
  document.addEventListener("travelflow:view-changed", update);
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.componentChoice)
      root.dataset.component = button.dataset.componentChoice;
    if (button.dataset.photoChoice)
      root.dataset.photos = button.dataset.photoChoice;
    if (button.dataset.deviceChoice)
      document.dispatchEvent(
        new CustomEvent("travelflow:preview-device", {
          detail: button.dataset.deviceChoice,
        }),
      );
    if (button.hasAttribute("data-reference-photo")) dialog.showModal();
    if (button.dataset.lab) {
      const buttons = [...document.querySelectorAll("[data-lab]")];
      const status = document.querySelector("#lab-status");
      const original = button.textContent;
      buttons.forEach((item) => {
        item.disabled = true;
      });
      button.setAttribute("aria-busy", "true");
      button.textContent = "处理中…";
      status.textContent = "本地等待演示中…";
      status.dataset.result = "pending";
      setTimeout(() => {
        buttons.forEach((item) => {
          item.disabled = false;
        });
        button.removeAttribute("aria-busy");
        button.textContent = original;
        status.dataset.result = button.dataset.lab;
        status.textContent =
          button.dataset.lab === "success"
            ? "已完成演示；正式行程未改动。"
            : "暂未完成；原内容已保留，可再次尝试（演示）。";
      }, 800);
    }
    update();
  });
  photos();
  update();
})();
