# forager

自托管的媒体采集与归档服务：**按规则持续发现、下载、归档，并按来源与分区分面浏览**。

- 后端：Python 3.12+ / FastAPI / SQLite / asyncio，除系统 `curl` 外无外部服务
- 访问控制：本地单账号（用户名 + 密码 + 恢复码）；首次启动的初始密码打印在服务端日志里
- 前端：tvOS 风格 SPA（React 19），四个页面：**首页 / 源 / 线索 / 设置**
- 哲学：界面的一切内容都来自「源」——**本地库也是一个源**，与 Hanime1、Pixiv 同级，共用同一套浏览与播放路径
- 出口：应用内的上游流量统一经 mihomo（Clash Meta）一个端口出去，分流与 DNS 在那边配
- 部署：`docker compose up -d` 即用（forager + mihomo 两个容器）

> 本仓库前身是 `libre-tv`（Hono + TS 全栈）。forager 用 Python 重写后端并保持接口契约，
> 旧的 TS 后端已移除，历史设计记录在 `docs/legacy/`。

## 四个页面

### 首页
tvOS 无 chrome 流式墙：进入即 4K 风景照打满全屏，右上角只有时间与设置。
从上到下依次是「继续播放 / 为你推荐」双货架、平台入口（本地、Hanime1、Pixiv；CCTV 待接入）
与采集器入口；除前两个货架外，其余区块不设标题、副标题、提示或「更多」。
获焦卡片会放大，横向货架自动居中滚动。

### 源
每个源一页，本地与在线完全同构：列表 / 搜索 / 分区筛选走
`/api/sources/<id>/list|search|categories`，播放走 `/api/library/play`（本地文件优先，其次源站代理流）。

### 线索
线索分两层：

- **线索**：一条发现规则 = 来源 + 分区（可选）+ 关键词（可选）+ 数量（前 1 / 3 / 5 / 10 页，或全部）。
  分区与关键词可以同时填（Hanime1 站内按 AND 处理）；Pixiv 另可填画师 ID，或选「我的收藏 / 我的关注」展开成画师子线索。
- **采集器**：若干线索的并集 + 触发时机（手动 / 1H / 1D / 1W / 1M）+ 默认清晰度。
  到点后依次扫描自己名下的线索，把新发现的作品交给下载队列。

去重分两级：每条线索记下自己见过的作品（「全部」靠它做增量早停：首轮翻到底，之后每轮翻到「整页都是老面孔」即停）；
全局作品台账按 `(来源, 作品 ID)` 记一行，所以多条线索交叉发现同一作品只会入队一次。

- 失败或重启丢失的条目会自动补投，最多 3 次；用户在下载面板取消、或在库里删除的条目不再补投
- 删除采集器会连同它的线索与扫描记忆一起删掉；作品台账保留，所以重建同样的采集器不会重复下载

选中线索后可在右侧浏览源站在线内容：点普通视频即按**最高可用清晰度**入库；
番剧季度与 UGC 多 P 视频会先展开分集，可单集下载或一次全部下载。
每个最小下载单元（单集 / 单个分 P）各自排队、各自显示进度，互不覆盖。
数量选「全部」时创建按钮需要二次确认——Hanime1 单个分区首轮就可能上万条。

### 设置
- **网页登录**：跳转到源站授权，回调后凭据由服务端直接落库，**不经过浏览器**
- 各源 Cookie / 代理 / DoH 代理 / 钉死 IP / 请求间隔；代理留空即跟随全局出口
- **默认清晰度**：采集器未单独指定时使用
- 凭据 Fernet 加密存储，接口只回 `****abcd`；保存后热生效，不用重启容器

## 快速开始

```bash
cp .env.example .env      # 填 PUID/PGID；想走机场节点就填 CLASH_SUBSCRIBE_URL
docker compose up -d --build
# 打开 http://<NAS-IP>:11408
```

1. `CLASH_SUBSCRIBE_URL` 填 clash YAML、`ss:// / vmess:// / trojan://` 链接列表或其 base64；
   留空则 mihomo 以全部直连运行，forager 照常经它出网。
2. 想让局域网其它设备共用这个代理：打开 compose 里 `mihomo` 服务注释掉的 `ports`。
3. Hanime1 默认直连（经机场节点访问会吃到 Cloudflare 挑战页），其余流量走订阅节点；
   要连 Hanime1 也走节点，删掉 `clash/config.example.yaml` 里那两行 `DOMAIN-SUFFIX,...DIRECT`。

数据落在 `./data`（`forager.db` + `media/`），可通过 `FORAGER_DATA` 改挂载点。
`./clash` 是被 mihomo 挂载的配置目录：仓库只提供两份模板，订阅缓存与渲染产物不随代码走。

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
├── db.py                SQLite：library / progress / settings / meta_cache / fetchers / clues / clue_seen / works
├── secrets.py           Fernet 凭据加密与密钥管理
├── source_config.py     设置页的读写、掩码、热应用
├── transport/
│   ├── curl.py          curl 子进程传输（一次性 + 流式）
│   └── client.py        频控、缓存、DoH 解析
├── sources/
│   ├── base.py          源契约 + 线索发现能力（ClueQuery / discover / validate_clue）
│   ├── hanime1.py       hanime1 适配器（分区表、详情页元信息）
│   └── pixiv.py         Pixiv 适配器（网页 ajax 接口 / 画师 / 收藏关注）
├── library/
│   ├── cache.py         元数据缓存 + 封面缓存
│   ├── sidecar.py       归档副产物：元信息投影、.info.json、.nfo
│   └── downloader.py    单并发下载队列（含 dash 双轨合并、海报与元信息落盘）
├── clues/
│   ├── scheduler.py     采集器调度：触发、翻页、增量早停、并集去重、补投
│   └── ledger.py        作品台账与失败重试计数
└── api/                 app / sources / library / fetchers / settings / auth
clash/                   mihomo 配置模板（订阅版 / 直连兜底版）
docker/                  forager 与 mihomo 的入口脚本
```

## 归档产物

每个入库的作品，除了媒体本身，还会在媒体目录里留下：

| 文件 | 内容 |
|------|------|
| `<stem>.mp4`（或图片原格式） | 媒体 |
| `<stem>-poster.<ext>` | 海报，Emby / Jellyfin / Kodi 直接识别 |
| `<stem>.info.json` | 页面元信息全量快照（标题、社团、简介、上传日期、观看数、时长、标签、封面、原始页地址），不含会过期的签名直链 |
| `<stem>.nfo` | 视频类条目额外写一份 movie NFO，媒体库扫库即可见元信息 |

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sources` | 已注册源（含 `local` 本地源） |
| GET | `/api/sources/:id/list` `search` `detail/:vid` | 源浏览 / 搜索 / 详情（本地源同构） |
| GET | `/api/sources/:id/categories` | 源分区表（本地源返回库内分区） |
| GET | `/api/sources/:id/stream` `image` | 流代理（Range）/ 图片代理（白名单） |
| GET | `/api/library/items?source=&category=&q=` | 本地库（支持分面筛选） |
| GET | `/api/library/continue` `facets` `keys` | 继续播放 / 计数分面 / 轻量键列表 |
| GET | `/api/library/play/:source/:id` | 播放来源解析 |
| GET/PUT | `/api/library/mode` `progress/:source/:id` | 离线开关 / 续看进度 |
| POST | `/api/library/downloads` | 入队下载 |
| GET | `/api/library/downloads?limit=&keys=` | 下载列表（已裁剪）+ 各状态计数 |
| DELETE | `/api/library/downloads/:key` `items/:key` | 取消任务 / 删除条目与文件 |
| GET | `/api/fetchers` | 采集器（含线索、进度、可选分区与触发/数量选项） |
| POST/DELETE | `/api/fetchers[/:id]` | 新建 / 删除采集器 |
| POST | `/api/fetchers/:id/run` | 立即运行（后台执行，`started: false` 表示它正在跑） |
| POST/DELETE | `/api/clues[/:id]` | 新建 / 删除线索 |
| GET | `/api/clues/:id/items?page=` | 浏览线索对应的在线内容（不记录、不下载） |
| GET | `/api/settings/sources` | 可配置项（敏感字段仅掩码） |
| PUT | `/api/settings/sources/:id` | 保存并热应用 |
| POST | `/api/settings/sources/:id/test` | 用当前配置试连 |

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` | 11408 | 监听端口 |
| `DATA_DIR` | `./data` | 数据目录（容器内 `/data`） |
| `PUID` / `PGID` | 1000 | 容器降权后的运行身份 |
| `FORAGER_SECRET_KEY` | 自动生成 | 凭据加密密钥，见下方说明 |
| `CLASH_SUBSCRIBE_URL` | 空 | mihomo 的订阅地址；留空 = 全部直连 |
| `MIHOMO_IMAGE` / `MIHOMO_TAG` | `metacubex/mihomo` / `v1.19.34` | 镜像名与版本，可换成加速地址 |
| `FORAGER_PROXY` | `http://mihomo:7890` | 应用全局出口；各源的单独设置优先于它 |
| `HANIME1_PROXY` / `_DNS_PROXY` / `_RESOLVE` / `_COOKIE` | — | hanime1 上游的单独覆盖 |
| `PIXIV_PROXY` / `PIXIV_COOKIE` | — | Pixiv 上游（读关注 / 收藏夹需要 Cookie） |
| `FORAGER_MIN_INTERVAL_MS` | 400 | 同一上游最小请求间隔 |
| `FORAGER_DOWNLOAD_CONCURRENCY` | 1 | 下载并发 |
| `FORAGER_SUB_TICK` | 30 | 采集器巡检间隔（秒） |

**设置页保存的值优先于环境变量**；代理的优先级是「设置页保存值 > 该源环境变量 > 全局 `FORAGER_PROXY`」，
清空设置页里的代理即回退到默认出口。

### 凭据加密

Cookie 用 Fernet 加密存 `settings` 表，接口只回掩码。密钥取 `FORAGER_SECRET_KEY`，
未设置时自动生成到 `data/.secret_key`（0600）。**迁移数据目录或更换密钥会让旧凭据失效**，
现象是设置页显示「未配置」，重新填写或扫码即可。跨机器迁移请显式指定密钥：

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 升级与回滚

上一版的线索只存在于 `subscriptions` / `subscription_seen` 两张表。新版本首次启动会：

1. 把库文件另存一份 `forager.db.bak`（只做一次）；
2. 在一个事务里改名、建表、回填：旧线索全部迁入一个「默认采集器」，参数拆成分区 / 关键词 / 画师与「前 1 页」，
   扫描记忆原样保留。

要回滚，停容器后用 `forager.db.bak` 覆盖 `forager.db` 再换回旧版本即可。

## 已知限制

- 设了代理后，应用自带的 DoH 自解析与「钉死 IPv4」不再生效——域名解析交给 mihomo
- Hanime1 的 `裏番 / 泡麵番 / 新番預告` 三个分区下发的是另一套「紧凑卡」模板（只有封面、标题、ID，没有时长 / 社团 / 播放数）：
  适配器两套模板都认；`query=` 检索页仍用标准卡
- Pixiv 匿名即可浏览排行/搜索/画师；关注与收藏夹类线索需在设置页填 Cookie
- Pixiv ugoira（动图）首版按首帧处理；多页作品经线索发现时只入库首页
- 下载并发默认 1，不支持跨重启断点续传（中断的条目靠台账补投重下）
- 无字幕轨合并、无 DRM 处理（遇到即标为不支持）

## 合规

仅供个人在自有 NAS 上抓取**你有权访问**的内容，请遵守各站点服务条款与所在地法律，
不用于绕过付费墙、DRM 或任何形式的再分发。