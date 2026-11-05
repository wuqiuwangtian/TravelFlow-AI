"""隔离本地API基线，不读取.env、个人数据库或调用外部服务。

在backend目录运行：
PYTHONPATH=. .venv/bin/python scripts/benchmark_local.py --output /tmp/local-api.json
测量ASGI接口及真实SQLite/checkpoint；不代表代理、网络、手机或APK性能。
"""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import platform
import tempfile
from collections import defaultdict
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.agents.checkpoint import open_checkpointer, thread_config
from app.agents.graph import build_graph
from app.agents.state import initial_state
from app.api import routes_itinerary, routes_session
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.domain.enums import PlanStatus
from app.domain.itinerary import DayPlan, ExpenseCreate, Itinerary, ScheduledActivity
from app.domain.models import Requirements
from app.services.chat_service import ChatService
from app.services.itinerary_service import sync_markdown
from app.services.travel_service import add_expense
from app.storage.database import create_connection
from app.storage.session_store import SessionStore


class NoExternalCalls:
    name = "benchmark-disabled"

    def __getattr__(self, name):
        raise AssertionError(f"benchmark must not invoke external provider: {name}")


def summary(values):
    ordered = sorted(values)
    return {
        "samples": len(values),
        "p50": round(ordered[math.ceil(len(values) * 0.5) - 1], 3),
        "p95": round(ordered[math.ceil(len(values) * 0.95) - 1], 3),
        "max": round(ordered[-1], 3),
    }


def sample_plan(days):
    plan = Itinerary(
        days=[
            DayPlan(
                day=day,
                title=f"隔离样例第{day}天",
                activities=[
                    ScheduledActivity(
                        id=f"d{day}-a{i}",
                        title=f"虚构停留{i}",
                        city="样例城市",
                        duration_minutes=60,
                        cost=None if i % 2 else 20,
                        cost_basis="total",
                        cost_source="estimate" if i % 2 == 0 else "unknown",
                    )
                    for i in range(6)
                ],
            )
            for day in range(1, days + 1)
        ]
    )
    for day in range(1, days + 1):
        add_expense(
            plan,
            ExpenseCreate(
                title="样例已有实付", amount="12.34", day=day, request_id=f"seed-{day:08d}"
            ),
        )
    return plan


async def benchmark_case(folder, days, samples):
    settings = Settings(_env_file=None, session_db_path=folder / "trip.db", amap_api_key=None)
    conn = await create_connection(settings.session_db_path)
    try:
        async with open_checkpointer(settings.session_db_path) as saver:
            graph = build_graph(
                NoExternalCalls(), NoExternalCalls(), settings=settings, checkpointer=saver
            )
            store = SessionStore(conn)
            app = FastAPI()
            app.state.store, app.state.graph = store, graph
            app.dependency_overrides[get_settings] = lambda: settings
            register_exception_handlers(app)
            app.include_router(routes_session.router)
            app.include_router(routes_itinerary.router)
            session = await store.create_session("隔离性能样例")
            branch = await store.create_branch(session.id, "主方案", is_primary=True)
            req = Requirements(destination="样例城市", duration_days=days, travelers=1)
            plan = sample_plan(days)
            values = {
                **initial_state(),
                "requirements": req,
                "itinerary": plan,
                "plan_markdown": sync_markdown("# 隔离性能样例", plan, req),
                "status": PlanStatus.REVIEWING,
            }
            config = await graph.aupdate_state(thread_config(branch.id), values, as_node="planner")
            first_id = await ChatService(graph=graph, store=store).record_version(
                branch.id, values, config
            )
            original = await store.get_version_detail(first_id)
            current_id = first_id
            for revision in range(1, 10):
                plan.days[0].title = f"隔离历史修订{revision}"
                values["plan_markdown"] = sync_markdown("# 隔离性能样例", plan, req)
                config = await graph.aupdate_state(
                    thread_config(branch.id), values, as_node="refiner"
                )
                current_id = await ChatService(graph=graph, store=store).record_version(
                    branch.id, values, config, trigger="benchmark_seed"
                )
            spans = defaultdict(list)
            active = {}

            def timed_sync(fn, label):
                def wrapped(*args, **kwargs):
                    start = perf_counter()
                    try:
                        return fn(*args, **kwargs)
                    finally:
                        active[label] = active.get(label, 0) + (perf_counter() - start) * 1000

                return wrapped

            def timed_async(fn, label):
                async def wrapped(*args, **kwargs):
                    start = perf_counter()
                    try:
                        return await fn(*args, **kwargs)
                    finally:
                        active[label] = active.get(label, 0) + (perf_counter() - start) * 1000

                return wrapped

            results = {}
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://isolated"
                ) as client,
                AsyncExitStack() as stack,
            ):
                stack.enter_context(
                    patch.object(
                        routes_itinerary,
                        "sync_markdown",
                        timed_sync(routes_itinerary.sync_markdown, "markdown_ms"),
                    )
                )
                for target, names in [
                    (graph, ["aget_state", "aupdate_state"]),
                    (store, ["get_version_detail", "create_version", "save_plan"]),
                ]:
                    for name in names:
                        stack.enter_context(
                            patch.object(
                                target, name, timed_async(getattr(target, name), name + "_ms")
                            )
                        )
                for operation in ("read_version", "read_session", "progress", "expense"):
                    elapsed, sizes = [], []
                    spans.clear()
                    for i in range(samples):
                        active.clear()
                        start = perf_counter()
                        if operation == "read_version":
                            response = await client.get(f"/api/versions/{current_id}")
                        elif operation == "read_session":
                            response = await client.get(f"/api/sessions/{session.id}")
                        elif operation == "progress":
                            response = await client.patch(
                                f"/api/versions/{current_id}/days/1/activities/d1-a0/progress",
                                json={"progress": "in_progress" if i % 2 == 0 else "not_started"},
                            )
                        else:
                            response = await client.post(
                                f"/api/versions/{current_id}/expenses",
                                json={
                                    "title": "样例追加实付",
                                    "amount": "1.23",
                                    "day": days,
                                    "request_id": f"benchmark-{days}-{i:08d}",
                                },
                            )
                        elapsed.append((perf_counter() - start) * 1000)
                        if response.status_code != 200:
                            raise RuntimeError(
                                f"{operation}: {response.status_code}: {response.text}"
                            )
                        sizes.append(len(response.content))
                        data = response.json()
                        if operation in {"progress", "expense"}:
                            current_id = data["version"]["id"]
                        for label, value in active.items():
                            spans[label].append(value)
                    results[operation] = {
                        "asgi_ms": summary(elapsed),
                        "response_bytes": summary(sizes),
                        "spans_ms": {k: summary(v) for k, v in spans.items()},
                    }
            final = await store.get_version_detail(current_id)
            assert await store.get_version_detail(first_id) == original
            assert final.itinerary.ledger_summary.paid_cents == days * 1234 + samples * 123
            assert len(final.itinerary.expenses) == days + samples
            assert final.itinerary.days[-1].activities[1].cost is None
            versions = await store.list_versions(branch.id)
            assert len(versions) == 10 + samples * 2
            return {
                "days": days,
                "activities": days * 6,
                "initial_versions": 10,
                "final_versions": len(versions),
                "results": results,
            }
    finally:
        await conn.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.samples <= 1000:
        parser.error("samples must be between 2 and 1000")
    logging.disable(logging.CRITICAL)
    cases = []
    with tempfile.TemporaryDirectory(prefix="travelflow-baseline-") as tmp:
        for days in (3, 7, 14):
            folder = Path(tmp) / str(days)
            folder.mkdir()
            cases.append(await benchmark_case(folder, days, args.samples))
    report = {
        "schema_version": 1,
        "workload_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "measurement": "in-process ASGI; SQLite; initialized runtime; sequential requests",
        "excluded": ["network", "Next.js proxy", "browser", "Android", "external providers"],
        "spans": "nested, do not sum; instrumentation overhead included",
        "invariants": "historical version immutable; exact ledger; unknown costs preserved",
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(cases)} isolated cases to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
