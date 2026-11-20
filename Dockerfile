
# ---------- 1) 前端构建：复用 libre-tv 的 tvOS SPA ----------
FROM node:26-alpine AS web
# Node 26 官方镜像已不再内置 corepack，直接装 pnpm 并指向国内源
RUN npm install -g pnpm@12 --registry=https://registry.npmmirror.com
WORKDIR /build
COPY . .
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @libre-tv/web build \
 && mkdir -p /out \
 && cp -a server/static/. /out/

# ---------- 2) 运行镜像 ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=11408 \
    FORAGER_STATIC_DIR=/app/static \
    TZ=Asia/Shanghai

# 系统依赖：curl 是传输层硬依赖（ADR-5：TLS 指纹过 Cloudflare），
# ffmpeg 用于合并 dash 音视频轨，gosu 用于降权。
# Debian 官方源在国内很慢（ffmpeg 依赖树又大），先换清华源再装。
RUN set -eux; \
    for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        if [ -f "$f" ]; then \
            sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; \
                    s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' "$f"; \
        fi; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ffmpeg tzdata gosu; \
    rm -rf /var/lib/apt/lists/*

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
