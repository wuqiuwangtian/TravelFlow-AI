# ============================================================
#  TravelFlow AI · 前端镜像
#  三阶段构建，最终只保留 Next.js standalone 产物
# ============================================================

# ---------- 依赖 ----------
FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# ---------- 构建 ----------
FROM node:22-alpine AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* 会被内联进客户端产物，因此必须在**构建期**提供
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_TELEMETRY_DISABLED=1

RUN pnpm build

# ---------- 运行 ----------
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# 非 root 运行
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# standalone 产物已包含 server.js 与精简后的 node_modules
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

# standalone 模式的正确启动方式（`next start` 不适用于该模式）
CMD ["node", "server.js"]
