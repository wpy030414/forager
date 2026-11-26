# ARCHITECTURE — forager

## 核心哲学

界面的一切内容都来自「源」：本地库（`local`）与 Hanime1、Pixiv 同级，
共用同一套 list / search / categories / detail / play 契约；
下载只是「把内容从一个源搬进本地源」。

## 系统概述

```
浏览器 ──► FastAPI (:11408)
            ├─ /                     SPA（构建产物 apps/server/static）
            ├─ /api/sources/*        源适配：列表/搜索/详情/流代理/图片代理
            ├─ /api/library/*        本地库、下载队列、分面、播放解析
            ├─ /api/fetchers/*       采集器（线索并集 + 触发时机）
            ├─ /api/clues/*          线索（发现规则）与在线浏览
            ├─ /api/settings/*       凭据与网络参数（加密存储、热应用）
            ├─ /api/wallpapers/*     首页背景图索引与文件（首次运行时拉进本地库）
            └─ /api/auth/*           扫码登录
                    │
        ┌───────────┴────────────┐
        │ 后台任务（同进程）       │
        │  Downloader：单队列      │
        │  FetcherScheduler：巡检  │
        │  背景图补拉（一次性）     │
        └───────────┬────────────┘
                    │ curl 子进程（TLS 指纹可过 CF）
          ┌─────────┴──────────┐
          ▼                    ▼
     源站 / 媒体 CDN       mihomo :7890（Clash Meta）
      （直连分流）          订阅节点 / DoH 解析
```

## 核心模块

| 模块 | 职责 |
|------|------|
| `transport/curl.py` | curl 子进程传输：非流式（HTML/JSON/图片）与流式（Range 透传） |
| `transport/client.py` | 统一 UA/Referer/Cookie、每主机串行频控、HTML 短 TTL 缓存、DoH 解析 + `--resolve`（仅未设代理时） |
| `sources/base.py` | 源契约：list/search/detail/stream_url + `ClueQuery` / `discover` / `validate_clue` / 配置字段声明 |
| `sources/pixiv.py` | 网页端 /ajax/* 接口：排行、搜索、作品详情、画师作品、关注/收藏（需 Cookie）；线索组合校验 |
| `sources/local.py` | 本地源：把库暴露成普通源（list/search/categories/detail/封面），不参与线索与设置 |
| `sources/hanime1.py` | Blade SSR 页面解析（选择器表、静态分区表、详情页元信息） |
| `library/downloader.py` | 单并发队列；dash 双轨 + ffmpeg remux；`.part` 原子落盘；海报与元信息文件；终态回调 |
| `library/sidecar.py` | 归档副产物：页面元信息投影、`.info.json`、movie NFO |
| `library/cache.py` | 元数据 7 天 + 直链单独判过期；封面按 URL 哈希落盘 |
| `clues/scheduler.py` | 采集器：触发判定、互斥运行、逐线索翻页、增量早停、并集去重、派生画师线索、补投 |
| `clues/ledger.py` | 作品台账：全局按 `(source, video_id)` 去重，记录入库状态与失败次数 |
| `source_config.py` + `secrets.py` | 配置读写、掩码、Fernet 加密、保存后热应用（环境默认值打底） |
| `wallpapers.py` | 首页背景图清单与本地库落盘：首次运行后台经统一出口补拉，接口只返回就绪清单 |
| `api/*` | 路由与统一错误出口（一律 `{"error": ...}`） |
| `clash/` + `docker/mihomo-entrypoint.sh` | mihomo 配置模板（订阅版 / 直连兜底）与启动渲染 |

## 关键数据流

- **发现**：FetcherScheduler 每 30s 巡检 → 按触发时机挑出到点的采集器 → 互斥执行 →
  处理展开类线索（收藏 / 关注 → 同一采集器内的画师子线索）→ 逐条线索 `discover()` 翻页 →
  每页「本线索没见过」的写 `clue_seen`、其中不在作品台账的写 `works(pending)` 并入队
  （两步同一事务提交后才入队，停机不会留下「已见但没入账」）→ 「全部」的线索首轮翻到底后靠增量早停。
- **去重**：`clue_seen` 是每条线索的扫描记忆；`works` 是全局台账，命中即 skip，并记录入库状态。
- **补投**：启动时与每轮采集器运行前对账，`pending` 且失败次数 < 3、不在下载队列里的作品重新入队；
  用户在面板取消 → `cancelled`，从库里删除 → `removed`，都不再补投；停机中断不算取消。
- **浏览与手选**：线索 → `source.discover`（只读，不写记忆）→ 详情返回 `episodes` →
  前端展开选集（番剧 ep / UGC 分P `BV...:pN`）→ 每个最小单元独立入队、独立进度。
- **下载**：detail 取元数据 → 若候选带 `audioUrl` 则双轨下载 + ffmpeg `-c copy` 合并 →
  校验长度 → 原子重命名 → 海报落在媒体旁 → 写 `.info.json` / `.nfo` → 入 `library`（投影后的元信息）。
- **观看**：库卡片 → `/api/library/play`（本地优先）→ `<video>` 走 `/api/library/media/*`（支持 Range）。
- **配置**：设置页写入 `settings` 表（密文）→ 立即改内存 client 参数；启动时库内配置覆盖环境变量。
- **首页背景**：启动后在后台按清单经统一出口把缺失的图片拉进 `<data>/media/wallpapers/` →
  前端取 `/api/wallpapers/index` 只排程已就绪的那几张；主体内容不等图片，缺图只显示深色底。
- **前端焦点**：所有可聚焦元素经 `Focusable` 上报给 Focus Manager（`apps/web/src/lib/focus.ts`），
  由它统一处理焦点记忆与恢复、货架跟随滚动、页面落脚点、Tab 与方向键、鼠标悬停移焦（ADR-10）。

## 重要技术边界

- 上游请求一律经 curl：Cloudflare 按 TLS 指纹与头名大小写判定，Python TLS 栈会被 ban。
- 设了代理后 `--resolve` / DoH / 钉死 IP 全部让位给代理解析；未设代理时才用 DoH + `--resolve` 直连 IPv4。
- mihomo 默认让 hanime1 直连：经机场节点访问会被 Cloudflare 挑战（ADR-8）。
- Hanime1 的 `genre` 与 `query` 可同时下发，站内为 AND；分区表是静态的站内 genre 列表。
- 凭据接口永不回明文；扫码成功后由服务端直接落库，不经过浏览器。
- 新源只允许改 `sources/` 与注册表，路由与前端零改动。