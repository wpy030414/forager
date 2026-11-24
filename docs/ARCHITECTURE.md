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
            ├─ /api/tree/*           线索（发现规则与进度）
            ├─ /api/settings/*       凭据与网络参数（加密存储、热应用）
            └─ /api/auth/*           扫码登录
                    │
        ┌───────────┴────────────┐
        │ 后台任务（同进程）       │
        │  Downloader：单队列      │
        │  Scheduler：线索巡检     │
        └───────────┬────────────┘
                    │ curl 子进程（TLS 指纹可过 CF）
                各源站 API / CDN
```

## 核心模块

| 模块 | 职责 |
|------|------|
| `transport/curl.py` | curl 子进程传输：非流式（HTML/JSON/图片）与流式（Range 透传） |
| `transport/client.py` | 统一 UA/Referer/Cookie、每主机串行频控、HTML 短 TTL 缓存、DoH 解析 + `--resolve` |
| `sources/base.py` | 源契约：list/search/detail/stream_url + 线索发现能力 + 配置字段声明 |
| `sources/pixiv.py` | 网页端 /ajax/* 接口：排行、搜索、作品详情、画师作品、关注/收藏（需 Cookie） |
| `sources/local.py` | 本地源：把库暴露成普通源（list/search/categories/detail/封面），不参与线索与设置 |
| `sources/hanime1.py` | Blade SSR 页面解析（选择器表 + 卡片/详情） |
| `library/downloader.py` | 单并发队列；dash 双轨 + ffmpeg remux；`.part` 原子落盘；元数据全量快照 |
| `library/cache.py` | 元数据 7 天 + 直链单独判过期；封面按 URL 哈希落盘 |
| `subs/scheduler.py` | 线索：巡检、差集入队、收藏/关注展开子节点、进度统计 |
| `source_config.py` + `secrets.py` | 配置读写、掩码、Fernet 加密、保存后热应用 |
| `api/*` | 路由与统一错误出口（一律 `{"error": ...}`） |

## 关键数据流

- **发现**：Scheduler 按 `interval_sec` 到期 → adapter 发现方法 → 与 `subscription_seen` 差集 →
  新条目 `Downloader.enqueue` → 全量记入 seen。
- **浏览与手选**：线索 → `source.browse`（番剧按季度、其余按视频）→ 详情返回 `episodes` →
  前端展开选集（番剧 ep / UGC 分P `BV...:pN`）→ 每个最小单元独立入队、独立进度。
- **下载**：detail 取元数据 → 若候选带 `audioUrl` 则双轨下载 + ffmpeg `-c copy` 合并 →
  校验长度 → 原子重命名 → 写封面 → 入 `library`（含 `category` 分区列）。
- **观看**：库卡片 → `/api/library/play`（本地优先）→ `<video>` 走 `/api/library/media/*`（支持 Range）。
- **配置**：设置页写入 `settings` 表（密文）→ 立即改内存 client 参数；启动时库内配置覆盖环境变量。

## 重要技术边界

- 上游请求一律经 curl：Cloudflare 按 TLS 指纹与头名大小写判定，Python TLS 栈会被 ban。
- 流代理与下载分离：`<video>` 只能吃渐进式，B 站直放走 `fnval=1` 的 durl，下载走 dash。
- 凭据接口永不回明文；扫码成功后由服务端直接落库，不经过浏览器。
- 新源只允许改 `sources/` 与注册表，路由与前端零改动。
