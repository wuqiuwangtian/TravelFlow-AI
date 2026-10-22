from dotenv import dotenv_values

from app.core.env_writer import MAP_SECTION, write_env
from app.core.errors import LLMAuthenticationFailed
from app.llm.openai_compat import _map_error


def test_auth_error_identifies_model_key_without_echoing_provider_response():
    error = _map_error(Exception("401 invalid API key: fake-model-secret"), structured=True)
    assert isinstance(error, LLMAuthenticationFailed)
    assert error.code == "LLM_AUTH_FAILED"
    assert "LLM_API_KEY" in error.message
    assert "高德 Key" in error.message
    assert "fake-model-secret" not in str(error)
    assert error.detail is None


def test_new_map_key_is_written_in_documented_section(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# 个人备注\nLLM_API_KEY=model-key\n\n"
        "# ---------------- 生成偏好 ----------------\nDEFAULT_DETAIL_LEVEL=L2_standard\n\n"
        "# ---------------- 数据 ----------------\nSESSION_DB_PATH=./data/my.db\n"
    )
    changed = write_env(path, {"AMAP_API_KEY": "map-key"})
    text = path.read_text()
    assert changed == ["AMAP_API_KEY"]
    assert text.index(MAP_SECTION) < text.index("# ---------------- 数据")
    assert (
        text.index(MAP_SECTION)
        < text.index("AMAP_API_KEY=")
        < text.index("# ---------------- 生成偏好")
    )
    assert "# 个人备注" in text
    assert "# 此 Key 与大模型的 LLM_API_KEY 独立" in text
    assert dotenv_values(path)["LLM_API_KEY"] == "model-key"
    assert dotenv_values(path)["AMAP_API_KEY"] == "map-key"
    assert path.stat().st_mode & 0o777 == 0o600


def test_existing_misplaced_key_is_moved_without_changing_value_and_layout_is_idempotent(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# ---------------- 数据 ----------------\nSESSION_DB_PATH=./data/my.db\n"
        "# 数据库的个人注释\nAMAP_API_KEY=old-key\nAMAP_API_KEY='final-map-key'\n"
        "AMAP_TIMEOUT_SECONDS=25\n"
    )
    before = dotenv_values(path)
    assert write_env(path, {}) == []
    first = path.read_text()
    assert dict(dotenv_values(path)) == dict(before)
    assert first.index(MAP_SECTION) < first.index("# ---------------- 数据")
    assert first.count("AMAP_API_KEY=") == 1
    assert "# 数据库的个人注释" in first
    write_env(path, {})
    assert path.read_text() == first


def test_existing_map_section_preserves_comments_and_stays_unique(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        MAP_SECTION + "\n# 我的地图说明\nAMAP_API_KEY=old-key\n\n"
        "# ---------------- 数据 ----------------\nSESSION_TTL_HOURS=168\n"
    )
    write_env(path, {"AMAP_API_KEY": "new-key"})
    first = path.read_text()
    assert first.count(MAP_SECTION) == 1
    assert "# 我的地图说明" in first
    assert dotenv_values(path)["AMAP_API_KEY"] == "new-key"
    write_env(path, {})
    assert path.read_text() == first


async def test_invalid_structured_response_is_repaired_once(monkeypatch):
    from pydantic import BaseModel, ValidationError

    from app.llm.openai_compat import OpenAICompatProvider

    class Output(BaseModel):
        value: int

    calls = []

    class Model:
        def with_structured_output(self, schema, **kwargs):
            calls.append(kwargs.get("method", "schema"))
            return self

        async def ainvoke(self, messages):
            if len(calls) == 1:
                raise ValidationError.from_exception_data(
                    "Output", [{"type": "int_parsing", "loc": ("value",), "input": "bad"}]
                )
            return Output(value=3)

    provider = OpenAICompatProvider(model="fake", api_key="fake", base_url=None)
    monkeypatch.setattr(provider, "_build", lambda temperature: Model())
    result = await provider.structured([], Output)
    assert result.value == 3
    assert calls == ["schema", "json_mode"]
