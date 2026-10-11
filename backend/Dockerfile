# ============================================================
#  TravelFlow AI · 后端镜像
#  多阶段构建：依赖层与代码层分离，改代码不必重装依赖
# ============================================================
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# uv：官方镜像，比 pip 快很多，且能用 uv.lock 精确复现
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

WORKDIR /app

# ---------- 依赖层（仅当依赖变化时才重建）----------
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---------- 运行层 ----------
FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
COPY app ./app

ENV PATH="/app/.venv/bin:$PATH" \
    SESSION_DB_PATH=/app/data/travelflow.db \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

RUN mkdir -p /app/data

EXPOSE 8000

# 健康检查：/api/system/health 不依赖任何外部服务，可放心用作探针
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/system/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
