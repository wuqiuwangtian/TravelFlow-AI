"""把配置写回 .env 的测试。

这个模块直接改动用户的配置文件，**必须可靠**：
写坏一次就可能让用户的服务起不来，因此逐条覆盖。
"""

from __future__ import annotations

from pathlib import Path

from app.core.env_writer import FIELD_TO_ENV, write_env


def test_creates_file_when_missing(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    changed = write_env(env, {"LLM_API_KEY": "sk-abc"})

    assert changed == ["LLM_API_KEY"]
    assert "LLM_API_KEY=sk-abc" in env.read_text(encoding="utf-8")


def test_updates_existing_key_in_place(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=old-model\nAPP_PORT=8000\n", encoding="utf-8")

    changed = write_env(env, {"LLM_MODEL": "new-model"})

    assert changed == ["LLM_MODEL"]
    text = env.read_text(encoding="utf-8")
    assert "LLM_MODEL=new-model" in text
    assert "old-model" not in text
    assert "APP_PORT=8000" in text  # 其它键不受影响


def test_preserves_comments_and_order(tmp_path: Path) -> None:
    """用户手写的注释与顺序必须保留 —— 这是「读-改-写」而非重写整份文件的理由。"""
    env = tmp_path / ".env"
    env.write_text(
        "# 我的配置\nLLM_PROVIDER=deepseek\n\n# 下面是检索\nSEARCH_PROVIDER=tavily\n",
        encoding="utf-8",
    )

    write_env(env, {"LLM_PROVIDER": "openai"})

    text = env.read_text(encoding="utf-8")
    assert "# 我的配置" in text
    assert "# 下面是检索" in text
    assert text.index("LLM_PROVIDER") < text.index("SEARCH_PROVIDER")
    assert "LLM_PROVIDER=openai" in text


def test_appends_new_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("APP_PORT=8000\n", encoding="utf-8")

    changed = write_env(env, {"SEARCH_API_KEY": "tvly-x"})

    assert changed == ["SEARCH_API_KEY"]
    text = env.read_text(encoding="utf-8")
    assert "APP_PORT=8000" in text
    assert "SEARCH_API_KEY=tvly-x" in text
    assert text.index("APP_PORT") < text.index("SEARCH_API_KEY")


def test_reports_empty_when_value_unchanged(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=deepseek-chat\n", encoding="utf-8")

    changed = write_env(env, {"LLM_MODEL": "deepseek-chat"})

    assert changed == [], "值没变就不该报告为「已修改」"


def test_handles_empty_value(tmp_path: Path) -> None:
    """清空 API Key 是合法操作（例如切换到本地模型）。"""
    env = tmp_path / ".env"
    env.write_text("LLM_API_KEY=sk-old\n", encoding="utf-8")

    write_env(env, {"LLM_API_KEY": ""})

    assert "LLM_API_KEY=\n" in env.read_text(encoding="utf-8")


def test_multiple_updates_in_one_pass(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=deepseek\nSEARCH_PROVIDER=tavily\n", encoding="utf-8")

    changed = write_env(env, {"LLM_PROVIDER": "ollama", "SEARCH_PROVIDER": "ddg"})

    assert sorted(changed) == ["LLM_PROVIDER", "SEARCH_PROVIDER"]


def test_only_whitelisted_fields_are_writable() -> None:
    """白名单是安全边界：不能通过接口改写任意环境变量。"""
    assert set(FIELD_TO_ENV) == {
        "llm_provider",
        "llm_model",
        "llm_api_key",
        "llm_base_url",
        "search_provider",
        "search_api_key",
        "searxng_base_url",
        "default_detail_level",
    }
    assert "app_host" not in FIELD_TO_ENV
    assert "session_db_path" not in FIELD_TO_ENV
