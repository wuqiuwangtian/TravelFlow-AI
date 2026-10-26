"""确定性汇合候选；保留分支证据和明确地点，不把冲突报价拼成已核实费用。"""

import hashlib
from datetime import UTC

from app.core.errors import InvalidRequest
from app.domain.models import Requirements
from app.domain.studio_candidates import (
    CandidatePlace,
    CandidatePool,
    CandidateVariant,
    MergeDecision,
    MergeReport,
    PoolLocationReport,
)
from app.domain.workbench import NodeArtifact, StudioNode, StudioSource
from app.maps.matching import normal_name
from app.services.studio_location import fresh_locations, same_city


def name_key(p: CandidatePlace) -> tuple[str, str]:
    return normal_name(p.city.strip().removesuffix("市")), normal_name(p.place_name)


def confirmed(p: CandidatePlace) -> str | None:
    location = p.location
    if (
        location
        and location.status == "matched"
        and location.place
        and fresh_locations(location.checked_at)
        and same_city(p.city, location.place.city)
    ):
        return location.place.id
    return None


def merge_requirements(pools: list[CandidatePool]) -> Requirements:
    result = pools[0].requirements.model_copy(deep=True)
    labels = {
        "destination": "目的地",
        "origin": "出发地",
        "duration_days": "天数",
        "travelers": "人数",
        "departure_date": "出发日期",
        "budget": "预算",
        "budget_basis": "预算口径",
    }
    for pool in pools[1:]:
        for field, label in labels.items():
            old, new = getattr(result, field), getattr(pool.requirements, field)
            if new is None or new == "":
                continue
            if old is None or old == "":
                setattr(result, field, new)
            elif old != new and not (field in {"destination", "origin"} and same_city(old, new)):
                raise InvalidRequest(f"候选池的{label}不一致，请统一各分支的旅行需求后合并。")
        result.preferences = list(
            dict.fromkeys([*result.preferences, *pool.requirements.preferences])
        )
    return result


def merge_candidates(
    node: StudioNode, inputs: list[dict], sources: list[StudioSource]
) -> CandidatePool:
    if not 1 <= len(inputs) <= 8:
        raise InvalidRequest("候选合并一次接收1至8份候选池，请分层合并。")
    priority = {id: i for i, id in enumerate(node.parameters.merge_order)}
    ordered = sorted(inputs, key=lambda item: (priority.get(item["source"], 8), item["source"]))
    artifacts = [NodeArtifact.model_validate(item["artifact"]) for item in ordered]
    pools = [a.candidates for a in artifacts if a.candidates]
    if len(pools) != len(ordered):
        raise InvalidRequest("上游未提供候选池，请检查端口后重跑。")
    requirements = merge_requirements(pools)
    index_for = {str(s.url): i for i, s in enumerate(sources, 1)}
    groups: list[list[CandidateVariant]] = []
    warnings = [w for pool in pools for w in pool.warnings]
    clipped_refs = 0
    for item, artifact, pool in zip(ordered, artifacts, pools, strict=True):
        for original in pool.places:
            p = original.model_copy(deep=True)
            indices = [
                index_for[str(artifact.sources[i - 1].url)]
                for i in p.source_indices
                if 1 <= i <= len(artifact.sources) and str(artifact.sources[i - 1].url) in index_for
            ]
            clipped_refs += len(p.source_indices) - len(indices)
            p.source_indices = list(dict.fromkeys(indices))
            if p.cost_source == "reference" and not p.source_indices:
                p.cost_source = "estimate" if p.cost is not None else "unknown"
            variant = CandidateVariant(
                source_id=item["source"], source_title=item["title"], candidate=p
            )
            target = None
            for group in groups:
                if name_key(group[0].candidate)[0] != name_key(p)[0]:
                    continue
                ids = {id for v in group for id in [confirmed(v.candidate)] if id}
                selected = {
                    v.candidate.location.selected_id
                    for v in group
                    if v.candidate.location and v.candidate.location.selected_id
                }
                pid = confirmed(p)
                chosen = p.location.selected_id if p.location else None
                if pid and ids and pid not in ids or chosen and selected and chosen not in selected:
                    continue
                if chosen and ids and chosen not in ids or pid and selected and pid not in selected:
                    continue
                if any(name_key(v.candidate) == name_key(p) for v in group) or pid and pid in ids:
                    target = group
                    break
            if target is None:
                groups.append([variant])
            else:
                target.append(variant)
    used_ids: dict[str, int] = {}
    for group in groups:
        id = group[0].candidate.id
        used_ids[id] = used_ids.get(id, 0) + 1
    places: list[CandidatePlace] = []
    decisions: list[MergeDecision] = []
    for group in groups:
        first = group[0].candidate
        p = first.model_copy(deep=True)
        explicit = next(
            (
                v.candidate.location
                for v in group
                if v.candidate.location and v.candidate.location.selected_id
            ),
            None,
        )
        if explicit:
            p.location = explicit.model_copy(deep=True)
        # 不把未核实的地图与不同分支的预算、时长自动拼成一个“更完整”事实。
        if (
            p.location
            and p.location.status != "unconfigured"
            and not fresh_locations(p.location.checked_at)
        ):
            p.location.place, p.location.candidates = None, []
            p.location.status, p.location.checked_at = "unavailable", None
            p.location.reason = "合并保留了原地点意图，但定位已过期或无有效时间，请重跑定位。"
        separate = used_ids[first.id] > 1
        if separate:
            discriminator = (
                confirmed(first)
                or (first.location.selected_id if first.location else None)
                or group[0].source_id
            )
            p.id = (
                "poi-"
                + hashlib.sha256(repr((name_key(first), discriminator)).encode()).hexdigest()[:16]
            )
        prices = {
            (v.candidate.cost, v.candidate.cost_basis)
            for v in group
            if v.candidate.cost is not None
        }
        durations = {
            v.candidate.duration_minutes for v in group if v.candidate.duration_minutes is not None
        }
        conflicts = []
        if len(prices) > 1:
            conflicts.append("费用或口径不同")
            if node.parameters.merge_cost_policy == "unknown":
                p.cost, p.cost_basis, p.cost_source = None, "unknown", "unknown"
        if len(durations) > 1:
            conflicts.append("建议停留不同")
        reason = "保留优先来源的名称、理由、停留与引用，其他分支建议见取舍记录。"
        if len(group) > 1:
            reason = "同名同城或相同明确地图地点合并；" + reason
        if separate:
            reason = "同名指向不同明确地点或不同指定，分别保留，未当作重复删除。"
        if conflicts:
            reason += (
                "费用冲突保留未知。"
                if node.parameters.merge_cost_policy == "unknown" and len(prices) > 1
                else "冲突字段沿用优先来源。"
            )
        omitted = len(places) >= node.parameters.candidate_limit
        if not omitted:
            places.append(p)
        decisions.append(
            MergeDecision(
                candidate_id=p.id,
                place_name=p.place_name,
                action="omitted"
                if omitted
                else "separate"
                if separate
                else "combined"
                if len(group) > 1
                else "kept",
                reason="超过合并上限，本次未入池；原候选和来源仍保留。" if omitted else reason,
                conflicts=conflicts,
                variants=group,
            )
        )
    if clipped_refs:
        warnings.append(
            "部分来源超过本次来源上限或引用范围，相关编号未沿用；完整资料仍在上游快照。"
        )
    if any(d.conflicts for d in decisions):
        warnings.append(
            "合并检测到建议差异；默认将冲突费用保留未知，停留沿用优先来源，请核对取舍记录。"
            if node.parameters.merge_cost_policy == "unknown"
            else "合并检测到建议差异，本节点明确沿用优先来源；保留报价不代表实时核实。"
        )
    locations = [p.location for p in places if p.location]
    stamps = [
        location.checked_at.replace(tzinfo=UTC)
        if location.checked_at.tzinfo is None
        else location.checked_at.astimezone(UTC)
        for location in locations
        if location.checked_at
    ]
    summary = (
        PoolLocationReport(
            status="ready"
            if len(locations) == len(places)
            and all(location.status == "matched" for location in locations)
            else "partial",
            checked_at=min(stamps) if stamps else None,
            warnings=["定位来自合并输入，保留时间与明确指定；需要刷新时运行候选定位。"],
        )
        if locations
        else None
    )
    before = sum(len(pool.places) for pool in pools)
    return CandidatePool(
        requirements=requirements,
        places=places,
        location=summary,
        warnings=list(dict.fromkeys(w[:160] for w in warnings))[:12],
        merge=MergeReport(
            input_pools=len(pools),
            before_count=before,
            after_count=len(places),
            duplicate_count=before - len(groups),
            omitted_count=len(groups) - len(places),
            source_order=[item["source"] for item in ordered],
            decisions=decisions,
        ),
    )
