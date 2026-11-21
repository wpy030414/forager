"""线索树调度器。

一个「线索」= 一条持续发现规则，落在 subscriptions 表（沿用旧表名）：
- query      关键词搜索
- partition  源站分区（B 站 tid / hanime1 genre）
- uploader   某个发布者的全部作品
- favorites  收藏夹展开 → 自动派生 uploader 子节点
- following  关注列表展开 → 自动派生 uploader 子节点

父子关系用 parent_id 表示。进度统计来自 subscription_seen × library 的关联：
discovered = 该线索发现过的条目数，done = 其中已下载入库的数量。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any, Callable

from ..db import Database, now_iso
from ..library.downloader import Downloader
from ..sources.base import BaseSource

EXPAND_KINDS = frozenset({"favorites", "following"})


class SubscriptionScheduler:
    def __init__(
        self,
        db: Database,
        sources: dict[str, BaseSource],
        downloader: Downloader,
        *,
        initial: int = 3,
        tick: int = 30,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.db = db
        self.sources = sources
        self.downloader = downloader
        self.initial = max(0, initial)
        self.tick = max(5, tick)
        self._log = log or (lambda _m: None)
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ---- 生命周期 ----

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="forager-tree")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 调度器不允许被单次失败打挂
                self._log(f"[tree] 轮询异常：{exc}")
            await asyncio.sleep(self.tick)

    # ---- 同步 ----

    async def run_once(self) -> list[dict[str, Any]]:
        enqueued: list[dict[str, Any]] = []
        for row in self._due():
            await self._sync(row, enqueued)
        return enqueued

    async def sync_node(self, node_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM subscriptions WHERE id = ?", (node_id,))
        if row is None:
            raise KeyError(node_id)
        enqueued: list[dict[str, Any]] = []
        await self._sync(row, enqueued)
        return {"ok": True, "enqueued": len(enqueued)}

    async def _sync(self, row: Any, enqueued: list[dict[str, Any]]) -> None:
        source = self.sources.get(row["source"])
        if source is None:
            self._mark(row["id"], "error", f"未注册的源：{row['source']}")
            return

        try:
            if row["kind"] in EXPAND_KINDS:
                users = await self._expand_source(source, row["kind"])
                added = self._expand(row, users)
                self._mark(row["id"], "ok", None)
                self._log(f"[tree] #{row['id']} 展开 {added} 个发布者")
                return

            page = await self._fetch(source, row)
            items = page.get("items") or []
            seen = {
                r["video_id"]
                for r in self.db.query(
                    "SELECT video_id FROM subscription_seen WHERE sub_id = ?", (row["id"],)
                )
            }
            fresh = [item for item in items if item["id"] not in seen]
            if not seen:
                fresh = fresh[: self.initial]  # 首轮只补最新 N 条
            quality = self._quality(row)

            for item in fresh:
                self.downloader.enqueue(row["source"], item["id"], quality)
                enqueued.append(
                    {"nodeId": row["id"], "source": row["source"], "id": item["id"]}
                )

            if items:
                stamp = now_iso()
                self.db.executemany(
                    "INSERT OR IGNORE INTO subscription_seen (sub_id, video_id, first_seen_at) "
                    "VALUES (?, ?, ?)",
                    [(row["id"], item["id"], stamp) for item in items],
                )
            self._mark(row["id"], "ok", None)
            if fresh:
                self._log(f"[tree] #{row['id']} 新增 {len(fresh)} 条入队")
        except Exception as exc:  # noqa: BLE001 —— 单节点失败只影响自己
            self._mark(row["id"], "error", str(exc) or exc.__class__.__name__)
            self._log(f"[tree] #{row['id']} 同步失败：{exc}")

    async def _fetch(self, source: BaseSource, row: Any) -> dict[str, Any]:
        kind, value = row["kind"], row["value"]
        if kind == "query":
            return await source.search(value, 1)
        if kind == "partition":
            return await source.partition_items(value, 1)
        if kind == "uploader":
            return await source.uploader_items(value, 1)
        raise ValueError(f"未知线索类型：{kind}")

    async def _expand_source(self, source: BaseSource, kind: str) -> list[dict[str, str]]:
        if kind == "favorites":
            return await source.favorite_uploaders()
        return await source.following_uploaders()

    def _expand(self, row: Any, users: list[dict[str, str]]) -> int:
        added = 0
        for user in users:
            value = str(user.get("id") or "").strip()
            if not value:
                continue
            exists = self.db.query_one(
                "SELECT id FROM subscriptions WHERE source = ? AND parent_id = ? "
                "AND kind = 'uploader' AND value = ?",
                (row["source"], row["id"], value),
            )
            if exists is not None:
                continue
            self.db.execute(
                "INSERT INTO subscriptions "
                "(source, kind, value, interval_sec, quality, enabled, created_at, parent_id, title) "
                "VALUES (?, 'uploader', ?, ?, ?, 1, ?, ?, ?)",
                (
                    row["source"],
                    value,
                    row["interval_sec"],
                    row["quality"],
                    now_iso(),
                    row["id"],
                    user.get("title") or value,
                ),
            )
            added += 1
        return added

    def _quality(self, row: Any) -> str | None:
        if row["quality"]:
            return row["quality"]
        return self.db.get_setting(f"source.{row['source']}.quality") or None

    def _mark(self, node_id: int, status: str, error: str | None) -> None:
        self.db.execute(
            "UPDATE subscriptions SET last_run_at = ?, status = ?, last_error = ? WHERE id = ?",
            (now_iso(), status, error, node_id),
        )

    def _due(self) -> list[Any]:
        now = datetime.now(UTC)
        due = []
        for row in self.db.query("SELECT * FROM subscriptions WHERE enabled = 1"):
            last = row["last_run_at"]
            if not last:
                due.append(row)
                continue
            try:
                elapsed = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                due.append(row)
                continue
            if elapsed >= max(60, row["interval_sec"]):
                due.append(row)
        return due

    # ---- 线索树 ----

    def tree(self) -> dict[str, Any]:
        rows = [dict(r) for r in self.db.query("SELECT * FROM subscriptions ORDER BY id")]
        stats = self._stats()
        nodes: dict[int, dict[str, Any]] = {}
        children: dict[int | None, list[dict[str, Any]]] = {}

        for row in rows:
            stat = stats.get(row["id"], {"discovered": 0, "done": 0})
            node = {
                "id": row["id"],
                "parentId": row.get("parent_id"),
                "source": row["source"],
                "kind": row["kind"],
                "value": row["value"],
                "title": row.get("title") or row["value"],
                "intervalSec": row["interval_sec"],
                "quality": row.get("quality") or "",
                "enabled": bool(row["enabled"]),
                "lastRunAt": row.get("last_run_at"),
                "status": row.get("status") or "idle",
                "lastError": row.get("last_error"),
                "discovered": int(stat["discovered"] or 0),
                "done": int(stat["done"] or 0),
                "children": [],
            }
            node["pending"] = max(0, node["discovered"] - node["done"])
            nodes[row["id"]] = node
            children.setdefault(row.get("parent_id"), []).append(node)

        for node in nodes.values():
            node["children"] = children.get(node["id"], [])

        groups = []
        for source_id, source in self.sources.items():
            roots = [n for n in children.get(None, []) if n["source"] == source_id]
            for root in roots:
                _aggregate(root)
            groups.append(
                {
                    "source": source_id,
                    "name": source.name,
                    "kinds": list(source.kinds),
                    "categories": self._categories(source, source_id),
                    "qualityOptions": list(source.quality_options),
                    "nodes": roots,
                    "total": _sum_totals(roots),
                }
            )

        return {
            "sources": groups,
            "total": _sum_totals([node for group in groups for node in group["nodes"]]),
        }

    def _categories(self, source: BaseSource, source_id: str) -> list[dict[str, str]]:
        """源站分区表；无静态表的源（hanime1）用库内已观测到的分区补齐。"""
        merged: dict[str, str] = {c["id"]: c["name"] for c in source.categories()}
        if not merged:
            for row in self.db.query(
                "SELECT DISTINCT category FROM library "
                "WHERE source = ? AND category IS NOT NULL AND category != '' ORDER BY category",
                (source_id,),
            ):
                merged[row["category"]] = row["category"]
        return [{"id": k, "name": v} for k, v in merged.items()]

    def _stats(self) -> dict[int, dict[str, Any]]:
        rows = self.db.query(
            "SELECT s.id AS sub_id, "
            "       COUNT(ss.video_id) AS discovered, "
            "       SUM(CASE WHEN l.key IS NULL THEN 0 ELSE 1 END) AS done "
            "FROM subscriptions s "
            "LEFT JOIN subscription_seen ss ON ss.sub_id = s.id "
            "LEFT JOIN library l ON l.key = s.source || ':' || ss.video_id "
            "GROUP BY s.id"
        )
        return {r["sub_id"]: {"discovered": r["discovered"], "done": r["done"]} for r in rows}

    # ---- 增删 ----

    def create(
        self,
        source: str,
        kind: str,
        value: str,
        interval_sec: int,
        quality: str | None,
        title: str | None = None,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        adapter = self.sources.get(source)
        if adapter is None:
            raise ValueError(f"未注册的源：{source}")
        if kind not in adapter.kinds:
            raise ValueError(f"{adapter.name} 不支持线索类型：{kind}")
        if kind not in EXPAND_KINDS and not value.strip():
            raise ValueError("该线索类型需要 value")
        if not title and kind == "partition":
            title = next(
                (c["name"] for c in adapter.categories() if c["id"] == value.strip()),
                value.strip(),
            )
        cur = self.db.execute(
            "INSERT INTO subscriptions "
            "(source, kind, value, interval_sec, quality, enabled, created_at, title, parent_id) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                source,
                kind,
                value.strip(),
                max(60, int(interval_sec)),
                quality,
                now_iso(),
                title or value.strip(),
                parent_id,
            ),
        )
        row = self.db.query_one("SELECT * FROM subscriptions WHERE id = ?", (cur.lastrowid,))
        return dict(row) if row else {}

    def delete(self, node_id: int) -> bool:
        """删除节点及其所有后代。"""
        ids = self._descendants(node_id)
        if not ids:
            return False
        marks = ",".join("?" * len(ids))
        self.db.execute(f"DELETE FROM subscription_seen WHERE sub_id IN ({marks})", tuple(ids))
        self.db.execute(f"DELETE FROM subscriptions WHERE id IN ({marks})", tuple(ids))
        return True

    def _descendants(self, root_id: int) -> list[int]:
        found: list[int] = []
        stack = [root_id]
        while stack:
            current = stack.pop()
            found.append(current)
            stack.extend(
                r["id"]
                for r in self.db.query(
                    "SELECT id FROM subscriptions WHERE parent_id = ?", (current,)
                )
            )
        return found


def _aggregate(node: dict[str, Any]) -> dict[str, int]:
    discovered = node["discovered"]
    done = node["done"]
    for child in node["children"]:
        sub = _aggregate(child)
        discovered += sub["discovered"]
        done += sub["done"]
    node["total"] = {"discovered": discovered, "done": done}
    return node["total"]


def _sum_totals(nodes: list[dict[str, Any]]) -> dict[str, int]:
    discovered = sum(n.get("total", {}).get("discovered", n["discovered"]) for n in nodes)
    done = sum(n.get("total", {}).get("done", n["done"]) for n in nodes)
    return {"discovered": discovered, "done": done}
