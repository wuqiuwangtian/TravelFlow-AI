"""安全回归测试。

每一条都对应一次真实的审查发现 —— 目的是让它们**不可能悄悄复活**。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.models import Citation
from app.export.common import is_safe_url, linkify_citations, safe_url
from app.main import MAX_REQUEST_BYTES


# ============================================================ 配置注入
@pytest.mark.parametrize(
    "payload",
    [
        {"llm_model": "ok\nAPP_HOST=0.0.0.0"},
        {"llm_model": "ok\r\nAPP_CORS_STRICT=false"},
        {"search_api_key": "key\nSESSION_DB_PATH=/etc/passwd"},
        {"llm_provider": "x\x00y"},
        {"default_detail_level": "L2\tstandard"},
    ],
)
def test_newline_injection_is_rejected(client: TestClient, payload: dict[str, str]) -> None:
    """★ 换行注入：曾可借此往 .env 里塞入任意配置行，绕过字段白名单。"""
    response = client.put("/api/system/settings", json=payload)

    assert response.status_code == 400, f"{payload} 竟然被接受了"


def test_injection_does_not_touch_env_file(tmp_path: Path, monkeypatch) -> None:
    """即使被拒，也不能留下半成品写入。"""
    from app.core.env_writer import write_env
    from app.core.errors import InvalidRequest

    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=deepseek-flash\n", encoding="utf-8")

    with pytest.raises(InvalidRequest):
        write_env(env_file, {"LLM_MODEL": "x\nAPP_HOST=0.0.0.0"})

    content = env_file.read_text(encoding="utf-8")
    assert "APP_HOST" not in content
    assert "deepseek-flash" in content  # 原内容保持不变


# ============================================================ 接口地址校验
@pytest.mark.parametrize(
    "url",
    [
        "http://attacker.example.com/v1",  # http 到外部主机 → 明文外送 Key
        "ftp://example.com",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_unsafe_base_url_is_rejected(client: TestClient, url: str) -> None:
    """★ 把 base_url 指向攻击者服务器，会导致用户 API Key 被外送。"""
    response = client.put("/api/system/settings", json={"llm_base_url": url})

    assert response.status_code == 400, f"{url} 竟然被接受了"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.deepseek.com/v1",
        "http://localhost:11434/v1",  # 本地模型允许 http
        "http://127.0.0.1:8000/v1",
        "http://host.docker.internal:11434/v1",
    ],
)
def test_safe_base_url_is_accepted(client: TestClient, url: str) -> None:
    response = client.put("/api/system/settings", json={"llm_base_url": url})

    assert response.status_code == 200


# ============================================================ 请求体上限
def test_oversized_body_is_rejected(client: TestClient) -> None:
    """★ 没有这道闸时，FastAPI 会先把整个 body 读进内存（可打爆服务）。"""
    huge = "x" * (MAX_REQUEST_BYTES + 1024)

    response = client.post(
        "/api/chat",
        content=huge,
        headers={"Content-Type": "application/json", "Content-Length": str(len(huge))},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ============================================================ 导出链接协议
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a", True),
        ("http://example.com/a", True),
        ("javascript:alert(1)", False),
        ("data:text/html,<script>alert(1)</script>", False),
        ("file:///etc/passwd", False),
        ("vbscript:msgbox(1)", False),
        ("", False),
        ("/relative/path", False),
        (None, False),
    ],
)
def test_url_scheme_whitelist(url: str | None, expected: bool) -> None:
    """★ 检索结果是外部数据，可能带回 javascript:/data: 链接。"""
    assert is_safe_url(url) is expected


def test_malicious_citation_becomes_plain_text() -> None:
    """恶意链接应退化为纯文本编号，而不是可点击的链接。"""
    citations = [
        Citation(
            index=1,
            title="恶意来源",
            url="javascript:alert(document.cookie)",
            provider="test",
            retrieved_at=datetime(2026, 10, 12),
        )
    ]

    linked, _ = linkify_citations("参考 [1] 的内容", citations)

    assert "javascript:" not in linked
    assert "[1]" in linked


def test_safe_citation_is_linked() -> None:
    citations = [
        Citation(
            index=1,
            title="正常来源",
            url="https://example.com/a",
            provider="test",
            retrieved_at=datetime(2026, 10, 12),
        )
    ]

    linked, _ = linkify_citations("参考 [1] 的内容", citations)

    assert "https://example.com/a" in linked


def test_safe_url_logs_and_blanks_unsafe() -> None:
    assert safe_url("https://ok.example.com") == "https://ok.example.com"
    assert safe_url("javascript:x") == ""
