# forager

自托管的媒体采集与归档服务：**按 Web 端的要求去源站抓取、下载、归档，并按订阅持续发现新内容**。

- 后端：Python 3.12+ / FastAPI / SQLite / asyncio，零外部依赖（除系统 `curl`）
- 前端：复用同仓库 `apps/web` 的 tvOS 风格 SPA（React 19），生产模式由同一进程托管
- 部署：单容器，`docker compose up -d` 即用

> 本仓库的前身是 `libre-tv`（Hono + TS 全栈）。forager 用 Python 重写了后端并保持 `/api` 契约不变，
> 因此前端一行未改即可继续工作；旧实现保留在 `apps/server`，其设计记录见 `docs/`。

## 它解决什么问题

- 源站没有 API、界面不适合大屏、直链有防盗链与签名过期 —— 由服务端统一解析与代理
- 想要的东西不想每次手动找 —— 用**订阅**让服务自己盯着，发现新条目就入队
- 抓到的东西要能在离线时照样看 —— 下载后连同全量元数据、封面一起入库

## 已完成（M1）

| 能力 | 说明 |
|------|------|
| 源适配层 | `BaseSource` 契约（list / search / detail / stream / image），单源失败不影响其他路由 |
| hanime1 源 | 移植自旧实现：选择器表 + 卡片/详情解析 + 时长/播放量/ID 解析器 |
| 传输层 | `curl` 子进程（TLS 指纹过 Cloudflare WAF）+ 头名规范化 + 每主机串行频控 + HTML 短 TTL 缓存 |
| DNS | DoH 自解析（1.1.1.1 / doh.pub / dns.google）+ `--resolve` 直连 IPv4，破本地 DNS 污染 |
| 播放 | 双播：本地优先，未下载则走源站 Range 代理 |
| 下载 | `.part` 原子落盘、content-length 校验、元数据全量快照、封面降级 |
| 缓存 | 元数据 7 天 + 直链单独判过期（过期只刷直链）；封面按 URL 哈希落盘 |
| 订阅 | `(源, 类型, 值)` 定时轮询，差集入队，永不重复下载同一条 |
| 打包 | 多阶段 Docker（前端构建 + Python 运行）、PUID/PGID 降权、健康检查 |

## 快速开始

```bash
cp .env.example .env      # 按需填 PUID/PGID 与上游代理/Cookie
docker compose up -d --build
# 浏览器打开 http://<NAS-IP>:11408
```

数据落在 `./data`（`forager.db` + `media/`），可通过 `FORAGER_DATA` 改挂载点。

### 本地开发

```bash
# 后端
cd server && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
FORAGER_STATIC_DIR=../apps/server/dist/client .venv/bin/python -m forager

# 前端（另开一个终端，/api 自动代理到 11408）
pnpm --filter @libre-tv/web dev

# 测试
cd server && .venv/bin/python -m pytest -q
```

## 目录结构

```
server/forager/
├── config.py            环境变量 → Settings
├── db.py                SQLite：library / progress / settings / meta_cache / subscriptions
├── transport/
│   ├── curl.py          curl 子进程传输（一次性 + 流式）
│   └── client.py        频控、缓存、DoH 解析
├── sources/
│   ├── base.py          源契约
│   └── hanime1.py       hanime1 适配器（含单元测试覆盖的解析器）
├── library/
│   ├── cache.py         元数据缓存 + 封面缓存
│   └── downloader.py    单并发下载队列
├── subs/scheduler.py    订阅调度
└── api/                 FastAPI 路由（app / sources / library / subscriptions）
```

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sources` | 已注册源列表 |
| GET | `/api/sources/:id/list?page=&category=` | 浏览 |
| GET | `/api/sources/:id/search?kw=&page=` | 搜索 |
| GET | `/api/sources/:id/detail/:vid` | 详情（带元数据缓存，响应头 `x-cache`） |
| GET | `/api/sources/:id/stream?v=&quality=` | 流代理（Range 透传） |
| GET | `/api/sources/:id/image?url=` | 图片代理（主机白名单 + 磁盘缓存） |
| POST | `/api/library/downloads` | 入队下载 |
| GET/DELETE | `/api/library/downloads[/:key]` | 任务列表 / 取消 |
| GET/DELETE | `/api/library/items[/:key]` | 本地库 / 删除（含文件） |
| GET | `/api/library/play/:source/:id` | 播放来源解析（本地优先） |
| GET/PUT | `/api/library/mode` | 在线 / 离线开关 |
| GET/PUT | `/api/library/progress/:source/:id` | 续看进度 |
| GET/POST/DELETE | `/api/subscriptions` | 订阅管理 |
| POST | `/api/subscriptions/run` | 立即跑一轮订阅 |

## 订阅怎么用

```bash
# 盯住某个关键词，每 10 分钟看一次有没有新片
curl -X POST http://localhost:11408/api/subscriptions \
  -H 'content-type: application/json' \
  -d '{"source":"hanime1","kind":"query","value":"studio","intervalSec":600}'

# 盯住某个分区（kind=genre，value 为站点分区名）
curl -X POST http://localhost:11408/api/subscriptions \
  -H 'content-type: application/json' \
  -d '{"source":"hanime1","kind":"genre","value":"裏番","intervalSec":1800}'
```

首次运行只补抓最新 `FORAGER_SUB_INITIAL` 条（默认 3），其余仅记为「已见」，
所以订阅一个分区不会把全站拉下来。

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` | 11408 | 监听端口 |
| `DATA_DIR` | `./data` | 数据目录（容器内 `/data`） |
| `PUID` / `PGID` | 1000 | 容器降权后的运行身份 |
| `HANIME1_PROXY` | — | 上游请求代理（过 Cloudflare） |
| `HANIME1_DNS_PROXY` | — | 仅 DoH 走代理，站点仍直连 |
| `HANIME1_RESOLVE` | — | 钉死上游 IPv4，跳过 DoH |
| `HANIME1_COOKIE` | — | `cf_clearance=...` |
| `FORAGER_MIN_INTERVAL_MS` | 400 | 同一上游最小请求间隔 |
| `FORAGER_DOWNLOAD_CONCURRENCY` | 1 | 下载并发 |
| `FORAGER_SUB_INITIAL` | 3 | 订阅首轮补抓上限 |
| `FORAGER_SUB_TICK` | 30 | 调度器巡检间隔（秒） |

## 已知限制

- 目前只接入 hanime1；Bilibili / 网易云音乐 / Pixiv / CCTV 待接入（适配器契约已为异构认证模型留好位置）
- 下载并发默认 1，不支持断点续传跨重启（单次进程内可取消）
- 无字幕轨合并、无 DRM 处理（遇到即标为不支持）
- hanime1 有 Cloudflare 防护：大陆直连通常需要代理或有效的 `cf_clearance`
- 前端构建需要 Node 26（`Dockerfile` 的 web 阶段会自动完成）

## 合规

仅供个人在自有 NAS 上抓取**你有权访问**的内容，请遵守各站点服务条款与所在地法律，
不用于绕过付费墙、DRM 或任何形式的再分发。
