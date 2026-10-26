"""离线选点结果，供节点和浏览器验收复用。"""

from conftest import requirements

from app.domain.studio_candidates import CandidateDraft, CandidateSuggestion


def candidate_draft():
    names = [
        "天坛公园",
        "示例公园",
        "北海公园",
        "故宫博物院",
        "景山公园",
        "颐和园",
        "圆明园",
        "国家博物馆",
        "奥林匹克公园",
        "陶然亭公园",
    ]
    return CandidateDraft(
        ready=True,
        requirements=requirements(),
        places=[
            CandidateSuggestion(
                place_name=name,
                city="北京",
                reason=f"候选理由：适合城市漫步，{name}作为本次备选。",
                duration_minutes=60,
                cost=10,
                cost_basis="per_person",
                source_indices=[1],
            )
            for name in names
        ],
    )
