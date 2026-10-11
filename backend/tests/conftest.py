"""pytest 公共夹具。

所有测试都使用**独立的临时数据库**，绝不触碰真实的 ``data/travelflow.db``。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """隔离配置：临时数据库 + 清空密钥，保证测试互不影响。"""
    monkeypatch.setenv("SESSION_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """每个测试使用全新的应用实例。"""
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()
