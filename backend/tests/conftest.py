"""pytest 公共夹具。

**两条隔离底线**（都踩过坑）：

1. 独立临时数据库，绝不触碰真实的 ``data/travelflow.db``；
2. **绝不读到 ``backend/.env``** —— 它里面有用户的真实 API Key。
   早期版本只 ``delenv("LLM_API_KEY")``，pydantic 会**回落到 .env**，
   于是测试真的拿着用户的 Key 去调模型了（既浪费额度，又让结果不确定）。
   现在通过 ``chdir`` 到临时目录，让相对路径的 ``.env`` 根本不存在。
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
    """每个测试都在「临时目录 + 无 .env + 无密钥」的干净环境中运行。"""
    # ① 切到临时目录：Settings 的 env_file=".env" 是相对路径，这样就读不到真实配置
    monkeypatch.chdir(tmp_path)

    # ② 数据库用绝对路径（chdir 之后相对路径会指错地方）
    monkeypatch.setenv("SESSION_DB_PATH", str(tmp_path / "test.db"))

    # ③ 显式清空密钥（双保险）
    for key in ("LLM_API_KEY", "SEARCH_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)

    # ④ 配置文件也指向临时位置，避免被测代码写回真实 .env
    monkeypatch.setenv("APP_ENV_FILE", str(tmp_path / ".env"))

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
