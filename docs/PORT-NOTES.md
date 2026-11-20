# 移植记录：libre-tv → forager

## 为什么移植

原实现（Hono + TypeScript）已跑通 hanime1 全链路，并与 Cloudflare/DNS 污染缠斗出了六条 ADR。
forager 需要的是 NAS 上可长期跑的 Python 服务，因此**后端重写、成果全量继承**：
契约、选择器、传输策略、缓存语义、下载落盘规则逐条对应移植。

## 对应关系

| 旧实现（TS） | 新实现（Python） | 处理方式 |
|---|---|---|
| `packages/shared/src/types.ts` | `sources/base.py` + 路由返回的 dict | 契约语义不变，前端零改动 |
| `sources/curl.ts` | `transport/curl.py` | 逐函数移植；新增 `open_stream` 流式路径（原来靠 Node fetch） |
| `sources/http.ts` | `transport/client.py` | 频控/缓存/DoH/头规范化全保留 |
| `sources/hanime1/index.ts` | `sources/hanime1.py` | 选择器表、`parseCards`、`parseWatch`、`parseDuration`、`parseViews`、`extractVid` 全保留 |
| `sources/index.ts` | `sources/__init__.py` | 注册表改为 `create_sources(settings)` |
| `library/db.ts` | `db.py` | 表结构一致；新增 `subscriptions` / `subscription_seen` |
| `library/downloader.ts` | `library/downloader.py` | 单并发队列、`.part` 原子重命名、大小校验、封面降级 |
| `library/cache.ts` | `library/cache.py` | 元数据 7 天 / 直链 5 分钟 skew / 封面 URL 哈希 |
| `routes/sources.ts` | `api/sources.py` | 含 `x-cache: HIT/PARTIAL/STALE/MISS` 语义 |
| `routes/library.ts` | `api/library.py` | 含 Range 单文件服务与目录穿越防护 |
| `index.ts`（静态托管） | `api/app.py` | SPA fallback + `/assets` 挂载 |
| `apps/server/tests/*.test.ts` | `server/tests/*.py` | 逐条移植，另补 API 装配冒烟与路径穿越测试 |

## 有意改变的地方

1. **传输层统一走 curl**：旧实现的下载用 Node `fetch`，新实现全部走 curl，避免媒体 CDN 上再次遇到指纹判定。
2. **流式响应显式解析响应头**：curl `-D <file> -o -`，头落盘后立刻转发 body，不缓冲整片。
3. **新增订阅调度器**：旧实现只有「点一下下一条」，forager 的核心是「一直盯着」。
4. **新增 `/api/subscriptions`**：旧前端未消费，后续可在 SPA 里加订阅页。

## 未继承 / 待办

- 旧 `apps/server`（Hono）已于 v2 移除，实现细节以本文件与 git 历史为准
- Bilibili（WBI 签名）、网易云（sidecar）、Pixiv（OAuth 轮换）、CCTV（栏目 API）尚未接入
- 断点续传跨重启、字幕轨合并、REAL-DEBRID 类多源择优
