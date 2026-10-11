"""服务商列表测试 —— 守护「不硬编码易变信息」这一设计原则（规划 §19.8）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

# 这些词语代表第三方会随时变更的信息，绝不应出现在服务商列表里
BANNED_WORDS = ("免费", "额度", "每天", "每月", "价格", "优惠", "credits", "¥", "$")


def test_providers_list_is_populated(client: TestClient) -> None:
    body = client.get("/api/system/providers").json()

    assert len(body["llm"]) >= 8
    assert len(body["search"]) >= 5
    llm_ids = {p["id"] for p in body["llm"]}
    assert {"deepseek", "ollama", "lmstudio"} <= llm_ids  # 云端 + 本地都有
    assert {"tavily", "ddg", "searxng"} <= {p["id"] for p in body["search"]}


def test_providers_contain_no_volatile_information(client: TestClient) -> None:
    """不得出现额度/价格等易变信息 —— 由测试守护，避免日后被顺手加回来。"""
    text = client.get("/api/system/providers").text

    for word in BANNED_WORDS:
        assert word not in text, f"服务商列表不应包含易变信息：{word}"


def test_every_provider_links_to_official_docs(client: TestClient) -> None:
    """每个服务商都必须给出官方文档链接，用户才能自行核实。"""
    body = client.get("/api/system/providers").json()

    for group in ("llm", "search"):
        for item in body[group]:
            assert item["docs_url"].startswith("http"), item
