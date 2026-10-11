#!/usr/bin/env bash
# ============================================================
#  TravelFlow AI · 一键启动（源码方式）
#
#  做三件事：检查环境 → 装依赖 → 同时拉起前后端并打开浏览器。
#  Ctrl+C 会同时停止两个服务。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$*"; }
fail() { printf "${RED}✗${NC} %s\n" "$*" >&2; exit 1; }

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3100}"
BACKEND_PID=""; FRONTEND_PID=""

cleanup() {
  echo
  info "正在停止服务…"
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  ok "已停止"
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------- 环境检查
info "检查运行环境…"

command -v curl >/dev/null 2>&1 || fail "未找到 curl，请先安装"

if ! command -v uv >/dev/null 2>&1; then
  fail "未找到 uv（Python 依赖管理器）。
  安装方式：curl -LsSf https://astral.sh/uv/install.sh | sh
  安装后重开终端再运行本脚本。"
fi

if ! command -v node >/dev/null 2>&1; then
  fail "未找到 Node.js（需要 20 以上）。
  下载地址：https://nodejs.org/"
fi

if ! command -v pnpm >/dev/null 2>&1; then
  warn "未找到 pnpm，尝试用 npm 安装…"
  npm install -g pnpm >/dev/null 2>&1 || fail "pnpm 安装失败，请手动执行：npm install -g pnpm"
fi

ok "环境检查通过（$(uv --version)，node $(node -v)）"

port_in_use() { curl -sS -o /dev/null -m 1 "http://127.0.0.1:$1/" 2>/dev/null; }

port_in_use "$BACKEND_PORT"  && fail "端口 $BACKEND_PORT 已被占用，请先关闭占用它的程序，或设置 BACKEND_PORT=其它端口"
port_in_use "$FRONTEND_PORT" && fail "端口 $FRONTEND_PORT 已被占用。若之前的服务还在运行，请先停止；也可设置 FRONTEND_PORT=其它端口"

# ---------------------------------------------------------- 后端
info "准备后端…"
cd backend
if [ ! -f .env ]; then
  cp .env.example .env
  warn "已生成 backend/.env —— 稍后可在界面「设置」里填写 API Key"
fi
uv sync --quiet
ok "后端依赖就绪"

info "启动后端（端口 $BACKEND_PORT）…"
uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" >/tmp/travelflow-backend.log 2>&1 &
BACKEND_PID=$!
cd ..

# 等待后端就绪（最多 30 秒）
for _ in $(seq 1 30); do
  if curl -sS -o /dev/null -m 2 "http://127.0.0.1:$BACKEND_PORT/api/system/health" 2>/dev/null; then
    ok "后端已就绪"
    break
  fi
  kill -0 "$BACKEND_PID" 2>/dev/null || fail "后端启动失败，日志：/tmp/travelflow-backend.log"
  sleep 1
done

# ---------------------------------------------------------- 前端
info "准备前端（首次安装依赖可能较慢）…"
cd frontend
if [ "${NODE_ENV:-}" = "production" ]; then
  pnpm install --frozen-lockfile
  NEXT_PUBLIC_API_BASE="http://127.0.0.1:$BACKEND_PORT" pnpm build
  NEXT_PUBLIC_API_BASE="http://127.0.0.1:$BACKEND_PORT" pnpm start -p "$FRONTEND_PORT" >/tmp/travelflow-frontend.log 2>&1 &
else
  pnpm install
  NEXT_PUBLIC_API_BASE="http://127.0.0.1:$BACKEND_PORT" pnpm dev -p "$FRONTEND_PORT" >/tmp/travelflow-frontend.log 2>&1 &
fi
FRONTEND_PID=$!
cd ..

info "等待前端就绪…"
for _ in $(seq 1 60); do
  if curl -sS -o /dev/null -m 2 "http://127.0.0.1:$FRONTEND_PORT/" 2>/dev/null; then
    ok "前端已就绪"
    break
  fi
  kill -0 "$FRONTEND_PID" 2>/dev/null || fail "前端启动失败，日志：/tmp/travelflow-frontend.log"
  sleep 1
done

# ---------------------------------------------------------- 打开浏览器
URL="http://127.0.0.1:$FRONTEND_PORT"
echo
ok "TravelFlow AI 已启动：$URL"
echo "  后端接口文档：http://127.0.0.1:$BACKEND_PORT/docs"
echo "  日志：/tmp/travelflow-backend.log , /tmp/travelflow-frontend.log"
echo "  按 Ctrl+C 停止全部服务"
echo

if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then open "$URL" >/dev/null 2>&1 || true
fi

wait
