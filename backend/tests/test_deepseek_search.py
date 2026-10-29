"""DeepSeek Search 的协议、引用、安全边界与配置复用。"""

import json

import httpx
import pytest

from app.agents.nodes.researcher import make_researcher_node
from app.core.config import Settings, get_settings
from app.core.errors import ConfigError, SearchUnavailable
from app.domain.models import Requirements
from app.search.deepseek_provider import DeepSeekSearchProvider
from app.search.factory import create_search_provider

ENDPOINT = "https://api.deepseek.com/v1/tools/search"


def page(**updates):
    return (
        dict(
            title="博物馆预约说明",
            url="https://example.com/info?from=original",
            summary="完整摘要：需预约",
            snippet="短摘要",
            content="",
            date="2026-10-25T00:00:00Z",
        )
        | updates
    )


async def test_protocol_retains_original_citation_url_and_prefers_summary(respx_mock):
    route = respx_mock.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json={"results": [page()]})
    )
    result = await DeepSeekSearchProvider("isolated-search-key").search(
        "苏州博物馆 预约", max_results=3
    )
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer isolated-search-key"
    assert json.loads(request.content) == {
        "query": "苏州博物馆 预约",
        "count": 3,
        "need_content": False,
    }
    assert len(result) == 1
    assert result[0].url == page()["url"]
    assert result[0].content == page()["summary"]
    assert result[0].provider == "deepseek"
    assert result[0].published_at.year == 2026


async def test_domains_fit_query_limit_and_empty_filter_falls_back_once(respx_mock):
    route = respx_mock.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(200, json={"results": [page()]}),
        ]
    )
    results = await DeepSeekSearchProvider("isolated-key").search(
        "苏州" * 100,
        max_results=100,
        domains=["12306.cn", "ctrip.com"],
    )
    queries = [json.loads(c.request.content) for c in route.calls]
    assert len(queries) == 2
    assert all(len(q["query"]) == 128 and q["count"] == 50 for q in queries)
    assert queries[0]["query"].endswith("site:12306.cn OR site:ctrip.com")
    assert "site:" not in queries[1]["query"]
    assert results[0].url == page()["url"]


async def test_empty_search_is_not_retried_without_domain_filter(respx_mock):
    route = respx_mock.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"results": []}))
    assert await DeepSeekSearchProvider("key").search("苏州 预约") == []
    assert len(route.calls) == 1


async def test_blank_query_never_calls_service(respx_mock):
    with pytest.raises(SearchUnavailable, match="关键词不能为空"):
        await DeepSeekSearchProvider("key").search("   ")
    assert not respx_mock.calls


async def test_missing_summaries_dates_and_invalid_links_are_handled(respx_mock):
    respx_mock.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    page(url="javascript:alert(1)"),
                    page(url="https://user:password@example.com/"),
                    page(summary=None, date="not-a-date"),
                    page(),
                    page(
                        url="https://example.com/other", summary="", snippet="", content="正文备用"
                    ),
                ]
            },
        )
    )
    result = await DeepSeekSearchProvider("key").search("预约")
    assert len(result) == 2
    assert result[0].content == "短摘要"
    assert result[0].published_at is None
    assert result[1].content == "正文备用"


@pytest.mark.parametrize(
    "status,message",
    [
        (401, "鉴权失败"),
        (402, "余额不足"),
        (403, "访问被拒绝"),
        (429, "过于频繁"),
        (500, "暂时不可用"),
        (400, "暂时不可用"),
    ],
)
async def test_errors_are_actionable_never_echo_body_and_do_not_retry(respx_mock, status, message):
    route = respx_mock.post(ENDPOINT).mock(
        return_value=httpx.Response(status, text="sensitive-provider-response")
    )
    with pytest.raises(SearchUnavailable, match=message) as failure:
        await DeepSeekSearchProvider("isolated-key").search("苏州", domains=["12306.cn"])
    assert len(route.calls) == 1
    assert "sensitive-provider-response" not in failure.value.message + (failure.value.detail or "")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"results": None},
        {"results": "bad"},
        [page()],
        {"results": [page(url="file:///tmp/test")]},
    ],
)
async def test_malformed_payload_is_not_mistaken_for_zero_hits(respx_mock, body):
    respx_mock.post(ENDPOINT).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(SearchUnavailable):
        await DeepSeekSearchProvider("key").search("苏州")


async def test_timeout_and_invalid_json_are_safe(respx_mock):
    route = respx_mock.post(ENDPOINT).mock(side_effect=httpx.ReadTimeout("private-debug-content"))
    with pytest.raises(SearchUnavailable) as failure:
        await DeepSeekSearchProvider("key").search("苏州")
    assert failure.value.detail == "ReadTimeout"
    route.mock(return_value=httpx.Response(200, text="private-invalid-json"))
    with pytest.raises(SearchUnavailable) as failure:
        await DeepSeekSearchProvider("key").search("苏州")
    assert "private-invalid-json" not in (failure.value.detail or "")


@pytest.mark.parametrize(
    "llm_provider,base_url,allowed",
    [
        ("deepseek", None, True),
        ("deepseek", "https://api.deepseek.com/v1", True),
        ("deepseek", "https://api.deepseek.com:443", True),
        ("deepseek", "https://proxy.example/v1", False),
        ("openai", None, False),
        ("deepseek", "https://api.deepseek.com.evil.example/v1", False),
        ("deepseek", "http://api.deepseek.com/v1", False),
        ("deepseek", "https://user@api.deepseek.com/v1", False),
        ("deepseek", "https://api.deepseek.com:8000/v1", False),
    ],
)
def test_model_key_reuse_is_limited_to_official_deepseek(llm_provider, base_url, allowed):
    settings = Settings(
        _env_file=None,
        search_provider="deepseek",
        search_api_key=None,
        llm_provider=llm_provider,
        llm_base_url=base_url,
        llm_api_key="isolated-model-key",
    )
    assert settings.search_ready is allowed
    assert bool(settings.resolved_search_api_key) is allowed
    if allowed:
        assert isinstance(create_search_provider(settings), DeepSeekSearchProvider)
        assert not any("SEARCH_API_KEY" in w for w in settings.startup_warnings())
    else:
        with pytest.raises(ConfigError):
            create_search_provider(settings)


def test_dedicated_key_has_priority_and_other_searches_do_not_borrow_model_key():
    settings = Settings(
        _env_file=None,
        search_provider="deepseek",
        search_api_key="dedicated",
        llm_provider="deepseek",
        llm_api_key="model",
    )
    assert settings.resolved_search_api_key == "dedicated"
    settings = settings.model_copy(update={"search_provider": "bocha", "search_api_key": None})
    assert not settings.search_ready
    assert settings.resolved_search_api_key is None


async def test_system_exposes_readiness_without_keys_and_search_connection_works(
    environment, respx_mock
):
    client, _, _, _ = environment
    settings = Settings(
        _env_file=None,
        search_provider="deepseek",
        search_api_key=None,
        llm_provider="deepseek",
        llm_api_key="isolated-model-private",
    )
    client._transport.app.dependency_overrides[get_settings] = lambda: settings
    for url in ["/api/system/config", "/api/system/settings"]:
        response = await client.get(url)
        assert response.status_code == 200
        assert "isolated-model-private" not in response.text
        if url.endswith("config"):
            assert response.json()["search"] == {
                "provider": "deepseek",
                "has_key": True,
                "ready": True,
            }
        else:
            assert response.json()["search_has_key"]
    providers = (await client.get("/api/system/providers")).json()["search"]
    assert any(p["id"] == "deepseek" and p["requires_api_key"] for p in providers)
    respx_mock.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"results": [page()]}))
    test = await client.post("/api/system/test-search")
    assert test.json()["ok"]


async def test_three_bucket_research_uses_same_reference_chain(respx_mock):
    respx_mock.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"results": [page()]}))
    node = make_researcher_node(DeepSeekSearchProvider("key"))
    result = await node({"requirements": Requirements(origin="上海", destination="苏州")})
    research = result["research"]
    assert all([research.transport, research.attractions, research.reviews])
    assert all(
        c.url == page()["url"] and c.provider == "deepseek" for c in research.build_citations()
    )
    assert "完整摘要" in research.numbered_sources()
    assert page()["url"] not in research.numbered_sources()


@pytest.mark.parametrize(
    "status,message", [(401, "鉴权失败"), (402, "余额不足"), (429, "过于频繁")]
)
async def test_planning_keeps_provider_error_guidance(respx_mock, status, message):
    route = respx_mock.post(ENDPOINT).mock(return_value=httpx.Response(status, text="private"))
    node = make_researcher_node(DeepSeekSearchProvider("key"))
    with pytest.raises(SearchUnavailable, match=message) as failure:
        await node({"requirements": Requirements(origin="上海", destination="苏州")})
    assert len(route.calls) == 3
    assert "private" not in failure.value.message
    assert "代理" not in failure.value.message
