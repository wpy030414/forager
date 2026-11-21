# forager

自托管的媒体采集与归档服务：**按规则持续发现、下载、归档，并按来源与分区分面浏览**。

- 后端：Python 3.12+ / FastAPI / SQLite / asyncio，除系统 `curl` 外无外部服务
- 前端：tvOS 风格 SPA（React 19），三个页面：**库 / 线索 / 设置**
- 部署：单容器，`docker compose up -d` 即用

> 本仓库前身是 `libre-tv`（Hono + TS 全栈）。forager 用 Python 重写后端并保持接口契约，
> 旧的 TS 后端已移除，历史设计记录在 `docs/legacy/`。

## 三个页面

### 库
只看本地已归档内容，按**来源**与**源站分区**（不是 tag）筛选：点来源→再点分区，两级收敛。
卡片显示清晰度、时长、体积；点一下直接播放，进度自动续看。

### 线索
一条线索 = 一条持续发现规则。每个节点显示自己的同步进度与子树汇总进度：

| 类型 | 含义 | 支持的源 |
|------|------|----------|
| `query` | 关键词持续搜索 | 全部 |
| `partition` | 某分区的新热门 | Bilibili（UGC 主分区排行榜；番剧/国创/综艺走 PGC 放送时间表）/ Hanime1（站内 genre） |
| `uploader` | 某发布者的全部作品 | Bilibili（mid）/ Hanime1（名称） |
| `favorites` | 我的收藏夹 → 自动展开成发布者子线索 | Bilibili（需登录） |
| `following` | 我的关注 → 自动展开成发布者子线索 | Bilibili（需登录） |

发现过的条目记在 `subscription_seen`，同一线索永不重复入队；首次运行只补最新 3 条。
每个节点可单独「同步」或删除（删父节点会级联删子节点）。

选中线索后可在右侧浏览源站在线内容：点普通视频即按**最高可用清晰度**入库；
番剧季度与 UGC 多 P 视频会先展开分集，可单集下载或一次全部下载。
每个最小下载单元（单集 / 单个分 P）各自排队、各自显示进度，互不覆盖。

### 设置
- **扫码登录**（Bilibili）：服务端生成二维码，扫码确认后凭据直接落库，**不经过浏览器**
- 各源 Cookie / 代理 / DoH 代理 / 钉死 IP / 请求间隔
- **默认清晰度**：线索未单独指定时使用
- 凭据 Fernet 加密存储，接口只回 `****abcd`；保存后热生效，不用重启容器

## 快速开始

```bash
cp .env.example .env      # 按需填 PUID/PGID 与上游代理
docker compose up -d --build
# 打开 http://<NAS-IP>:11408
```

数据落在 `./data`（`forager.db` + `media/`），可通过 `FORAGER_DATA` 改挂载点。

### 本地开发

仓库是 pnpm workspace：`apps/web`（SPA）+ `apps/server`（Python 后端）+ `packages/shared`。

```bash
# 一次性：建虚拟环境并装后端依赖
cd apps/server
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'     # Windows: .venv\Scripts\pip install -e ".[dev]"
source .venv/bin/activate              # Windows: .venv\Scripts\activate
cd ../..

pnpm dev                               # 并行拉起前后端；前端 /api 自动代理到后端
pnpm dev:server                        # 只起后端，默认托管 apps/server/static
pnpm dev:web                           # 只起前端

pnpm build                             # 产物输出到 apps/server/static
pnpm test                              # 后端 pytest
```

## 目录结构

```
apps/server/forager/
├── config.py            环境变量 → Settings
├── db.py                SQLite：library / progress / settings / meta_cache / subscriptions
├── secrets.py           Fernet 凭据加密与密钥管理
├── source_config.py     设置页的读写、掩码、热应用
├── transport/
│   ├── curl.py          curl 子进程传输（一次性 + 流式）
│   └── client.py        频控、缓存、DoH 解析
├── sources/
│   ├── base.py          源契约 + 线索发现能力
│   ├── hanime1.py       hanime1 适配器
│   └── bilibili.py      Bilibili 适配器（WBI / dash / 扫码 / 收藏关注）
├── library/
│   ├── cache.py         元数据缓存 + 封面缓存
│   └── downloader.py    单并发下载队列（含 dash 双轨合并）
├── subs/scheduler.py    线索调度与进度统计
└── api/                 app / sources / library / tree / settings / auth
```

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sources` | 已注册源 |
| GET | `/api/sources/:id/list` `search` `detail/:vid` | 源站浏览 / 搜索 / 详情 |
| GET | `/api/sources/:id/stream` `image` | 流代理（Range）/ 图片代理（白名单） |
| GET | `/api/library/items?source=&category=&q=` | 本地库（支持分面筛选） |
| GET | `/api/library/facets` | 来源与分区的计数分面 |
| GET | `/api/library/play/:source/:id` | 播放来源解析 |
| GET/PUT | `/api/library/mode` `progress/:source/:id` | 离线开关 / 续看进度 |
| POST/GET/DELETE | `/api/library/downloads[/:key]` | 下载队列 |
| DELETE | `/api/library/items/:key` | 删除条目与文件 |
| GET | `/api/tree` | 线索（含每节点与汇总进度） |
| POST/DELETE | `/api/tree/nodes[/:id]` | 新建 / 删除线索 |
| POST | `/api/tree/nodes/:id/sync` | 立即同步单条线索 |
| GET | `/api/settings/sources` | 可配置项（敏感字段仅掩码） |
| PUT | `/api/settings/sources/:id` | 保存并热应用 |
| POST | `/api/settings/sources/:id/test` | 用当前配置试连 |
| GET | `/api/auth/bilibili/qrcode` | 生成登录二维码（服务端出图） |
| GET | `/api/auth/bilibili/qrcode/poll?key=` | 轮询登录状态，成功后自动落库 |

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` | 11408 | 监听端口 |
| `DATA_DIR` | `./data` | 数据目录（容器内 `/data`） |
| `PUID` / `PGID` | 1000 | 容器降权后的运行身份 |
| `FORAGER_SECRET_KEY` | 自动生成 | 凭据加密密钥，见下方说明 |
| `HANIME1_PROXY` / `_DNS_PROXY` / `_RESOLVE` / `_COOKIE` | — | hanime1 上游 |
| `BILIBILI_PROXY` / `_COOKIE` | — | Bilibili 上游 |
| `FORAGER_MIN_INTERVAL_MS` | 400 | 同一上游最小请求间隔 |
| `FORAGER_DOWNLOAD_CONCURRENCY` | 1 | 下载并发 |
| `FORAGER_SUB_INITIAL` | 3 | 线索首轮补抓上限 |
| `FORAGER_SUB_TICK` | 30 | 调度器巡检间隔（秒） |

**设置页保存的值优先于环境变量。**

### 凭据加密

Cookie 用 Fernet 加密存 `settings` 表，接口只回掩码。密钥取 `FORAGER_SECRET_KEY`，
未设置时自动生成到 `data/.secret_key`（0600）。**迁移数据目录或更换密钥会让旧凭据失效**，
现象是设置页显示「未配置」，重新填写或扫码即可。跨机器迁移请显式指定密钥：

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 已知限制

- Bilibili 匿名只能取 360P/480P；高清晰度需扫码登录或填 Cookie。海外访问通常需要 `BILIBILI_PROXY`
- Bilibili 多 P 稿件只取 P1；番剧（大会员/地区）未实现
- hanime1 没有独立分区字段，分区取其 genre；收藏/关注类线索仅 Bilibili 支持
- 下载并发默认 1，不支持跨重启断点续传
- 无字幕轨合并、无 DRM 处理（遇到即标为不支持）

## 合规

仅供个人在自有 NAS 上抓取**你有权访问**的内容，请遵守各站点服务条款与所在地法律，
不用于绕过付费墙、DRM 或任何形式的再分发。
