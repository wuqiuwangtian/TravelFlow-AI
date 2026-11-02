# 发布流程（维护者）

本项目采用「源码 + 静态项目主页」发布：不打预构建二进制、不推容器镜像、不发布 PyPI/npm 包。发布版本以 Git tag 和 GitHub Release 为准。

## 发布前

1. 确认 `main` 与远端同步、工作区干净、目标提交的 CI 通过。
2. 同步版本号：`backend/pyproject.toml`、`frontend/package.json`、`CHANGELOG.md` 顶部版本段必须一致；在 CHANGELOG 写清新增能力、修复、已知限制和验证边界。
3. 检查 LICENSE、THIRD_PARTY_NOTICES、README 徽章与链接；更新受影响的用户文档与内部状态页。
4. 本地与 CI 运行：

```sh
node scripts/check-docs-links.mjs
node scripts/check-release.mjs --tag vX.Y.Z
cd backend && uv sync --frozen && uv run ruff format --check app tests && uv run ruff check app tests && uv run mypy app && uv run pytest -q
cd ../frontend && pnpm install --frozen-lockfile && pnpm typecheck && pnpm lint && pnpm check:design && pnpm build
cd .. && PLAYWRIGHT_BROWSERS_PATH=/tmp/travelflow-browser pnpm --dir frontend test:e2e
```

## 打标签与创建 Release

发布说明从 CHANGELOG 对应版本段生成，不手写第二份版本清单：

```sh
node scripts/check-release.mjs --notes --tag vX.Y.Z > /tmp/release-notes.md
# 可加项目主页、截图、许可链接后作为 Release 说明
gh release create vX.Y.Z --repo wuqiuwangtian/TravelFlow-AI --draft --target main \
  --title "TravelFlow AI vX.Y.Z" --notes-file /tmp/release-notes.md
gh release edit vX.Y.Z --draft=false --repo wuqiuwangtian/TravelFlow-AI
```

`.github/workflows/release.yml` 在 `v*` tag 推送时校验元数据并创建 Release；若 Release 已存在则跳过，可安全重跑。`.github/workflows/pages.yml` 在 `main` 更新后部署 `site/`。

## 发布后

- 核对 License API 为 MIT、Release 可见、tag 指向预期提交、Pages 与截图资源返回 200。
- 更新 `PROJECT_STATUS.md` 的接续状态和发布说明中的后续事项。
- 观察 Issues、安全报告和 Dependabot PR；依赖升级单独评审，不与发布说明混在一起。

## 失败与回退

- 已发布 tag 不移动、不强制重写；发现问题发下一个 patch（如 v1.0.1）。
- Release 说明有误可编辑 Release 正文（不改 tag 内容）；草稿阶段可删除重来。
- Pages 部署失败先看 Actions 日志与仓库 Pages 设置；不影响源码发布。
- 更新 `.github/workflows/` 需要具备 `workflow` 权限的凭据；当前环境可使用已授权的 SSH remote 推送，避免 OAuth token 的 workflow scope 限制。

## 边界

- 不承诺托管服务、在线试用、SLA 或第三方模型／检索／地图的持续可用性。
- 不把截图、样例数据或自动化通过当作真实旅行验收。
