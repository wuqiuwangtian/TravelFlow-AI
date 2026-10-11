@echo off
REM ============================================================
REM  TravelFlow AI - 一键启动（Windows 源码方式）
REM  做三件事：检查环境 -> 装依赖 -> 同时拉起前后端并打开浏览器
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%BACKEND_PORT%"=="" set BACKEND_PORT=8000
if "%FRONTEND_PORT%"=="" set FRONTEND_PORT=3100

echo.
echo TravelFlow AI 启动中...
echo.

REM ---------------- 环境检查 ----------------
where uv >nul 2>nul
if errorlevel 1 (
  echo [X] 未找到 uv（Python 依赖管理器）
  echo     安装方式：powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo     安装后请重开本窗口再运行。
  pause
  exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
  echo [X] 未找到 Node.js（需要 20 以上），下载：https://nodejs.org/
  pause
  exit /b 1
)

where pnpm >nul 2>nul
if errorlevel 1 (
  echo [!] 未找到 pnpm，正在用 npm 安装...
  call npm install -g pnpm
)

echo [OK] 环境检查通过

REM ---------------- 后端 ----------------
echo.
echo [*] 准备后端...
cd backend
if not exist .env (
  copy .env.example .env >nul
  echo [!] 已生成 backend\.env -- 稍后可在界面「设置」里填写 API Key
)
call uv sync --quiet
echo [OK] 后端依赖就绪

echo [*] 启动后端（端口 %BACKEND_PORT%）...
REM 按实际前端端口注入跨域白名单，避免严格模式下「连不上后端」
set APP_CORS_ORIGINS=http://localhost:%FRONTEND_PORT%,http://127.0.0.1:%FRONTEND_PORT%
start "TravelFlow Backend" cmd /k "set APP_CORS_ORIGINS=%APP_CORS_ORIGINS% && uv run uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%"
cd ..

REM ---------------- 前端 ----------------
echo.
echo [*] 准备前端（首次安装依赖可能较慢）...
cd frontend
call pnpm install
set NEXT_PUBLIC_API_BASE=http://127.0.0.1:%BACKEND_PORT%
echo [*] 启动前端（端口 %FRONTEND_PORT%）...
start "TravelFlow Frontend" cmd /k "pnpm dev -p %FRONTEND_PORT%"
cd ..

REM ---------------- 打开浏览器 ----------------
timeout /t 8 /nobreak >nul
start "" "http://127.0.0.1:%FRONTEND_PORT%"

echo.
echo [OK] TravelFlow AI 已启动
echo      界面地址：  http://127.0.0.1:%FRONTEND_PORT%
echo      接口文档：  http://127.0.0.1:%BACKEND_PORT%/docs
echo.
echo      两个服务分别在独立窗口中运行，关闭对应窗口即可停止。
echo.
pause
