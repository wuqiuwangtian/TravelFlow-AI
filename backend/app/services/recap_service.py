"""旅后回顾只从执行状态和账本取事实；模板生成不调用模型。"""

import hashlib
import html
import json
from typing import Any

from app.domain.itinerary import Itinerary
from app.domain.models import Requirements
from app.services.budget_service import budget_overview


def recap_view(itinerary: Itinerary, requirements: Requirements) -> dict[str, Any]:
    days: list[dict[str, Any]] = []
    for day in itinerary.days:
        groups: dict[str, list[str]] = {"completed": [], "skipped": [], "unconfirmed": []}
        for activity in day.activities:
            if activity.is_context_anchor:
                continue
            key = (
                activity.progress
                if activity.progress in {"completed", "skipped"}
                else "unconfirmed"
            )
            label = activity.title
            if activity.place_name and activity.place_name != activity.title:
                label += f"（{activity.place_name}）"
            groups[key].append(label)
        days.append({"day": day.day, **groups})
    budget = budget_overview(itinerary, requirements)
    facts = {
        "days": days,
        "ledger": itinerary.ledger_summary.model_dump(),
        "expense_count": len(itinerary.expenses),
        "unsettled_count": sum(not row.settled for row in budget.rows),
        "unknown_count": budget.unknown_count,
        "destination": requirements.destination,
        "departure_date": requirements.departure_date,
    }
    fingerprint = hashlib.sha256(
        json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    visits = sum(len(d["completed"]) for d in days)
    template = (
        f"这次旅程留下了{visits}项已完成活动的记录。\n"
        + (
            "\n".join(
                f"第{d['day']}天：" + "、".join(d["completed"]) for d in days if d["completed"]
            )
            or "还没有确认完成的活动，可先补充旅中状态。"
        )
        + "\n\n最难忘的片段：\n\n下次还想："
    )
    saved = itinerary.recap
    return {
        **facts,
        "source_fingerprint": fingerprint,
        "title": saved.title if saved else f"{requirements.destination or '我的旅行'} · 旅行回顾",
        "body": saved.body if saved else template,
        "social_copy": saved.social_copy if saved else "",
        "template": template,
        "saved": saved is not None,
        "stale": bool(saved and saved.source_fingerprint != fingerprint),
        "photos": [p.model_dump() for p in saved.photos] if saved else [],
        "cover_photo_id": saved.cover_photo_id if saved else None,
    }


def render_recap(view: dict[str, Any], *, as_html: bool = False) -> str:
    money = lambda cents: f"¥{cents / 100:.2f}"  # noqa: E731
    ledger = view["ledger"]
    lines = [
        f"# {view['title']}",
        "",
        "## 我的回忆（用户可编辑）",
        view["body"],
        "",
        "## 当前版本的实际记录",
    ]
    if view["stale"]:
        lines.append("回忆文字依据的记录已变化，请核对；以下事实汇总按当前版本更新。")
    for day in view["days"]:
        lines.append(f"### 第{day['day']}天")
        for key, label in (
            ("completed", "已完成"),
            ("skipped", "已跳过"),
            ("unconfirmed", "尚未确认到访"),
        ):
            lines.append(f"{label}：" + ("、".join(day[key]) or "无记录"))
    lines += [
        "",
        "## 已记支出（全团）",
        f"已记消费：{view['expense_count']}笔",
        f"原始实付：{money(ledger['paid_cents'])}",
        f"已到账退款：{money(ledger['refunded_cents'])}",
        f"净消费：{money(ledger['net_expense_cents'])}",
        f"已到账报销：{money(ledger['reimbursed_cents'])}",
        f"净垫付：{money(ledger['net_outlay_cents'])}",
        f"待到账退款：{money(ledger['pending_refund_cents'])}",
        f"待到账报销：{money(ledger['pending_reimbursement_cents'])}",
        f"尚有{view['unsettled_count']}项未结算，{view['unknown_count']}项未定价。",
        "这里只汇总已记录账目，不代表全部旅行开支；预估不作为实际消费。",
        "未确认到访的计划地点不计入已完成记录。票据图片、消费备注和订单资料不包含在此导出中。",
    ]
    text = "\n\n".join(lines)
    if not as_html:
        return text
    blocks = []
    for line in lines:
        tag, content = "p", line
        for prefix, heading in (("### ", "h3"), ("## ", "h2"), ("# ", "h1")):
            if line.startswith(prefix):
                tag, content = heading, line[len(prefix) :]
                break
        blocks.append(f"<{tag}>{html.escape(content)}</{tag}>")
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(view['title'])}</title>"
        "<style>body{margin:0;background:#f0fdfa;color:#134e4a;font:16px/1.9 system-ui}"
        "main{max-width:860px;margin:40px auto;padding:40px;background:white;border-radius:24px}"
        "p{white-space:pre-wrap;overflow-wrap:anywhere}h1{font-size:32px;line-height:1.4}"
        "h2{margin-top:36px;border-bottom:1px solid #ccfbf1;padding-bottom:8px}"
        "figure{margin:24px 0}img{display:block;width:100%;max-height:700px;object-fit:contain;"
        "border-radius:16px;background:#f8fafc}figcaption{font-size:14px;color:#475569}"
        ".photo-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}"
        ".photo-grid figure{margin:0}.photo-grid img{height:280px;object-fit:contain}"
        "@media(max-width:600px){main{margin:12px;padding:20px}}"
        "@media print{body{background:white}main{margin:0;padding:0}"
        "figure{break-inside:avoid}}</style>"
        "<main>" + "".join(blocks) + "</main></html>"
    )
