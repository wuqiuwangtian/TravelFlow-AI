"""终止节点与业务更新一起写入完成标记，回复丢失后可核对而不重复执行。"""

from app.domain.enums import PlanStatus


def mark_completed_request(node):
    async def wrapped(state):
        result = await node(state)
        status = result.get("status", state.get("status"))
        if status in {PlanStatus.COLLECTING, PlanStatus.REVIEWING, PlanStatus.FINISHED}:
            result["completed_chat"] = state.get("chat_request")
        return result

    return wrapped
