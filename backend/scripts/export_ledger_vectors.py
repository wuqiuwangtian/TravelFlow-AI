"""从正式Python领域模型生成P0跨端账目汇总样例，不读取用户数据。"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.itinerary import Itinerary  # noqa: E402

DESTINATION = ROOT / "contracts/ledger-v1/ledger-vectors.json"


def vectors():
    def expense(key, cents):
        return {"id": key, "amount_cents": cents}

    def adjustment(key, cents, kind="refund", status="received"):
        return {"expense_id": key, "amount_cents": cents, "kind": kind, "status": status}

    cases = [
        ("empty", [], []),
        ("one-cent", [expense("a", 1)], []),
        ("exact-small-payments", [expense("a", 123), expense("b", 234)], []),
        ("received-refund", [expense("a", 12550)], [adjustment("a", 501)]),
        ("pending-refund", [expense("a", 12550)], [adjustment("a", 501, status="pending")]),
        ("received-reimbursement", [expense("a", 12550)], [adjustment("a", 501, "reimbursement")]),
        (
            "pending-reimbursement",
            [expense("a", 12550)],
            [adjustment("a", 501, "reimbursement", "pending")],
        ),
        (
            "mixed-recoveries",
            [expense("a", 12550), expense("b", 789)],
            [
                adjustment("a", 501),
                adjustment("b", 123, "reimbursement"),
                adjustment("a", 200, status="pending"),
                adjustment("b", 100, "reimbursement", "pending"),
            ],
        ),
        ("negative-net-not-clamped", [expense("a", 100)], [adjustment("a", 150)]),
        (
            "over-recovered-per-expense",
            [expense("a", 100), expense("b", 10000)],
            [adjustment("a", 80), adjustment("a", 50, "reimbursement")],
        ),
        (
            "pending-not-over-recovered",
            [expense("a", 100)],
            [adjustment("a", 300, status="pending")],
        ),
        ("legacy-unmatched-recovery", [], [adjustment("missing", 200)]),
        ("large-exact-integers", [expense("a", 9999999999)], []),
        (
            "many-payments",
            [expense(f"e{i}", 123) for i in range(2000)],
            [adjustment(f"e{i}", 1) for i in range(2000)],
        ),
    ]
    result = []
    stamp = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    for name, expenses, adjustments in cases:
        state = {
            "expenses": expenses,
            "adjustments": adjustments,
            # 非实际消费不能混入汇总；输入中保留额外字段也不能被计算器改写。
            "budget_items": [{"id": "manual", "title": "人工预估", "planned_cents": 99999}],
        }
        model = Itinerary.model_validate(
            {
                **state,
                "expenses": [
                    {**e, "title": "虚构实付", "created_at": stamp, "updated_at": stamp}
                    for e in expenses
                ],
                "adjustments": [
                    {**a, "id": f"r{i}", "created_at": stamp, "updated_at": stamp}
                    for i, a in enumerate(adjustments)
                ],
            }
        )
        vector = {"name": name, "input": state, "expected": model.ledger_summary.model_dump()}
        if name == "many-payments":
            vector["repeat"] = 2000
            state["expenses"] = [expense("e", 123)]
            state["adjustments"] = [adjustment("e", 1)]
        result.append(vector)
    return {"schema_version": 1, "source": "Itinerary.ledger_summary", "cases": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(vectors(), ensure_ascii=False, separators=(",", ":")) + "\n"
    if args.check:
        if not DESTINATION.exists() or DESTINATION.read_text(encoding="utf-8") != content:
            raise SystemExit("Ledger vectors are stale; regenerate and review domain differences.")
        print("Ledger vectors match the production Python domain model.")
    else:
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        DESTINATION.write_text(content, encoding="utf-8")
        print(f"Wrote {len(vectors()['cases'])} synthetic ledger vectors.")


if __name__ == "__main__":
    main()
