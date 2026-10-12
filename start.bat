@echo off
REM 切到 UTF-8 代码页，保证中文提示不乱码
chcp 65001 >nul
REM ============================================================
REM  TravelFlow AI - 一键启动（Windows 源码方式）
REM  做三件事：检查环境 -> 装依赖 -> 拉起前后端并打开浏览器
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%BACKEND_PORT%"=="" set BACKEND_PORT=8000
if "%FRONTEND_PORT%"=="" set FRONTEND_PORT=3100

REM 统一用 localhost：与 start.sh / README 保持一致
set FRONTEND_URL=http://localhost:%FRONTEND_PORT%
set BACKEND_URL=http://localhost:%BACKEND_PORT%

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
  where pnpm >nul 2>nul
  if errorlevel 1 (
    echo [X] pnpm 安装失败，请手动执行：npm install -g pnpm
    pause
    exit /b 1
  )
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
echo [*] 准备前端（首次安装依赖可能较慢，请耐心等待）...
cd frontend
call pnpm install
set NEXT_PUBLIC_API_BASE=%BACKEND_URL%
echo [OK] 前端依赖就绪
echo [*] 启动前端（端口 %FRONTEND_PORT%）...
start "TravelFlow Frontend" cmd /k "set NEXT_PUBLIC_API_BASE=%BACKEND_URL% && pnpm dev -p %FRONTEND_PORT%"
cd ..

REM ---------------- 等待服务就绪 ----------------
REM 不用固定等待：首次启动前端可能要几十秒，等太短会打开一个空白页
echo.
echo [*] 等待服务就绪...

set /a WAITED=0
:wait_backend
curl -s -o nul -m 2 "%BACKEND_URL%/api/system/health" >nul 2>nul
if not errorlevel 1 goto backend_ok
set /a WAITED+=2
if !WAITED! GEQ 60 goto backend_slow
timeout /t 2 /nobreak >nul
goto wait_backend

:backend_slow
echo [!] 后端启动较慢，稍后请手动访问 %BACKEND_URL%/docs 确认
goto wait_frontend_start

:backend_ok
echo [OK] 后端已就绪

:wait_frontend_start
set /a WAITED=0
:wait_frontend
curl -s -o nul -m 2 "%FRONTEND_URL%/" >nul 2>nul
if not errorlevel 1 goto frontend_ok
set /a WAITED+=2
if !WAITED! GEQ 90 goto frontend_slow
timeout /t 2 /nobreak >nul
goto wait_frontend

:frontend_slow
echo [!] 前端启动较慢（首次编译属正常），请稍候再刷新页面
goto opened

:frontend_ok
echo [OK] 前端已就绪

:opened
REM ---------------- 打开浏览器 ----------------
start "" "%FRONTEND_URL%"

echo.
echo [OK] TravelFlow AI 已启动
echo      界面地址：  %FRONTEND_URL%
echo      接口文档：  %BACKEND_URL%/docs
echo.
echo      两个服务分别在独立窗口中运行，关闭对应窗口即可停止。
echo.
pause
