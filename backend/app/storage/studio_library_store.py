"""个人模板的独立存储、修订比较与最近请求回执。"""

import json
import unicodedata
from datetime import UTC, datetime

import aiosqlite

from app.core.errors import ConflictError, InvalidRequest, NotFoundError
from app.domain.studio_library import (
    DeleteUserTemplate,
    SaveUserTemplate,
    UserTemplate,
    UserTemplateSummary,
)
from app.domain.workbench import StudioGraph
from app.services.studio_engine import digest, topology


def template_row(row) -> UserTemplate:
    graph = StudioGraph.model_validate_json(row["graph"])
    inputs = json.loads(row["trip_input_ids"])
    return UserTemplate(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        revision=row["revision"],
        graph=graph,
        trip_input_ids=inputs,
        node_count=len(graph.nodes),
        trip_input_count=len(inputs),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def prepare(payload: SaveUserTemplate) -> SaveUserTemplate:
    result = payload.model_copy(deep=True)
    result.title = result.title.strip()
    result.description = result.description.strip()
    if not result.title or not result.graph.nodes:
        raise InvalidRequest("请填写模板名称，并至少添加一个节点。")
    topology(result.graph)
    inputs = {node.id for node in result.graph.nodes if node.kind == "input"}
    ids = result.trip_input_ids if result.trip_input_ids is not None else sorted(inputs)
    if len(ids) != len(set(ids)) or not set(ids) <= inputs:
        raise InvalidRequest("旅行需求只能绑定已有的文本输入节点，且不能重复。")
    result.trip_input_ids = sorted(ids)
    result.graph.title = result.title
    for node in result.graph.nodes:
        if node.id in ids or node.kind != "input":
            node.parameters.text = ""
    return result


class StudioLibraryStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def row(self, id: str):
        cursor = await self.conn.execute("SELECT * FROM workflow_user_templates WHERE id=?", (id,))
        return await cursor.fetchone()

    async def get(self, id: str) -> UserTemplate:
        row = await self.row(id)
        if not row or row["deleted"]:
            raise NotFoundError("模板已删除或不存在，请刷新模板库。")
        return template_row(row)

    async def list(self) -> list[UserTemplateSummary]:
        cursor = await self.conn.execute(
            "SELECT * FROM workflow_user_templates WHERE deleted=0 "
            "ORDER BY updated_at DESC,id LIMIT 100"
        )
        return [
            UserTemplateSummary.model_validate(template_row(row).model_dump())
            for row in await cursor.fetchall()
        ]

    async def save(self, payload: SaveUserTemplate, id: str | None = None) -> UserTemplate:
        payload = prepare(payload)
        id = id or payload.request_id
        fingerprint = digest(payload.model_dump(mode="json"))
        row = await self.row(id)
        if row and row["last_request_id"] == payload.request_id:
            if row["last_fingerprint"] == fingerprint and not row["deleted"]:
                return template_row(row)
            raise ConflictError("同一次模板请求的内容已变化或模板已删除，请重新载入核对。")
        if payload.expected_revision == 0:
            if row:
                raise ConflictError("此模板请求已处理且后来发生变化，请刷新模板库核对。")
            cursor = await self.conn.execute(
                "SELECT COUNT(*) FROM workflow_user_templates WHERE deleted=0"
            )
            count = await cursor.fetchone()
            assert count is not None
            if count[0] >= 100:
                raise InvalidRequest("本机最多保存100个个人模板，请先删除不再使用的模板。")
        elif not row or row["deleted"]:
            raise NotFoundError("模板已删除或不存在，不能用旧页面重新创建。")
        elif row["revision"] != payload.expected_revision:
            raise ConflictError(
                "模板已在其他页面更新，请载入最新模板后再保存；当前编辑内容已保留。"
            )
        name_key = " ".join(unicodedata.normalize("NFKC", payload.title).casefold().split())
        now = datetime.now(UTC).isoformat()
        values = (
            payload.title,
            payload.description,
            payload.graph.model_dump_json(),
            json.dumps(payload.trip_input_ids),
            name_key,
            now,
            payload.request_id,
            fingerprint,
        )
        try:
            if payload.expected_revision == 0:
                await self.conn.execute(
                    "INSERT INTO workflow_user_templates(title,description,graph,trip_input_ids,"
                    "name_key,updated_at,last_request_id,last_fingerprint,id,revision,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                    (*values, id, now),
                )
            else:
                cursor = await self.conn.execute(
                    "UPDATE workflow_user_templates SET title=?,description=?,graph=?,"
                    "trip_input_ids=?,name_key=?,updated_at=?,last_request_id=?,last_fingerprint=?,"
                    "revision=revision+1 WHERE id=? AND revision=? AND deleted=0",
                    (*values, id, payload.expected_revision),
                )
                if cursor.rowcount != 1:
                    raise ConflictError("模板已变化，请刷新模板库后重试。")
            await self.conn.commit()
        except aiosqlite.IntegrityError as exc:
            raise ConflictError("已有同名模板，请换一个名称，或选择更新已有模板。") from exc
        return await self.get(id)

    async def delete(self, id: str, payload: DeleteUserTemplate) -> None:
        row = await self.row(id)
        fingerprint = digest({"delete": id, **payload.model_dump()})
        if row and row["last_request_id"] == payload.request_id:
            if row["deleted"] and row["last_fingerprint"] == fingerprint:
                return
            raise ConflictError("同一次模板请求的内容已变化，请重新载入。")
        if not row or row["deleted"]:
            raise NotFoundError("模板已删除或不存在，请刷新模板库。")
        cursor = await self.conn.execute(
            "UPDATE workflow_user_templates SET deleted=1,revision=revision+1,title='',"
            "description='',graph='{}',trip_input_ids='[]',name_key='',updated_at=?,"
            "last_request_id=?,last_fingerprint=? WHERE id=? AND revision=? AND deleted=0",
            (
                datetime.now(UTC).isoformat(),
                payload.request_id,
                fingerprint,
                id,
                payload.expected_revision,
            ),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            raise ConflictError("模板已在其他页面更新，请刷新模板库核对后再删除。")
