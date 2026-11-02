## 变更内容

<!-- 简要说明做了什么、为什么这样做 -->

## 关联 Issue

<!-- 例如 Closes #123；没有关联 Issue 时写“无” -->

## 验证

请贴出实际运行的命令与结果；未运行的检查明确写出原因，不要写成已通过。

- [ ] 后端：`uv run ruff format --check app`、`uv run ruff check app`、`uv run mypy app`、`uv run pytest -q`
- [ ] 前端：`pnpm typecheck`、`pnpm lint`、`pnpm check:design`、`pnpm build`
- [ ] 浏览器验收：`pnpm test:e2e`（或只运行与改动相关的场景）
- [ ] 只改文档：已核对差异、相对链接、命令与引用路径

```text
# 命令与结果
```

## 影响与风险

<!-- 行为、数据、配置、兼容性影响；未验证的外部服务与平台差异 -->

## 文档与测试

- [ ] 新增或更新了对应测试
- [ ] 更新了受影响的文档（PROJECT_STATUS、PRODUCT_ROADMAP 或原专题页）
- [ ] 没有提交 `.env`、密钥、数据库、截图或真实旅行数据

## 检查清单

- [ ] 改动范围与关联 Issue 一致，没有无关格式化或重排
- [ ] 分支基于最新 `main`，提交信息清晰
- [ ] 没有把未运行或无法验证的检查写成已通过
