"""OpenAI 兼容 Provider 测试。

通过替换 ``ChatOpenAI`` 为假实现，**不发起任何真实网络调用**，
覆盖消息转换、文本提取、结构化输出与错误映射四条路径。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.core.errors import AppError, LLMOutputInvalid, LLMTimeout, LLMUnavailable
from app.llm.base import ChatMessage
from app.llm.openai_compat import OpenAICompatProvider, _map_error, _text_of, _to_langchain

MESSAGES = [
    ChatMessage(role="system", content="你是助手"),
    ChatMessage(role="user", content="你好"),
]


# ============================================================ 纯函数
def test_message_conversion_keeps_roles() -> None:
    converted = _to_langchain(MESSAGES)

    assert [m.type for m in converted] == ["system", "human"]
    assert converted[1].content == "你好"


def test_text_extraction_handles_plain_string() -> None:
    assert _text_of("直接字符串") == "直接字符串"


def test_text_extraction_handles_content_blocks() -> None:
    """多模态模型返回的 content 可能是分块列表。"""

    class Block:
        def __init__(self, content: Any) -> None:
            self.content = content

    assert _text_of(
        Block([{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}])
    ) == ("第一段第二段")


# ============================================================ 错误映射
def test_timeout_maps_to_llm_timeout() -> None:
    assert isinstance(_map_error(TimeoutError("request timed out")), LLMTimeout)


def test_auth_error_is_actionable() -> None:
    class AuthenticationError(Exception):
        pass

    mapped = _map_error(AuthenticationError("Invalid API key"))

    assert isinstance(mapped, LLMUnavailable)
    assert "API Key" in mapped.message


def test_structured_failure_maps_to_output_invalid() -> None:
    class ValidationError(Exception):
        pass

    assert isinstance(_map_error(ValidationError("bad shape"), structured=True), LLMOutputInvalid)


def test_app_error_passes_through_unchanged() -> None:
    original = LLMTimeout("already mapped")

    assert _map_error(original) is original


# ============================================================ 假客户端
class _FakeChatOpenAI:
    """可编程的假 ChatOpenAI：按预设决定返回或抛错。"""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.raise_on_invoke: Exception | None = None
        self.reply: Any = "默认回复"
        self.structured_result: Any = None
        self.stream_chunks: list[Any] = []
        self.structured_schema: type | None = None

    async def ainvoke(self, messages: Any) -> Any:
        if self.raise_on_invoke:
            raise self.raise_on_invoke

        # 结构化路径直接返回结果对象；普通对话路径返回「消息对象」
        if self.structured_schema is not None:
            return self.reply

        class _Message:
            content = self.reply

        return _Message()

    def with_structured_output(self, schema: type) -> _FakeChatOpenAI:
        self.structured_schema = schema
        return self

    async def astream(self, messages: Any) -> AsyncIterator[Any]:
        if self.raise_on_invoke:
            raise self.raise_on_invoke
        for chunk in self.stream_chunks:
            yield chunk


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeChatOpenAI:
    """把模块里的 ChatOpenAI 换成可编程假实现。"""
    fake = _FakeChatOpenAI()
    monkeypatch.setattr("app.llm.openai_compat.ChatOpenAI", lambda **kwargs: fake)
    fake.kwargs_holder = fake  # type: ignore[attr-defined]
    return fake


def _provider(**overrides: Any) -> OpenAICompatProvider:
    params = {
        "model": "deepseek-chat",
        "api_key": "sk-test",
        "base_url": "https://api.deepseek.com/v1",
        "name": "deepseek",
    }
    params.update(overrides)
    return OpenAICompatProvider(**params)  # type: ignore[arg-type]


# ============================================================ 调用路径
async def test_chat_returns_text(fake_client: _FakeChatOpenAI) -> None:
    fake_client.reply = "生成的计划"

    assert await _provider().chat(MESSAGES) == "生成的计划"


async def test_chat_maps_transport_error(fake_client: _FakeChatOpenAI) -> None:
    fake_client.raise_on_invoke = TimeoutError("timed out")

    with pytest.raises(LLMTimeout):
        await _provider().chat(MESSAGES)


async def test_structured_returns_schema_instance(fake_client: _FakeChatOpenAI) -> None:
    from pydantic import BaseModel

    class Result(BaseModel):
        value: int

    fake_client.reply = Result(value=42)

    result = await _provider().structured(MESSAGES, Result)

    assert isinstance(result, Result)
    assert result.value == 42


async def test_structured_accepts_dict_and_validates(
    fake_client: _FakeChatOpenAI,
) -> None:
    """某些模型/SDK 组合会返回 dict，这里要能兜住。"""
    from pydantic import BaseModel

    class Result(BaseModel):
        value: int

    fake_client.reply = {"value": 7}

    result = await _provider().structured(MESSAGES, Result)

    assert result.value == 7


async def test_structured_invalid_payload_raises_output_invalid(
    fake_client: _FakeChatOpenAI,
) -> None:
    from pydantic import BaseModel

    class Result(BaseModel):
        value: int

    fake_client.reply = {"wrong_field": "x"}

    with pytest.raises(LLMOutputInvalid):
        await _provider().structured(MESSAGES, Result)


async def test_stream_yields_only_non_empty_chunks(fake_client: _FakeChatOpenAI) -> None:
    class _Chunk:
        def __init__(self, content: str) -> None:
            self.content = content

    fake_client.stream_chunks = [_Chunk("第一"), _Chunk(""), _Chunk("第二")]

    collected = [piece async for piece in _provider().stream(MESSAGES)]

    assert collected == ["第一", "第二"]


async def test_stream_maps_error(fake_client: _FakeChatOpenAI) -> None:
    fake_client.raise_on_invoke = ConnectionError("boom")

    with pytest.raises(LLMUnavailable):
        async for _ in _provider().stream(MESSAGES):
            pass


async def test_health_check_reports_success(fake_client: _FakeChatOpenAI) -> None:
    fake_client.reply = "pong"

    ok, message = await _provider().health_check()

    assert ok is True
    assert "deepseek" in message


async def test_health_check_reports_failure_without_raising(
    fake_client: _FakeChatOpenAI,
) -> None:
    fake_client.raise_on_invoke = TimeoutError("timed out")

    ok, message = await _provider().health_check()

    assert ok is False
    assert message  # 必须给出可读原因，而不是抛异常


# ============================================================ 构造细节
def test_local_model_gets_placeholder_key() -> None:
    """本地模型不需要 Key，但客户端要求非空，应自动填占位值。"""
    provider = _provider(api_key=None, base_url="http://localhost:11434/v1")

    assert provider._api_key  # type: ignore[attr-defined]


def test_temperature_override_does_not_mutate_default(fake_client: _FakeChatOpenAI) -> None:
    provider = _provider()
    provider._build(temperature=0.0)  # type: ignore[attr-defined]

    assert provider._temperature == 0.7  # type: ignore[attr-defined]


def test_all_errors_are_app_errors() -> None:
    for exc in (TimeoutError("t"), ConnectionError("c"), ValueError("v")):
        assert isinstance(_map_error(exc), AppError)


# ============================================================ 结构化输出降级
class _FakeRunnable:
    def __init__(self, behavior):
        self._behavior = behavior

    async def ainvoke(self, messages):
        return self._behavior(messages)


class _FallbackClient:
    """第一次调用模拟「不支持 json_schema」，第二次成功返回结构化结果。"""

    def __init__(self):
        self.mode = "json_schema"
        self.seen_methods: list[str] = []

    def with_structured_output(self, schema, **kwargs):
        method = kwargs.get("method", "json_schema")
        self.seen_methods.append(method)

        def behavior(messages):
            if method == "json_schema":
                raise ValueError("Error code: 400 - This response_format type is unavailable now")
            return {"value": 7}

        return _FakeRunnable(behavior)


async def test_falls_back_to_json_mode_when_schema_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归：DeepSeek 不支持 json_schema，必须自动退回 json_object。

    若不降级，用户会看到「模型返回格式异常」这种完全误导的提示。
    """
    from pydantic import BaseModel

    class Result(BaseModel):
        value: int

    fake = _FallbackClient()
    monkeypatch.setattr("app.llm.openai_compat.ChatOpenAI", lambda **kwargs: fake)

    result = await _provider().structured(MESSAGES, Result)

    assert result.value == 7
    assert fake.seen_methods == ["json_schema", "json_mode"]


async def test_non_fallback_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """401 之类的错误不该被当成「格式问题」并重试。"""

    class _AuthClient:
        def with_structured_output(self, schema, **kwargs):
            def behavior(messages):
                raise ValueError("Error code: 401 - Invalid API key")

            return _FakeRunnable(behavior)

    monkeypatch.setattr("app.llm.openai_compat.ChatOpenAI", lambda **kwargs: _AuthClient())

    from pydantic import BaseModel

    class Result(BaseModel):
        value: int

    with pytest.raises(LLMUnavailable) as exc_info:
        await _provider().structured(MESSAGES, Result)

    assert "API Key" in exc_info.value.message


def test_json_hint_mentions_json_and_schema() -> None:
    """json_object 模式要求提示里出现 JSON 字样，否则 LangChain 直接报错。"""
    from pydantic import BaseModel

    from app.llm.openai_compat import _with_json_hint

    class Result(BaseModel):
        value: int

    hinted = _with_json_hint(MESSAGES, Result)

    assert "JSON" in hinted[-1].content
    assert "value" in hinted[-1].content


# ============================================================ 模型列表
class _FakeHttpResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, response: _FakeHttpResponse) -> None:
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            return response

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _Client())


async def test_list_models_returns_sorted_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(
        monkeypatch,
        _FakeHttpResponse(200, {"data": [{"id": "b-model"}, {"id": "a-model"}]}),
    )

    models = await _provider().list_models()

    assert models == ["a-model", "b-model"]


async def test_list_models_reports_bad_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, _FakeHttpResponse(401, "unauthorized"))

    with pytest.raises(LLMUnavailable) as exc_info:
        await _provider().list_models()

    assert "API Key" in exc_info.value.message


async def test_list_models_handles_unsupported_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有些服务没有 /models 接口，应提示手动填写而不是报错崩掉。"""
    _patch_httpx(monkeypatch, _FakeHttpResponse(404, "not found"))

    with pytest.raises(LLMUnavailable) as exc_info:
        await _provider().list_models()

    assert "手动填写" in exc_info.value.message


async def test_list_models_handles_odd_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, _FakeHttpResponse(200, {"unexpected": []}))

    with pytest.raises(LLMUnavailable):
        await _provider().list_models()


async def test_list_models_requires_base_url() -> None:
    with pytest.raises(LLMUnavailable):
        await _provider(base_url="").list_models()
