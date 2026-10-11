# Prompt 设计（阶段②规划附件）

> **性质**：`docs/02-upgrade-plan.md` 的配套设计材料，**待用户验证**。
> **定位**：阶段①拆解结论认定「Prompt 资产 + 检索链路」是本项目最值得继承的部分，本文档把这份资产**按实测结论重写**。

---

## 1. 设计依据：从旧 Prompt 的实测结果出发

阶段①的三条实测结论（见 `01-legacy-analysis.md` 7.3 / 7.6 / 7.7）决定了本次改写的方向：

| 实测结论 | 对 Prompt 设计的指示 |
| :--- | :--- |
| 「信息收集 → 检索 → 生成」主干可用，产出结构完整、避雷真实 | ✅ **保留**：结构化要求、避雷高亮、费用双档表格 |
| 引用 URL 靠 LLM 复写，程序零校验 | ❌ **改掉**：让程序持有 URL，LLM 只写编号 |
| 检索 query 硬编码 `2024`，产出生成当天即过时 | ❌ **改掉**：`{year}` 动态注入 |
| 同一批次路书对同一事实给出不同数字（索道 20/30） | ⚠️ **新增**：强制"有据可依 + 标引用"，无据则写"建议出行前核实" |
| `data["status"]` 下标取值导致 KeyError 500 | ❌ **改掉**：全部改为结构化输出 |

---

## 2. 三道防幻觉防线（总体架构）

```
第一道 · 检索层 ─────────────────────────────────────────
   动态年份 query  →  结构化 SearchResult（url/title/content/published_at）
   程序持有真实 URL 与抓取时间

第二道 · Prompt 层 ───────────────────────────────────────
   硬性要求：所有事实必须标引用编号；无据必须写"建议核实"；
   ★ 禁止 LLM 输出任何 URL

第三道 · 程序层 ──────────────────────────────────────────
   把 LLM 写的 [1][2] 替换为 Citation 中的真实 URL
   编号不存在 → 丢弃并告警（可发现 LLM 编造引用）
```

> **关键设计**：LLM 从头到尾**看不到也不需要输出 URL**。它只做两件事：写内容、标编号。这样「引用真实性」由程序保证，与 LLM 能力无关。

---

## 3. 节点一：collector（需求收集）

### 3.1 结构化输出契约

```python
class Requirements(BaseModel):
    destination: str | None = Field(None, description="目的地城市")
    origin: str | None = Field(None, description="出发地城市")
    duration_days: int | None = Field(None, description="游玩天数", ge=1, le=30)
    budget: float | None = Field(None, description="总预算（人民币）", ge=0)
    travelers: int | None = Field(None, description="出行人数", ge=1, le=50)
    preferences: list[str] = Field(default_factory=list, description="偏好标签")

class CollectResult(BaseModel):
    extracted: Requirements          # 本轮新提取到的字段
    missing_fields: list[str]        # 仍缺失的必填字段
    reply: str                       # 给用户的自然语言回复
    ready: bool                      # 必填是否齐全，可进入检索
```

### 3.2 System Prompt

```
你是专业且亲切的旅行规划顾问，当前处于「需求收集」阶段。

【已收集到的信息】
{collected_json}

【必填字段】destination（目的地）、origin（出发地）、duration_days（天数）、budget（预算）
【选填字段】travelers（人数）、preferences（偏好，如亲子/美食/徒步/摄影）

【你的任务】
1. 阅读用户最新一条消息，提取其中**明确表达**的字段值。
2. 对比已有信息，列出仍然缺失的必填字段。
3. 生成一段自然、简洁的回复（中文，不超过 2 句话）：
   - 若仍有缺失：只针对缺失字段提问，语气友好，一次问完，不要挤牙膏。
   - 若已齐全：简短确认，并说明即将开始检索真实信息。
4. 设置 ready = true 仅当四个必填字段全部就位。

【硬性约束】
- 只提取用户明确说出的信息，**严禁臆测或填充**（例如用户没说预算，不要默认 2000）。
- 不要重复询问已经收集到的字段。
- 若用户表示"随便/你定/无所谓"，对该字段给出一个合理建议值并**明确告知用户你替他定的值**，然后将其视为已收集。
- 本阶段**不要输出任何具体行程、景点或价格建议**。
- 若用户消息与旅行无关，友好地把话题引导回需求收集。
```

### 3.3 相比旧版的改进

| 旧版问题 | 新版对策 |
| :--- | :--- |
| 手工 `json.loads` + `strip("```json")` 脆弱解析 | 结构化输出，框架保证 schema |
| `data["reply"]` / `data["status"]` 下标取值 → KeyError | Pydantic 强类型，缺失即校验失败并可重试 |
| 无字段类型约束（天数可被填成"三天左右"） | `duration_days: int`，类型由 schema 强制 |
| 缺少 travelers / preferences | 新增字段，为生成更贴合的路书提供依据 |

---

## 4. 节点二：researcher（检索）

本节点**不使用 LLM**，由程序构造 query 并并行调用检索 Provider。

### 4.1 query 模板（★ 动态年份，根除过时问题）

```python
from datetime import datetime

YEAR = datetime.now().year          # ← 禁止写字面量年份

QUERY_TEMPLATES = {
    "transport":   f"{{origin}} 到 {{destination}} 火车票 高铁 机票 价格 时刻表 {YEAR}",
    "attractions": f"{{destination}} 必去景点 门票价格 开放时间 {YEAR}",
    "reviews":     f"{{destination}} 旅游 避雷 坑 真实体验 {YEAR}",
}
```

### 4.2 关于旧版 `site:` 限制的调整

旧版把 `site:12306.cn OR site:ctrip.com` 写死在 query 里。调查后建议**改为 Provider 的域名过滤参数**：

| 方式 | 优点 | 缺点 |
| :--- | :--- | :--- |
| query 内嵌 `site:` | 通用 | 部分搜索引擎（含 Tavily）支持不佳，易导致结果为空 |
| **Provider 的 `include_domains` 参数**（Tavily 原生支持） | 精确、不影响 query 语义 | 仅部分 Provider 支持 |

**方案**：`SearchProvider` 接口提供可选的 `domains` 参数；Provider 若不支持，则**忽略并降级**为纯 query 检索，保证不会因过滤过严而返回空结果。

### 4.3 并行执行

```python
results = await asyncio.gather(
    provider.search(q_transport, max_results=4),
    provider.search(q_attractions, max_results=5),
    provider.search(q_reviews, max_results=5),
    return_exceptions=True,          # 单路失败不影响整体
)
```

> 对照阶段①实测：旧版串行耗时 6~15s，并行后预计 **2~5s**（等于最慢一路的耗时）。

---

## 5. 节点三：planner（路书生成）

### 5.1 Prompt 模板

```
你是资深旅行规划师。请基于下方**已检索到的真实资料**，为用户生成一份可直接执行的旅行路书。

【用户需求】
目的地：{destination}
出发地：{origin}
天数：{duration_days} 天
预算：{budget} 元
人数：{travelers}
偏好：{preferences}

【检索资料】
（每条已由系统编号；编号即引用编号，请在正文中直接使用 [n] 标注来源）
{numbered_sources}

【输出结构】（严格遵守，使用 Markdown）
## 一、行程概览
## 二、交通建议
## 三、住宿推荐
## 四、每日详细行程
   ### 第 1 天：<主题>
   ### 第 2 天：<主题>   （依此类推）
## 五、费用预估
   （必须使用 Markdown 表格，至少区分「经济型 / 舒适型」两档）
## 六、避雷与实用贴士
## 七、参考资料

【硬性要求】
1. **所有事实性信息**（票价、开放时间、交通方式、地址）必须来自上方资料，并在句末标注来源编号，如 [1][3]。
2. 资料中**未提及**的信息，写「（建议出行前核实）」，**严禁凭常识编造具体数字**。
3. 来源中的避雷、负面体验信息，必须用 `>` 引用块高亮展示，并标注编号。
4. 费用预估需与用户预算挂钩，末尾给出一句「预算达成判断」。
5. 第七节「参考资料」**只列编号**（如 `[1][3][5]`），
   ★ **绝对不要输出任何 URL、网址或链接** —— URL 由系统自动填充。
6. 若资料之间存在冲突（如同一景点两个不同票价），**并列写出并注明差异**，不要擅自取舍。
7. 全文使用简体中文，**篇幅严格按下方【目标篇幅】执行**，信息密度优先，不要空话。

【详细程度】{detail_name}　【目标篇幅】{target_length} 字
【本档位内容要求】{detail_requirements}

> 篇幅由「档位 × 天数」动态计算（L1 极简 → L4 保姆级），**1 日游与 7 日游篇幅应有明显差距**。
> 完整档位定义、篇幅矩阵与各档章节要求见 [`docs/02d-session-ux-design.md`](02d-session-ux-design.md) §4。
```

### 5.2 引用如何闭环（程序侧）

```python
# 1) 把检索结果编号后交给 LLM
numbered_sources = "\n".join(
    f"[{i}] {r.title} — {r.content[:500]}"
    for i, r in enumerate(results, start=1)
)
citations = [Citation(index=i, title=r.title, url=r.url,
                      retrieved_at=now, provider=r.source)
             for i, r in enumerate(results, start=1)]

# 2) LLM 只产出形如 "[3]" 的标记

# 3) 渲染时替换为真实链接；编号不存在则丢弃并告警
def render(text: str, citations: list[Citation]) -> str:
    def repl(m):
        idx = int(m.group(1))
        cite = index_map.get(idx)
        if cite is None:
            logger.warning("LLM 引用了不存在的编号 [%s]", idx)
            return m.group(0)          # 保留原文但不生成链接
        return f"[{idx}]({cite.url})"
    return re.sub(r"\[(\d+)\]", repl, text)
```

**这一层是「防幻觉」的最终保证**：无论 LLM 怎么写，最终出现在文件里的 URL 一定来自检索结果。

---

## 6. 节点四：refiner（增量修改）

### 6.1 意图判定改为枚举（修复旧版误判风险）

旧版用 `if "YES" in llm_output` 判断用户是否确认 —— 若模型输出 "NOT YES" 或解释性文字中含 YES 就会误判。

新版：

```python
class RefineIntent(BaseModel):
    intent: Literal["approve", "modify", "unclear"]
    summary: str          # 一句话说明用户想要什么
```

### 6.2 System Prompt

```
你是旅行规划师，正在修改一份**已完成**的路书。

【原路书】
{plan_markdown}

【可用资料】（编号与原路书一致，可直接复用）
{numbered_sources}

【用户最新消息】
{user_message}

【任务】
1. 判断用户意图，输出 intent：
   - approve：明确表示满意/确认/可以了/没问题
   - modify：提出任何修改、调整、补充要求
   - unclear：无法判断（此时礼貌地询问用户想改什么）
2. 若为 modify，**只改动用户指定的部分，其余内容逐字保留**。
   - 不要重新生成整份路书。
   - 不要"顺手优化"用户没有提到的章节。
   - 保持原有的引用编号与 Markdown 结构。
3. 若用户要求的新信息在资料中不存在，明确说明「资料中未找到，建议补充检索」，
   ★ 不要编造。

【硬性约束】
- 输出仍是完整路书（Markdown），但内容上只应有局部差异。
- 第七节参考资料仍然**只列编号，不要输出 URL**。
- 全文简体中文。
```

### 6.3 关键行为差异

| 场景 | 旧版行为（阶段①实测） | 新版行为 |
| :--- | :--- | :--- |
| 用户说"确认，就这样" | 重新走 collector → **重新全量检索+重生成** | intent=approve → 直接置 `finished` |
| 用户说"第二天轻松点" | 被当作新需求重新抽取，**全量重生成** | intent=modify → **仅改第二天**，复用已有检索结果 |
| 检索结果 | 每次重新调用 3 次 Tavily | **复用 `state.research`，零额外检索** |

---

## 7. 版本化的 Prompt 管理

规划第 5 章已定：Prompt **外置于 `agents/prompts/*.md`**，不写死在 Python 里。

```
agents/prompts/
├── collect.md
├── plan.md
├── refine.md
└── intent.md
```

好处：
- 改 Prompt 不需要动代码，便于迭代调优；
- 便于阶段③做 Prompt 快照测试（同一输入 → 稳定输出结构）；
- 非开发者（或未来的你）也能直接微调文案。

---

## 8. 待你确认的设计选择

| # | 选择点 | 我的方案 | 备选 |
| :--- | :--- | :--- | :--- |
| P1 | 路书篇幅 | 1200~2000 字 | 更短（800）/ 更长（3000） |
| P2 | 章节结构 | 七节（含独立的「避雷与实用贴士」） | 沿用旧版四节 |
| P3 | 费用表 | 经济型 / 舒适型 双档 | 单档 + 浮动区间 |
| P4 | 资料冲突处理 | 并列写出并注明差异 | 取最新/取多数 |
| P5 | 无据信息 | 写"（建议出行前核实）" | 允许模型用常识补充并标注 |
| P6 | Prompt 语言 | 中文 | 英文（部分模型英文指令遵循更好） |

---

## 附：与旧 Prompt 的逐项对照

| 维度 | 旧版 | 新版 |
| :--- | :--- | :--- |
| 输出解析 | 手工 `json.loads` + `strip("```json")` | Pydantic 结构化输出 |
| 字段类型 | 无约束（`Dict`） | 强类型 + 值域校验（`ge/le`） |
| 引用来源 | 要求 LLM "保留 URL" | **禁止 LLM 输出 URL**，程序替换编号 |
| 检索年份 | 硬编码 `2024` | `datetime.now().year` 动态注入 |
| 检索执行 | 串行 3 次 | `asyncio.gather` 并行 |
| 域名过滤 | 写死 `site:` 于 query | Provider 可选 `domains` 参数，不支持则降级 |
| 确认判定 | `"YES" in output` | `Literal["approve","modify","unclear"]` |
| 修改行为 | 全量重生成 | 最小必要修改 + 复用检索结果 |
| 事实约束 | 无明确要求 | 强制标引用；无据须写"建议核实"；冲突须并列 |
| Prompt 位置 | 内嵌于 `graph.py` | 外置 `.md`，可版本化 |
