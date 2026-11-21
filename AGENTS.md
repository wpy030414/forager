# AGENTS.md

forager 是自托管媒体采集与归档服务（Python 后端 + tvOS 风格 SPA 前端）。

## 概述
- 本项目是什么：按「线索」持续发现源站内容，下载归档到本地，并在 Web 端按来源与分区分面浏览。
- 两个源：hanime1（HTML 解析）、Bilibili（WAPI + WBI 签名 + dash 双轨合并）。

## 边界与范围
- 范围内：线索树调度、下载队列与入库、播放（本地优先 / 源站 Range 代理）、凭据设置与扫码登录。
- 非目标：多租户与公网部署、DRM 处理、番剧/大会员、跨重启断点续传。

## Agent 操作指南
- 如何理解本项目：先读 `docs/ARCHITECTURE.md`；源契约在 `apps/server/forager/sources/base.py`，
  新源继承 `BaseSource` 并在 `sources/__init__.py` 注册，可选实现线索发现能力。
- 全局规则 / 约定：
  - 后端测试：`pnpm test`（即 `cd apps/server && .venv/bin/python -m pytest -q`）；前端检查：`pnpm check`。
  - 依赖：Python 走清华源，npm 走 npmmirror；`tailwindcss` 钉 3.4.x。
  - 回答与文档只用中文；commit 遵循 Conventional Commits（type/scope 英文、描述中文）。
  - 工作目录不是 Git 仓库时拒绝写入；多步骤任务验证通过后只提交一次。

## 目录速查
- `apps/server/` — Python 后端工作区包（`@libre-tv/server`）：`forager/` 内含
  `transport/`（curl + DoH）、`sources/`（适配器）、`library/`（下载与缓存）、
  `subs/`（线索树调度）、`api/`（路由）
- `apps/server/tests/` — pytest
- `apps/web/src/` — React SPA（库 / 线索树 / 设置三页，空间导航、tvOS 视觉）
- `packages/shared/src/` — 前端共享类型（历史命名，仍为 `@libre-tv/shared`）
- `docs/` — ARCHITECTURE / DECISIONS / PORT-NOTES / legacy（历史设计）
- `Dockerfile` / `compose.yaml` — 多阶段镜像与编排
- `.xat/workflows/ci-cd.yml` — Docker Compose 部署流程
