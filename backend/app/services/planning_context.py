"""从 checkpoint 投影可公开的需求，不推断聊天内容或填入业务默认值。"""

from app.domain.enums import PlanStatus
from app.domain.models import Requirements
from app.domain.schemas import PlanningContext


def planning_context(values: dict) -> PlanningContext:
    raw = values.get("requirements")
    requirements = raw if isinstance(raw, Requirements) else Requirements.model_validate(raw or {})
    status = values.get("status")
    return PlanningContext(
        requirements=requirements,
        missing_fields=requirements.missing_fields(),
        status=status if status in set(PlanStatus) else None,
        destination_ideas=list(values.get("destination_ideas") or []),
        destination_recommendation_requested=bool(
            values.get("destination_recommendation_requested")
        ),
    )
