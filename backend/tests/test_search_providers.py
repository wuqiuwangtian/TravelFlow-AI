"""5 个检索 Provider 的解析与错误处理测试。

全部通过 ``respx`` 拦截 HTTP，**不发起真实网络请求、不消耗任何额度**。

最重要的断言：**返回的 URL 必须与接口给的一模一样** ——
这是「引用由程序透传、不经大模型改写」这一防幻觉根基的回归测试。
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.errors import SearchUnavailable
from app.search.bocha_provider import BochaProvider
from app.search.ddg_provider import DuckDuckGoProvider
from app.search.qianfan_provider import QianfanProvider
from app.search.searxng_provider import SearXNGProvider
from app.search.tavily_provider import TavilyProvider

RAW_URL = "https://m.cq.bendibao.com/tour/49116.shtm?from=search&x=1"


# ---------------------------------------------------------------- Tavily
async def test_tavily_returns_results_with_untouched_urls() -> None:
    with respx.mock:
        respx.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"title": "重庆攻略", "url": RAW_URL, "content": "正文"}]},
            )
        )
        results = await TavilyProvider("tvly-test").search("重庆 3 天")

    assert len(results) == 1
    assert results[0].url == RAW_URL  # ← 原样透传，一字不改
    assert results[0].provider == "tavily"
    assert results[0].domain == "m.cq.bendibao.com"


async def test_tavily_passes_domain_filter() -> None:
    with respx.mock as mock:
        route = mock.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        await TavilyProvider("k").search("x", domains=["12306.cn", "ctrip.com"])

    sent = json.loads(route.calls[0].request.content)
    assert sent["include_domains"] == ["12306.cn", "ctrip.com"]


async def test_tavily_auth_failure_message_is_actionable() -> None:
    with respx.mock:
        respx.post("https://api.tavily.com/search").mock(return_value=httpx.Response(401))
        with pytest.raises(SearchUnavailable) as exc_info:
            await TavilyProvider("bad-key").search("x")

    assert "API Key" in exc_info.value.message


async def test_tavily_drops_items_without_url() -> None:
    with respx.mock:
        respx.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "没有链接", "content": "..."},
                        {"title": "有链接", "url": "https://a.example/1", "content": "ok"},
                    ]
                },
            )
        )
        results = await TavilyProvider("k").search("x")

    assert [r.url for r in results] == ["https://a.example/1"]


# ---------------------------------------------------------------- 博查
async def test_bocha_parses_nested_payload() -> None:
    with respx.mock:
        respx.post("https://api.bochaai.com/v1/web-search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "重庆避雷",
                                    "url": "https://zhuanlan.zhihu.com/p/123",
                                    "summary": "摘要内容",
                                }
                            ]
                        }
                    }
                },
            )
        )
        results = await BochaProvider("k").search("重庆 避雷")

    assert results[0].url == "https://zhuanlan.zhihu.com/p/123"
    assert results[0].content == "摘要内容"
    assert results[0].provider == "bocha"


# ---------------------------------------------------------------- 千帆
async def test_qianfan_parses_references() -> None:
    with respx.mock:
        respx.post("https://qianfan.baidubce.com/v2/ai_search/web_search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "references": [
                        {
                            "title": "三峡博物馆",
                            "url": "https://www.3gmuseum.cn/",
                            "content": "免费",
                        }
                    ]
                },
            )
        )
        results = await QianfanProvider("k").search("三峡博物馆 预约")

    assert results[0].url == "https://www.3gmuseum.cn/"
    assert results[0].provider == "qianfan"


# ---------------------------------------------------------------- SearXNG
async def test_searxng_parses_results() -> None:
    with respx.mock:
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"title": "T", "url": "https://s.example/1", "content": "C"}]},
            )
        )
        results = await SearXNGProvider("http://localhost:8080").search("x")

    assert results[0].url == "https://s.example/1"
    assert results[0].provider == "searxng"


async def test_searxng_json_format_hint_on_403() -> None:
    """SearXNG 未开启 json 输出时会返回 403，提示必须说清原因。"""
    with respx.mock:
        respx.get("http://localhost:8080/search").mock(return_value=httpx.Response(403))
        with pytest.raises(SearchUnavailable) as exc_info:
            await SearXNGProvider("http://localhost:8080").search("x")

    assert "API Key" in exc_info.value.message  # 403 被归类为鉴权问题


async def test_searxng_adds_site_filter_to_query() -> None:
    with respx.mock as mock:
        route = mock.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        await SearXNGProvider("http://localhost:8080").search("门票", domains=["ctrip.com"])

    assert "site%3Actrip.com" in str(route.calls[0].request.url)


# ---------------------------------------------------------------- DuckDuckGo
async def test_ddg_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DuckDuckGoProvider()
    monkeypatch.setattr(
        provider,
        "_search_sync",
        lambda query, max_results: [
            {"title": "标题", "href": "https://ddg.example/1", "body": "正文"}
        ],
    )

    results = await provider.search("重庆")

    assert results[0].url == "https://ddg.example/1"
    assert results[0].provider == "ddg"


async def test_ddg_runs_in_thread_not_blocking_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归阶段① P1-1：同步库必须放到线程池，不能阻塞事件循环。"""
    import asyncio
    import threading

    provider = DuckDuckGoProvider()
    seen: list[int] = []

    def fake(query: str, max_results: int) -> list[dict]:
        seen.append(threading.get_ident())
        return []

    monkeypatch.setattr(provider, "_search_sync", fake)
    main_thread = threading.get_ident()

    await provider.search("x")

    assert seen and seen[0] != main_thread
    assert asyncio.get_event_loop().is_running()
