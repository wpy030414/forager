"""订阅调度器。

语义：
- 订阅 = (source, kind, value, interval_sec)。kind=query 走 search，kind=genre 走 list。
- 每轮只取第 1 页（最新），与 subscription_seen 做差集，新条目入队下载。
- 首次运行：只入队最新 N 条（FORAGER_SUB_INITIAL），其余仅记为已见，避免订阅一个分区就拉全站。
- 「已见」是永久的：同一个视频对一个订阅只会入队一次。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any, Callable

from ..db import Database, now_iso
from ..library.downloader import Downloader
from ..sources.base import BaseSource


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
        self._task = asyncio.create_task(self._loop(), name="forager-scheduler")

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
                self._log(f"[sub] 轮询异常：{exc}")
            await asyncio.sleep(self.tick)

    # ---- 单轮 ----

    async def run_once(self) -> list[dict[str, Any]]:
        """跑一轮：返回本轮被入队的视频（便于手动触发时给前端反馈）。"""
        enqueued: list[dict[str, Any]] = []
        for row in self._due():
            sub_id = row["id"]
            source = self.sources.get(row["source"])
            if source is None:
                self._log(f"[sub] 未注册的源：{row['source']}（订阅 #{sub_id}）")
                continue

            seen = {
                r["video_id"]
                for r in self.db.query(
                    "SELECT video_id FROM subscription_seen WHERE sub_id = ?", (sub_id,)
                )
            }
            first_run = not seen

            try:
                page = await self._fetch(source, row["kind"], row["value"])
            except Exception as exc:  # noqa: BLE001
                self._log(f"[sub] #{sub_id} 抓取失败：{exc}")
                self.db.execute(
                    "UPDATE subscriptions SET last_run_at = ? WHERE id = ?",
                    (now_iso(), sub_id),
                )
                continue

            items = page.get("items") or []
            fresh = [item for item in items if item["id"] not in seen]
            fresh = fresh[: self.initial] if first_run else fresh

            for item in fresh:
                self.downloader.enqueue(row["source"], item["id"], row["quality"])
                enqueued.append({"subId": sub_id, "source": row["source"], "id": item["id"]})

            if items:
                stamp = now_iso()
                self.db.executemany(
                    "INSERT OR IGNORE INTO subscription_seen (sub_id, video_id, first_seen_at) "
                    "VALUES (?, ?, ?)",
                    [(sub_id, item["id"], stamp) for item in items],
                )
            self.db.execute(
                "UPDATE subscriptions SET last_run_at = ? WHERE id = ?", (now_iso(), sub_id)
            )
            if fresh:
                self._log(f"[sub] #{sub_id} 新增 {len(fresh)} 条入队")

        return enqueued

    async def _fetch(self, source: BaseSource, kind: str, value: str) -> dict[str, Any]:
        if kind == "query":
            return await source.search(value, 1)
        return await source.list(1, value)

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

    # ---- 增删查（供 API 使用）----

    def create(self, source: str, kind: str, value: str, interval_sec: int, quality: str | None) -> dict[str, Any]:
        if kind not in {"query", "genre"}:
            raise ValueError("kind 只能是 query 或 genre")
        cur = self.db.execute(
            "INSERT INTO subscriptions (source, kind, value, interval_sec, quality, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (source, kind, value, max(60, int(interval_sec)), quality, now_iso()),
        )
        row = self.db.query_one("SELECT * FROM subscriptions WHERE id = ?", (cur.lastrowid,))
        return dict(row) if row else {}

    def delete(self, sub_id: int) -> bool:
        cur = self.db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        self.db.execute("DELETE FROM subscription_seen WHERE sub_id = ?", (sub_id,))
        return cur.rowcount > 0

    def list_all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM subscriptions ORDER BY id DESC")]
