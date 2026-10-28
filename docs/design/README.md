# 设计材料与历史样稿

正式应用已完成本轮前端升级。当前规范以 [SYSTEM.md](SYSTEM.md) 和 [DESIGN_BRIEF.md](../../DESIGN_BRIEF.md) 为准；新对话从 [HANDOFF.md](../../HANDOFF.md) 接续，不重新进行四轮选型。

- 正式工作区、撤销、工程、费用、入口配置、回顾、比较和主题的记录见 [升级计划](../../FRONTEND_UPGRADE_PLAN.md)。
- 最新主题与全站收口见 [交付记录](../frontend-themes.md)／[原图](themes/README.md)。
- 普通用户主流程最新修复见 [走查记录](../ordinary-flow.md)／[原图](../ordinary-flow/README.md)。
- 下文为 FE-2 历史选型档案，18487 为静态样稿地址；演示数据不代表正式业务或待重新决策事项。


## FE-2 历史背景

依据 [设计约定](../../DESIGN_BRIEF.md)。已确认三套配色作为设置中的可选主题、默认 A，电脑松弛、手机紧凑；电脑默认 D3 对话主导，手机默认 M1 今日路线。第三轮已确认 C1 轻圆角短反馈＋P1 小图辅助；第四轮已确认 R1 直接更新可撤销＋W1 画布右侧检查。选型结束，进入 FE-3；正式共享规范见 [SYSTEM.md](SYSTEM.md)，FE-3 第一批覆盖与检查见 [共享基础交付记录](../frontend-foundation.md)。

## 历史：第四轮完整任务流程

打开[第四轮交互预览](http://127.0.0.1:18487/task-flows.html)，沿用 A／D3／M1／C1／P1。可以连续完成对话调整、原位记账、查看路书与执行下一站；高级工程可载入三种模板、拖动增删节点、按类型连接、修改参数、检查独立产物，并观察下游重算与复用。

本轮比较 **R1 直接更新可撤销／R2 先核对再采用**，以及 **W1 画布右侧检查／W2 画布底部检查**。用户已确认 R1＋W1；其他选项保留为比较记录。实付金额不随撤销规划被删除；失败保留草稿，预算余量和超额在原位置显示。详见[任务路径、边界与运行说明](TASK_FLOWS.md)。

运行 `PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser node frontend/scripts/check-task-preview.mjs`，核对三种宽度、三主题与两种工程检查区，以及四条任务的成功／失败和草稿保留，生成[任务检查结果](task-check.json)和[任务截图](tasks/)。全部使用内存虚构数据与模拟生成，不请求后端、地图或模型；现有正式工程继续使用原有实现。

## 第三轮：组件手感与图文比例

打开[第三轮交互预览](http://127.0.0.1:18487/layout-options.html?round=components)，在固定 D3／M1 和默认 A 主题下比较：

| 组别 | 选项                         | 推荐与取舍                     |
| ---- | ---------------------------- | ------------------------------ |
| 手感 | C1 轻圆角短反馈：8px / 160ms | 已选定，轻快克制，适合频繁修改 |
| 手感 | C2 方正干脆：3px / 120ms     | 边界更明确、工具感更强         |
| 手感 | C3 柔和回弹：14px / 260ms    | 面板更柔和，按压有轻回弹       |
| 图片 | P1 小图辅助                  | 已选定，辅助辨认而不打断路线   |
| 图片 | P2 景点大图                  | 更有目的地氛围，滚动更多       |
| 图片 | P3 按需看图                  | 文字优先，点开才看图           |

2026-10-27 用户已认可 C1＋P1，附带的图片尺寸／比例顾虑已用固定容器和完整图查看规则处理。其他选项仍可切换比较，不改变已记录的默认选择。预览上方可试悬停、按住、等待、成功与失败；失败保留原内容。工作区仍可对话调整、记账、选择站点；设置保留 A/B/C 与两端密度。手机优先路线，正文和触屏目标保留可读可点下限。

只对含参考照片的景点比较图片比例；其他项目不凭空配图。照片注明“非该站实拍”，展开可看完整图与署名；[照片来源与许可](assets/SOURCES.md)随文件保存。图片在本地提供，加载失败则移除占位回到文字路线。地图点位的反馈不改变位置或缩放。

运行 `PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser node frontend/scripts/check-component-preview.mjs` 生成[第三轮检查结果](component-check.json)与[截图目录](components/)。检查 1440／390／320 宽度、三种手感、三种图片比例、三主题共 81 组合，另核对异步反馈、键盘、减少动画、图片对话框、路线上下文与缺图回落。

照片比例核对：运行 `PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser node frontend/scripts/check-component-preview.mjs --photos-only`，生成[图片检查结果](photo-check.json)。以横图、竖图、超宽／超长图、方图和低分辨率图，在 1440／390／320 宽度核对 18 组合；固定 64 × 56px 小图等比裁切，路线行高与下一站位置不因原图变化。展开完整图片使用受视口高度限制的容器等比容纳，缺图回落文字。此项只验证版式与查看行为，不把低清照片变清晰；正式接入时优先合适的缩略图，无法辨认的图片可以不展示。

## 第二轮：布局比较记录

打开 [布局交互预览](http://127.0.0.1:18487/layout-options.html)，或查看 [本地源文件](layout-options.html)。本轮使用同一 A 主题，通过不同面积、位置和默认入口比较工作重心；设置内仍可试切换 B、C。电脑默认松弛、手机默认紧凑，正文与触屏目标保留可读、可点的下限。

| 选项            | 工作重心                                             | 原始截图                                 |
| --------------- | ---------------------------------------------------- | ---------------------------------------- |
| D1 电脑行程主导 | 连续行程为主，旁边对照地图与对话；保留比较           | [查看](layouts/desktop-journey.png)      |
| D2 电脑地图主导 | 大地图为主，地点选择与左侧路线联动                   | [查看](layouts/desktop-map.png)          |
| D3 电脑对话主导 | 用户已选定；对话占主要空间，行程与地图在旁边跟随更新 | [查看](layouts/desktop-conversation.png) |
| M1 手机今日路线 | 用户已选定；打开先看下一站与今天怎么走               | [查看](layouts/mobile-today.png)         |
| M2 手机地图导航 | 打开先看地图，再衔接下一站与导航                     | [查看](layouts/mobile-map.png)           |

选择的是默认工作重心，其他能力仍可直接进入。样稿中地图标号与路线选择相互联动，可以预览并应用下午调整、标记完成和记消费；操作只更新虚构样稿，不调用后端、模型、地图或定位，不是生产数据写入。未生成行程时仍应以对话收集需求，不能用生成后布局遮住初始规划入口。

默认进入已同步为 D3／M1；此入口保留第二轮布局比较，第三轮通过独立查询参数加载增强层，不改变历史比较方式。

运行 `PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser node frontend/scripts/check-layout-preview.mjs` 可复核 13 个宽度／布局组合与三主题，共 39 种组合，生成五张截图与 [布局检查结果](layout-check.json)。检查包括真实列宽／模块顺序变化、联动选择、调整与记账、默认主题和两端密度、横向溢出与减少动画，不将单纯换色冒充布局比较。

## 历史：第一轮配色与密度比较

样稿采用同一份虚构行程和相同主要结构，先比较视觉气质与信息密度。导航位置、聊天／路线／地图关系在下一轮单独比较，不能从这版样稿推断布局已获批准。字体使用系统字体栈，具体字体与跨设备一致性随正式规范固定。

| 方向           | 主要气质                       | 电脑                               | 手机                              |
| -------------- | ------------------------------ | ---------------------------------- | --------------------------------- |
| A 纸感旅行册   | 暖纸色、墨绿、偏阅读的节奏     | [截图](previews/desktop-paper.png) | [截图](previews/mobile-paper.png) |
| B 清爽城市导览 | 冷白、深蓝、更清晰利落的工具感 | [截图](previews/desktop-guide.png) | [截图](previews/mobile-guide.png) |
| C 深色路线工具 | 深灰绿、浅文字、低饱和强调色   | [截图](previews/desktop-night.png) | [截图](previews/mobile-night.png) |

第一轮截图保留当时均衡密度以便回看；当前默认值已按用户选择更新在第二轮中，不再采用双端都均衡的推荐。

## 试用样稿

预览统一使用端口 **18487**。在 Linux 本机浏览器中，从仓库根目录运行：

```sh
python3 -m http.server 18487 --bind 127.0.0.1 --directory docs/design
```

Windows 浏览器访问 WSL 项目时，优先在 **Windows PowerShell** 中启动，当前目录同样设为仓库根目录（可通过 `\\wsl.localhost\发行版\项目目录` 进入）。下面使用 Codex 随附的 Windows Python，避免依赖 WSL 的 localhost 转发：

```powershell
$previewPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $previewPython -m http.server 18487 --bind 127.0.0.1 --directory (Join-Path (Get-Location) 'docs/design')
```

预览保持前台运行，关闭终端或按 Ctrl+C 会停止服务；只提供 `docs/design` 静态目录。在当前开发环境中，已由 Windows Python 启动并验证页面、脚本与照片可访问。

2026-10-27 排障记录：旧端口 4387 落在 Windows TCP 保留范围 **4369–4468**，WSL 内部返回 200 不代表 Windows 浏览器可访问。已改用 18487，Windows 原生客户端实测三个资源均返回 200。若其他电脑仍无法打开，应在 Windows 执行 `netsh interface ipv4 show excludedportrange protocol=tcp` 检查保留端口，再用 `curl.exe --noproxy "*" -I "http://127.0.0.1:18487/layout-options.html?round=components"` 验证；不要只检查 WSL，也无需修改系统保留范围或防火墙。

在本机打开 [视觉方向预览](http://127.0.0.1:18487/visual-directions.html)。可切换风格、密度与电脑／手机尺寸，试按保存、调整、记账、日期和路书按钮。所有数据与结果都只在样稿内演示，不读取环境文件、不请求模型、地图或项目后端，也不保存最终设计投票；用户在对话中的选择才进入设计记录。

图标从项目现有 `lucide-react` 渲染并保存在 `icons.js`，无需外部请求，[许可文本](LUCIDE-LICENSE.txt)随样稿保留。湖面与路线只是版式示意，不是可导航的实际地图。界面没有 emoji，控件的悬停、按下、焦点及等待状态可实际检查；减少动画设置下保留静态反馈。

## 验证与边界

```sh
cd frontend
PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser node scripts/check-design-preview.mjs
```

浏览器脚本使用临时本机端口并在完成后关闭，检查三个方向、三种密度和 1440／390／320 宽度，共 27 种组合；核对实际悬停与按下、异步演示反馈、修改与记账表单、触屏高度和减少动画，生成六张原始截图与 [检查结果](preview-check.json)。它不等于正式应用改造验收，本轮没有修改产品页面与业务数据。
