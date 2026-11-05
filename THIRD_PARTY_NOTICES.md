# 第三方组件与素材声明

TravelFlow AI 的源码以 [MIT License](./LICENSE) 发布。第三方依赖、字体图标与示例图片不因本项目采用 MIT 而改变其原有许可；各组件以自身包内许可文件为准。

本文件列出发布时的主要运行时组件。完整依赖与精确版本见 `backend/uv.lock`、`frontend/pnpm-lock.yaml`。

## 后端运行时依赖

| 许可族 | 代表组件 |
| :--- | :--- |
| MIT | FastAPI、Pydantic、LangGraph、LangChain、Jinja2、aiosqlite、python-dotenv、ddgs、httpx 生态中的 MIT 组件 |
| BSD（2/3 条款） | Starlette、uvicorn、uvloop、jinja2 的部分依赖、httpcore、idna |
| Apache-2.0 | OpenAI Python SDK、opentelemetry-api、tenacity、pytest 相关开发组件 |
| MPL-2.0 | certifi、orjson、pathspec 等（文件级弱 copyleft，不改变本项目许可） |
| 其他宽松许可 | pillow（MIT-CMU）、typing_extensions（PSF-2.0）等 |

开发依赖（pytest、mypy、ruff 等）随源码提供，不进入运行时产物。

## 前端运行时依赖

| 许可族 | 代表组件 |
| :--- | :--- |
| MIT | Next.js、React、React DOM、react-markdown、zustand、clsx、tailwind-merge、Tailwind CSS |
| ISC | Lucide 图标（`lucide-react`），完整许可见 [`docs/design/LUCIDE-LICENSE.txt`](./docs/design/LUCIDE-LICENSE.txt) |
| Apache-2.0 / BSD / MPL-2.0 | 构建链与间接依赖 |
| LGPL-3.0-or-later | `@img/sharp-libvips-linux-x64`（可选原生图像库，用于服务端图片处理） |

`@img/sharp-libvips` 是本机可选原生依赖，本仓库不复制其源码，也不修改它。若重新分发包含该库的安装目录或构建产物，请保留其许可文本并遵守 LGPL 的相应义务；不需要把本项目代码改为 LGPL。前端生产构建会打包第三方 JavaScript，重新分发构建产物时应保留相应依赖的许可声明。

## 独立移动仓库

Capacitor候选及Android原型已迁入 [TravelFlow-Mobile](https://github.com/wuqiuwangtian/TravelFlow-Mobile)，依赖与许可由该仓库维护。本仓库不再包含Android候选依赖；跨端只读账目样例由 `contracts/` 维护，不引入额外运行时依赖。

## 示例图片

`docs/design/assets/west-lake-reference.jpg`

- 作品：[West Lake, Hangzhou 2025.jpg](https://commons.wikimedia.org/wiki/File:West_Lake,_Hangzhou_2025.jpg)
- 作者：Takashishin
- 许可：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- 用途：界面图文比例样稿参考，不是任何站点的实拍；下载后未编辑，仅在样稿中以 CSS 裁切展示。
- 该图片不适用 MIT License；再分发时需保留作者署名、来源链接与许可说明。

## 说明

- 本项目不对第三方服务的可用性、价格、班次或可预订性作出承诺。
- 若你分发修改版或构建产物，请同时保留 MIT 许可声明与本文件中仍适用的第三方声明。
- 本文件用于信息说明，不构成法律意见；如涉及商业分发，请自行复核各依赖的实际许可文本。
