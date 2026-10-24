"""模板是同一份可编辑工程的起点，没有隐藏的固定执行路径。"""

from app.domain.workbench import StudioEdge, StudioGraph, StudioNode, StudioTemplate


def node(id: str, kind: str, title: str, x: int, y: int, prompt: str = "") -> dict:
    return {
        "id": id,
        "kind": kind,
        "title": title,
        "x": x,
        "y": y,
        "parameters": {"prompt": prompt},
    }


def edge(source: str, target: str, port: str = "context", sources: bool = False) -> StudioEdge:
    return StudioEdge(
        source=source, target=target, source_port="sources" if sources else "text", target_port=port
    )


def templates() -> list[StudioTemplate]:
    brief = node("brief", "input", "旅行需求", 60, 230)
    research = node("research", "search", "目的地资料", 360, 230, "旅行目的地 景点 开放 预约 官方")
    output = node("document", "output", "成稿", 1560, 230)
    standard = [
        brief,
        research,
        node(
            "places",
            "generate",
            "景点筛选",
            660,
            230,
            "根据旅行需求与编号资料筛选具体景点，解释取舍，列出城市、建议停留时长和待核实事项。",
        ),
        node(
            "route",
            "generate",
            "串联每日路线",
            960,
            100,
            "把上游景点串成连续的每日行程，解释顺序与交通衔接。给出建议时间，未知耗时明确标注。",
        ),
        node(
            "budget",
            "generate",
            "拆解旅行预算",
            960,
            430,
            "按需求与筛选景点估算饮食、住宿、交通、门票及备用金，标注人均或全团与未知项。",
        ),
        node(
            "guide",
            "generate",
            "整合完整攻略",
            1260,
            230,
            "整合上游需求、路线和预算为一份可阅读的完整路书；冲突或缺失单列，不编造车次与已核实价格。",
        ),
        output,
    ]
    common = [
        edge("brief", "research", "query"),
        edge("brief", "places"),
        edge("research", "places", "sources", True),
    ]
    standard_edges = [
        *common,
        edge("places", "route"),
        edge("brief", "route"),
        edge("places", "budget"),
        edge("brief", "budget"),
        edge("route", "guide"),
        edge("budget", "guide"),
        edge("brief", "guide"),
        edge("guide", "document", "content"),
    ]
    inspiration = [
        brief,
        research,
        node(
            "ideas",
            "generate",
            "推荐与取舍",
            660,
            230,
            "用户还未确定目的地。结合出发地、时间、预算和偏好推荐三个合适城市，比较优缺点，给出首选。",
        ),
        node("document", "output", "城市灵感提案", 960, 230),
    ]
    alternatives = [
        brief,
        research,
        node(
            "slow",
            "generate",
            "松弛版行程",
            660,
            60,
            "依据需求和资料生成少赶路、每天不超过三处主要停留的松弛版路线，并给出预算估算。",
        ),
        node(
            "full",
            "generate",
            "丰富版行程",
            660,
            430,
            "依据需求和资料生成体验丰富但交通可解释的行程，给出预算及体力负担，未知信息单列。",
        ),
        node(
            "compare",
            "generate",
            "对比与决策",
            960,
            230,
            "比较两套上游行程的景点、节奏、交通和开支，解释适合人群，推荐一版并给出具体采用理由。",
        ),
        node("document", "output", "双方案决策书", 1260, 230),
    ]
    definitions = [
        (
            "inspiration",
            "城市灵感",
            "还没想好去哪：资料检索 → 城市推荐与取舍 → 提案。",
            inspiration,
            [
                edge("brief", "research", "query"),
                edge("brief", "ideas"),
                edge("research", "ideas", "sources", True),
                edge("ideas", "document", "content"),
            ],
        ),
        (
            "journey",
            "完整行程",
            "筛选景点后，路线与预算分别生成，再汇合成完整路书。",
            standard,
            standard_edges,
        ),
        (
            "alternatives",
            "双方案比较",
            "同一份需求与资料，分支生成松弛版／丰富版，再汇合比较。",
            alternatives,
            [
                edge("brief", "research", "query"),
                edge("brief", "slow"),
                edge("brief", "full"),
                edge("research", "slow", "sources", True),
                edge("research", "full", "sources", True),
                edge("slow", "compare"),
                edge("full", "compare"),
                edge("brief", "compare"),
                edge("compare", "document", "content"),
            ],
        ),
        (
            "structured",
            "结构化行程",
            "模型生成景点与日程数据 → 地图校验 → 程序汇总预算 → 可直接核对采用的成稿。",
            [
                brief,
                research,
                node(
                    "plan",
                    "plan",
                    "景点与日程",
                    660,
                    230,
                    "按需求与资料安排每一天的具体景点、建议时间、停留和费用，优先少折返；未知票价和交通留空，不编造确认信息。",
                ),
                node("route", "route", "路线校验", 960, 230),
                node("budget", "budget", "预算汇总", 1260, 430),
                node("document", "output", "结构化成稿", 1560, 230),
            ],
            [
                edge("brief", "research", "query"),
                edge("brief", "plan"),
                edge("research", "plan", "sources", True),
                StudioEdge(
                    source="plan", target="route", source_port="itinerary", target_port="itinerary"
                ),
                StudioEdge(
                    source="route",
                    target="budget",
                    source_port="itinerary",
                    target_port="itinerary",
                ),
                StudioEdge(
                    source="route",
                    target="document",
                    source_port="itinerary",
                    target_port="itinerary",
                ),
                StudioEdge(
                    source="budget", target="document", source_port="budget", target_port="budget"
                ),
            ],
        ),
    ]
    result = [
        StudioTemplate(
            id=id,
            title=title,
            description=description,
            graph=StudioGraph(
                title=title, nodes=[StudioNode.model_validate(n) for n in nodes], edges=edges
            ),
        )
        for id, title, description, nodes, edges in definitions
    ]
    curated = result[3].graph.model_copy(deep=True)
    curated.title = "筛选与优化"
    curated.nodes.insert(
        3,
        StudioNode(
            id="selection",
            kind="select",
            title="景点筛选",
            x=960,
            y=230,
            parameters={"max_attractions_per_day": 3},
        ),
    )
    for item in curated.nodes:
        if item.id in {"route", "budget", "document"}:
            item.x += 300
        if item.id == "route":
            item.parameters.route_order = "nearby"
    curated.edges = [e for e in curated.edges if not (e.source == "plan" and e.target == "route")]
    curated.edges.extend(
        [
            StudioEdge(
                source="plan", target="selection", source_port="itinerary", target_port="itinerary"
            ),
            StudioEdge(
                source="selection", target="route", source_port="itinerary", target_port="itinerary"
            ),
        ]
    )
    result.append(
        StudioTemplate(
            id="curated",
            title="筛选与优化",
            description="候选日程 → 按必去／排除和数量筛选 → 减少直线折返并核验交通 → 成稿。",
            graph=curated,
        )
    )
    return result
