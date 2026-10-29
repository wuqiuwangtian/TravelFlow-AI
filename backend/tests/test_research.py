"""检索缺口不能被误报为网络故障或完整资料。"""

import pytest
from ddgs.exceptions import DDGSException, TimeoutException

from app.agents.nodes.researcher import make_researcher_node
from app.core.errors import SearchUnavailable
from app.domain.models import Requirements
from app.search.base import SearchResult
from app.search.ddg_provider import DuckDuckGoProvider


@pytest.mark.parametrize(
    "error",
    [
        DDGSException("No results found."),
        TimeoutException("timeout"),
        DDGSException("backend failed"),
    ],
)
async def test_ddg_distinguishes_zero_hits_from_provider_failure(monkeypatch, error):
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, *args, **kwargs):
            raise error

    monkeypatch.setattr("ddgs.DDGS", Client)
    provider = DuckDuckGoProvider()
    if type(error) is DDGSException and str(error) == "No results found.":
        assert await provider.search("苏州博物馆") == []
    else:
        with pytest.raises(SearchUnavailable):
            await provider.search("苏州博物馆")


class Search:
    name = "test"
    requires_api_key = False

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    async def search(self, *args, **kwargs):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize("outcomes", [[[], [], []], [SearchUnavailable("失败"), [], []]])
async def test_research_without_any_usable_sources_stops_before_planning(outcomes):
    node = make_researcher_node(Search(outcomes))
    with pytest.raises(SearchUnavailable, match="暂未检索到可用"):
        await node({"requirements": Requirements(destination="苏州")})


async def test_partial_empty_searches_preserve_citations_and_report_gaps(monkeypatch):
    notices = []
    monkeypatch.setattr("app.agents.nodes.researcher.emit_notice", notices.append)
    source = SearchResult(
        title="真实资料", url="https://example.com/info", content="摘要", provider="test"
    )
    node = make_researcher_node(Search([[source], [], SearchUnavailable("失败")]))
    result = await node({"requirements": Requirements(destination="苏州")})
    assert [c.url for c in result["research"].build_citations()] == [source.url]
    assert len(notices) == 1
    assert "2 路检索未取得可用资料" in notices[0]
