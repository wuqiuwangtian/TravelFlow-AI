from typing import TypedDict, List, Dict, Annotated
from langgraph.graph.message import add_messages

# 确保类名必须是 PlanState，因为 graph.py 正在尝试导入这个名字
class PlanState(TypedDict):
    messages: Annotated[List[dict], add_messages]  # 对话历史
    user_requirements: Dict  # 结构化需求 {dest, days, budget...}
    research_content: str    # 收集到的所有 raw data
    plan_draft: str          # 当前生成的 Markdown 计划
    status: str              # 状态机标记: collecting, researching, planning, refining, finished
    missing_fields: List[str] # 还需要问什么