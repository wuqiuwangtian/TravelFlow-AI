"""工程草稿与运行快照；不修改旅程账目和正式路书版本。"""

from datetime import UTC, datetime

import aiosqlite

from app.core.errors import ConflictError
from app.domain.workbench import StudioGraph, StudioProject, StudioRun


class WorkbenchStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def project(self, branch_id: str) -> StudioProject:
        cursor = await self.conn.execute(
            "SELECT * FROM workflow_projects WHERE branch_id=?", (branch_id,)
        )
        row = await cursor.fetchone()
        return (
            StudioProject(
                revision=row["revision"],
                graph=StudioGraph.model_validate_json(row["graph"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            if row
            else StudioProject()
        )

    async def save(self, branch_id: str, graph: StudioGraph, expected: int) -> StudioProject:
        now = datetime.now(UTC).isoformat()
        if expected == 0:
            cursor = await self.conn.execute(
                "INSERT OR IGNORE INTO workflow_projects(branch_id,revision,graph,updated_at) "
                "VALUES (?,1,?,?)",
                (branch_id, graph.model_dump_json(), now),
            )
        else:
            cursor = await self.conn.execute(
                "UPDATE workflow_projects SET revision=revision+1,graph=?,updated_at=? "
                "WHERE branch_id=? AND revision=?",
                (graph.model_dump_json(), now, branch_id, expected),
            )
        await self.conn.commit()
        if cursor.rowcount != 1:
            raise ConflictError("工程已在其他窗口更新，请先导出当前草稿，再载入最新工程。")
        return await self.project(branch_id)

    async def runs(self, branch_id: str) -> list[StudioRun]:
        cursor = await self.conn.execute(
            "SELECT result FROM workflow_runs WHERE branch_id=? ORDER BY created_at DESC LIMIT 20",
            (branch_id,),
        )
        return [StudioRun.model_validate_json(row["result"]) for row in await cursor.fetchall()]

    async def run(self, branch_id: str, run_id: str) -> StudioRun | None:
        cursor = await self.conn.execute(
            "SELECT result FROM workflow_runs WHERE branch_id=? AND id=?", (branch_id, run_id)
        )
        row = await cursor.fetchone()
        return StudioRun.model_validate_json(row["result"]) if row else None

    async def write_run(self, run: StudioRun) -> None:
        await self.conn.execute(
            "INSERT INTO workflow_runs(branch_id,id,result,created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(branch_id,id) DO UPDATE SET result=excluded.result",
            (run.branch_id, run.id, run.model_dump_json(), run.created_at.isoformat()),
        )
        await self.conn.commit()
