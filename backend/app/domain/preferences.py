"""仅由用户主动保存的常用偏好，不从旅行记录自动推断。"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

Preference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class PreferenceValues(BaseModel):
    preferences: list[Preference] = Field(default_factory=list, max_length=12)

    @field_validator("preferences")
    @classmethod
    def clean_preferences(cls, values: list[str]) -> list[str]:
        if any(any(ord(char) < 32 or ord(char) == 127 for char in value) for value in values):
            raise ValueError("每条偏好不能包含换行或控制字符")
        return list(dict.fromkeys(values))


class SavedPreferences(PreferenceValues):
    revision: int = Field(default=0, ge=0)
    updated_at: datetime | None = None


class SavePreferences(PreferenceValues):
    expected_revision: int = Field(ge=0)
