"""分支中的待澄清操作，不保存已执行一半的账本草稿。"""

from typing import Literal

from pydantic import BaseModel, Field

ClarificationKind = Literal["day", "amount", "activity"]


class JourneyClarification(BaseModel):
    kind: ClarificationKind
    clauses: list[str] = Field(max_length=12)
    day_scopes: list[int | None] = Field(max_length=12)
    index: int = Field(ge=0, le=11)
    prompt: str
    itinerary_fingerprint: str
    after_message_id: str = ""


def clarification_prompt(pending: JourneyClarification | None) -> dict[str, str] | None:
    return {"kind": pending.kind, "prompt": pending.prompt} if pending else None
