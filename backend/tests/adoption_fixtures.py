"""成稿采用验收：替换未来安排，保留旧路书中的旅中起点与关联公园。"""

from conftest import requirements

from app.domain.itinerary import Activity, DayDraft
from app.domain.studio_adoption import StudioPlanDraft


def adoption_draft():
    return StudioPlanDraft(
        adoptable=True,
        requirements=requirements(),
        days=[
            DayDraft(
                day=1,
                title="夜间抵达",
                activities=[
                    Activity(
                        title="住宿建议",
                        place_name="示例酒店",
                        city="北京",
                        kind="hotel",
                        start_time="00:30",
                        start_day_offset=1,
                        duration_minutes=30,
                        transport_minutes=30,
                    )
                ],
            ),
            DayDraft(
                day=2,
                title="公园漫步",
                activities=[
                    Activity(
                        title="新标题的公园",
                        place_name="示例公园",
                        city="北京",
                        kind="attraction",
                        start_time="08:00",
                        duration_minutes=60,
                        cost=999,
                        cost_basis="total",
                        cost_source="manual",
                    )
                ],
            ),
            DayDraft(
                day=3,
                title="新的北京安排",
                activities=[
                    Activity(
                        title="天坛游览",
                        place_name="天坛公园",
                        city="北京",
                        kind="attraction",
                        start_time="09:00",
                        duration_minutes=60,
                        cost=80,
                        cost_basis="total",
                        source_indices=[1],
                    ),
                    Activity(
                        title="北海散步",
                        place_name="北海公园",
                        city="北京",
                        kind="attraction",
                        start_time="11:00",
                        duration_minutes=60,
                        transport_minutes=20,
                    ),
                ],
            ),
        ],
    )
