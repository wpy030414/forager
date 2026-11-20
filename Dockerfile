# syntax=docker/dockerfile:1

# ---------- 1) 前端构建：复用 libre-tv 的 tvOS SPA ----------
FROM node:26-alpine AS web
RUN corepack enable
WORKDIR /build
COPY . .
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @libre-tv/web build \
 && mkdir -p /out \
 && cp -a apps/server/dist/client/. /out/

# ---------- 2) 运行镜像 ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=11408 \
    FORAGER_STATIC_DIR=/app/static \
    TZ=Asia/Shanghai

# curl 是传输层的硬依赖（见 ADR-5：TLS 指纹过 Cloudflare）；gosu 用于降权
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata gosu \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/pyproject.toml ./server/pyproject.toml
COPY server/forager ./server/forager
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple ./server

COPY --from=web /out /app/static
COPY docker/entrypoint.sh /usr/local/bin/forager-entrypoint
RUN chmod +x /usr/local/bin/forager-entrypoint && mkdir -p /data

EXPOSE 11408
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

ENTRYPOINT ["forager-entrypoint"]
