"""线索调度：采集器到点后依次扫描自己的线索，把新发现的作品交给下载队列。

一个「采集器」= 若干线索的并集 + 触发时机（manual | 1h | 1d | 1w | 1m）+ 业务参数（清晰度）；
一条「线索」= 一条发现规则：来源 + 分区 / 关键词 / 画师 + 数量（前 N 页或全部）。

去重分两级：
- clue_seen：每条线索自己见过的作品，用于「全部」的增量早停
- works：全局作品台账，决定是否入队（见 ledger.py）

「全部」（pages = 0）：首轮翻到底并置 scanned；之后每轮从第 1 页翻到「整页都是老面孔」即停。
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Callable

from ..db import Database, now_iso
from ..library.downloader import Downloader
from ..sources.base import BaseSource, ClueQuery, SourceError
from .ledger import Ledger, card_title, work_key

#: 触发时机 → 间隔秒数；manual 只在用户点「立即运行」时跑
TRIGGERS: dict[str, int | None] = {
    "manual": None,
    "1h": 3600,
    "1d": 86400,
    "1w": 604800,
    "1m": 2592000,
}

#: 展开类线索：不直接产出作品，而是在同一采集器内派生出发布者子线索
EXPAND_KINDS = frozenset({"favorites", "following"})
EXPAND_LABELS = {"favorites": "我的收藏", "following": "我的关注"}

#: 单轮扫描的页数硬上限（与源的 clamp_page 一致）
MAX_SCAN_PAGES = 500

EMPTY_STATS = {"discovered": 0, "done": 0, "pending": 0, "failed": 0}


def pages_label(pages: int) -> str:
    return "全部页数" if pages == 0 else f"前 {pages} 页"


def clue_title(source: BaseSource, query: ClueQuery, pages: int) -> str:
    """线索的展示标题：「裏番 · 前 5 页」「关键词 X · 全部页数」「画师 123 · 前 1 页」。"""
    if query.kind in EXPAND_KINDS:
        return EXPAND_LABELS.get(query.kind, query.kind)
    names = {item["id"]: item["name"] for item in source.categories()}
    parts: list[str] = []
    if query.uploader:
        parts.append(f"画师 {query.uploader}")
    if query.category:
        parts.append(names.get(query.category, query.category))
    if query.keyword:
        parts.append(f"关键词「{query.keyword}」")
    if not parts:
        parts.append("全部分区")
    parts.append(pages_label(pages))
    return " · ".join(parts)


class _Tally:
    """按作品键去重的计数器：同一条线索内、同一线索记忆里都不会有重复，
    但一个采集器的多条线索之间会交叉，所以用集合而不是简单相加。"""

    __slots__ = ("discovered", "done", "failed")

    def __init__(self) -> None:
        self.discovered: set[str] = set()
        self.done: set[str] = set()
        self.failed: set[str] = set()

    def add(self, key: str, in_library: bool, failed: bool) -> None:
        self.discovered.add(key)
        if in_library:
            self.done.add(key)
        elif failed:
            self.failed.add(key)

    def counts(self) -> dict[str, int]:
        return {
            "discovered": len(self.discovered),
            "done": len(self.done),
            "failed": len(self.failed),
            "pending": len(self.discovered) - len(self.done) - len(self.failed),
        }


class FetcherScheduler:
    def __init__(
        self,
        db: Database,
        sources: dict[str, BaseSource],
        downloader: Downloader,
        *,
        tick: int = 30,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.db = db
        self.sources = sources
        self.downloader = downloader
        self.ledger = Ledger(db)
        self.tick = max(5, tick)
        self._log = log or (lambda _m: None)
        self._task: asyncio.Task[None] | None = None
        self._runs: dict[int, asyncio.Task[dict[str, Any]]] = {}  # 手动触发的后台任务（持强引用）
        self._running: set[int] = set()
        self._operating = False

    # ---- 生命周期 ----

    async def start(self) -> None:
        self._operating = True
        self._reset_running()
        self._reconcile()  # 重启后内存队列是空的：没入库的作品在这里补回
        self._task = asyncio.create_task(self._loop(), name="forager-clues")

    async def stop(self) -> None:
        self._operating = False
        pending = [task for task in (self._task, *self._runs.values()) if task is not None]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._task = None
        self._runs.clear()
        self._running.clear()

    async def _loop(self) -> None:
        while self._operating:
            try:
                for fetcher_id in self._due():
                    await self._execute(fetcher_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 调度器不允许被单次失败打挂
                self._log(f"[clues] 轮询异常：{exc}")
            await asyncio.sleep(self.tick)

    async def _execute(self, fetcher_id: int) -> dict[str, Any]:
        """跑一个采集器（互斥）；定时循环与手动触发共用这一个入口。"""
        if fetcher_id in self._running:
            return {"ok": True, "started": False}
        self._running.add(fetcher_id)
        try:
            await self.run_fetcher(fetcher_id)
            return {"ok": True, "started": True}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 失败要落在采集器状态上，而不是炸掉循环
            message = _text(exc)
            self._mark_fetcher(fetcher_id, "error", message)
            self._log(f"[clues] 采集器 #{fetcher_id} 运行失败：{message}")
            return {"ok": False, "started": True, "error": message}
        finally:
            self._running.discard(fetcher_id)

    def trigger(self, fetcher_id: int) -> dict[str, Any]:
        """手动触发：立即返回，采集器在后台跑；正在跑时不重复启动。"""
        if self.db.query_one("SELECT 1 FROM fetchers WHERE id = ?", (fetcher_id,)) is None:
            raise KeyError(fetcher_id)
        if fetcher_id in self._running:
            return {"ok": True, "started": False}
        task = asyncio.create_task(
            self._execute(fetcher_id), name=f"forager-fetcher-{fetcher_id}"
        )
        self._runs[fetcher_id] = task
        task.add_done_callback(
            lambda done, fid=fetcher_id: self._runs.pop(fid, None)
            if self._runs.get(fid) is done
            else None
        )
        return {"ok": True, "started": True}

    # ---- 运行 ----

    async def run_fetcher(self, fetcher_id: int) -> dict[str, Any]:
        """依次扫描采集器下的每条线索；单条线索失败只记在它自己身上。"""
        fetcher = self.db.query_one("SELECT * FROM fetchers WHERE id = ?", (fetcher_id,))
        if fetcher is None:
            raise KeyError(fetcher_id)
        self._mark_fetcher(fetcher_id, "running", None)
        self._reconcile()

        failures: list[str] = []
        for clue in self.db.query(
            "SELECT * FROM clues WHERE fetcher_id = ? AND kind IN ('favorites', 'following') "
            "ORDER BY id",
            (fetcher_id,),
        ):
            try:
                await self._expand(fetcher, clue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._fail_clue(clue, exc, failures)

        # 展开之后重新取一遍：新生成的画师子线索这一轮就要跑
        for clue in self.db.query(
            "SELECT * FROM clues WHERE fetcher_id = ? AND kind = 'search' ORDER BY id",
            (fetcher_id,),
        ):
            try:
                stats = await self._scan(fetcher, clue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._fail_clue(clue, exc, failures)
                continue
            if stats["new"]:
                self._log(
                    f"[clues] 线索 #{clue['id']} 新发现 {stats['new']} 条，入队 {stats['queued']} 条"
                )
            self._mark_clue(clue["id"], "ok", None)

        if failures:
            self._mark_fetcher(
                fetcher_id, "error", f"{len(failures)} 条线索同步失败：{failures[0]}"
            )
        else:
            self._mark_fetcher(fetcher_id, "ok", None)
        return {"failed": len(failures)}

    def _fail_clue(self, clue: Any, exc: Exception, failures: list[str]) -> None:
        message = _text(exc)
        self._mark_clue(clue["id"], "error", message)
        failures.append(f"线索 #{clue['id']}：{message}")
        self._log(f"[clues] 线索 #{clue['id']} 同步失败：{message}")

    async def _expand(self, fetcher: Any, clue: Any) -> None:
        """「我的收藏 / 我的关注」→ 在同一采集器内派生出画师子线索（数量继承上级）。"""
        source = self.sources.get(clue["source"])
        if source is None:
            raise ValueError(f"未注册的源：{clue['source']}")
        if clue["kind"] == "favorites":
            users = await source.favorite_uploaders()
        else:
            users = await source.following_uploaders()

        pages = int(clue["pages"] or 0)
        stamp = now_iso()
        added = 0
        for user in users:
            uid = str(user.get("id") or "").strip()
            if not uid:
                continue
            exists = self.db.query_one(
                "SELECT id FROM clues WHERE fetcher_id = ? AND parent_id = ? AND uploader = ?",
                (fetcher["id"], clue["id"], uid),
            )
            if exists is not None:
                continue
            name = str(user.get("title") or "").strip()
            label = f"画师 {name}" if name and name != uid else f"画师 {uid}"
            self.db.execute(
                "INSERT INTO clues (fetcher_id, parent_id, source, kind, uploader, pages, title, "
                "scanned, enabled, created_at) VALUES (?, ?, ?, 'search', ?, ?, ?, 0, 1, ?)",
                (
                    fetcher["id"],
                    clue["id"],
                    clue["source"],
                    uid,
                    pages,
                    f"{label} · {pages_label(pages)}",
                    stamp,
                ),
            )
            added += 1
        if added:
            self._log(f"[clues] 线索 #{clue['id']} 新增 {added} 个画师子线索")

    async def _scan(self, fetcher: Any, clue: Any) -> dict[str, int]:
        """扫一条线索：翻页 → 记线索记忆 → 新作品入台账并入队。"""
        source = self.sources.get(clue["source"])
        if source is None:
            raise ValueError(f"未注册的源：{clue['source']}")
        query = ClueQuery(
            kind=clue["kind"],
            category=clue["category"],
            keyword=clue["keyword"],
            uploader=clue["uploader"],
        )
        pages = int(clue["pages"] or 0)
        incremental = pages == 0 and bool(clue["scanned"])
        known = {
            r["video_id"]
            for r in self.db.query(
                "SELECT video_id FROM clue_seen WHERE clue_id = ?", (clue["id"],)
            )
        }
        works = self.ledger.keys()
        quality = self._quality(fetcher, source.id)

        page, fresh_total, queued = 1, 0, 0
        while page <= MAX_SCAN_PAGES:
            result = await source.discover(query, page)
            if self.db.query_one("SELECT 1 FROM clues WHERE id = ?", (clue["id"],)) is None:
                return {"new": fresh_total, "queued": queued}  # 线索已被删除：不再写任何东西
            items = [item for item in (result.get("items") or []) if item.get("id")]
            if not items or int(result.get("page") or page) < page:
                break  # 到底了，或源把页号夹回了上限
            fresh = _unseen(items, known)
            new_works = [
                item for item in fresh if work_key(source.id, str(item["id"])) not in works
            ]
            stamp = now_iso()
            with self.db.transaction() as conn:  # 线索记忆与作品台账同进同退
                if fresh:
                    conn.executemany(
                        "INSERT OR IGNORE INTO clue_seen (clue_id, video_id, first_seen_at) "
                        "VALUES (?, ?, ?)",
                        [(clue["id"], str(item["id"]), stamp) for item in fresh],
                    )
                if new_works:
                    conn.executemany(
                        "INSERT OR IGNORE INTO works (source, video_id, first_seen_at, fetcher_id, "
                        "clue_id, card_json, quality, state, attempts, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)",
                        [
                            (
                                source.id,
                                str(item["id"]),
                                stamp,
                                fetcher["id"],
                                clue["id"],
                                json.dumps(item, ensure_ascii=False),
                                quality,
                                stamp,
                            )
                            for item in new_works
                        ],
                    )
            for item in new_works:  # 提交之后再入队：停机时台账里有、队列里没有的会被对账补回
                key = work_key(source.id, str(item["id"]))
                works.add(key)
                job = self.downloader.enqueue(
                    source.id, str(item["id"]), quality, title=str(item.get("title") or "")
                )
                if job.get("status") == "done":
                    self.ledger.settle(key, "done")
                queued += 1
            fresh_total += len(fresh)

            if not result.get("hasMore", True):
                break
            if incremental and not fresh:
                break  # 增量早停：整页都是这条线索的老面孔
            if pages and page >= pages:
                break
            page += 1

        if pages == 0:
            self.db.execute("UPDATE clues SET scanned = 1 WHERE id = ?", (clue["id"],))
        return {"new": fresh_total, "queued": queued}

    def _reconcile(self) -> int:
        """补投：已发现未入库、失败次数未耗尽、且不在当前下载队列里的作品重新入队。"""
        requeued = 0
        for row in self.ledger.due():
            key = work_key(row["source"], row["video_id"])
            job = self.downloader.get(key)
            if job is not None and job.get("status") in {"queued", "downloading"}:
                continue
            result = self.downloader.enqueue(
                row["source"], row["video_id"], row["quality"], title=card_title(row["card_json"])
            )
            if result.get("status") == "done":
                self.ledger.settle(key, "done")
            else:
                requeued += 1
        return requeued

    def _quality(self, fetcher: Any, source_id: str) -> str | None:
        if fetcher["quality"]:
            return fetcher["quality"]
        return self.db.get_setting(f"source.{source_id}.quality") or None

    def _due(self) -> list[int]:
        now = datetime.now(UTC)
        due: list[int] = []
        for row in self.db.query(
            "SELECT id, trigger_type, last_run_at FROM fetchers WHERE enabled = 1"
        ):
            interval = TRIGGERS.get(row["trigger_type"])
            if not interval:
                continue  # manual：只认「立即运行」
            last = row["last_run_at"]
            if not last:
                due.append(row["id"])
                continue
            try:
                elapsed = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                due.append(row["id"])
                continue
            if elapsed >= interval:
                due.append(row["id"])
        return due

    def _reset_running(self) -> None:
        """上次运行途中进程被杀会留下 running：启动时复位，否则界面一直显示在跑。"""
        self.db.execute("UPDATE fetchers SET status = 'idle' WHERE status = 'running'")
        self.db.execute("UPDATE clues SET status = 'idle' WHERE status = 'running'")

    def _mark_fetcher(self, fetcher_id: int, status: str, error: str | None) -> None:
        self.db.execute(
            "UPDATE fetchers SET last_run_at = ?, status = ?, last_error = ? WHERE id = ?",
            (now_iso(), status, error, fetcher_id),
        )

    def _mark_clue(self, clue_id: int, status: str, error: str | None) -> None:
        self.db.execute(
            "UPDATE clues SET last_run_at = ?, status = ?, last_error = ? WHERE id = ?",
            (now_iso(), status, error, clue_id),
        )

    # ---- 总览（给线索页与首页）----

    def overview(self) -> dict[str, Any]:
        stats = self._stats()
        fetchers = [
            self._fetcher_json(row, stats)
            for row in self.db.query("SELECT * FROM fetchers ORDER BY id")
        ]
        return {
            "fetchers": fetchers,
            "total": stats["total"].counts(),
            "triggers": [
                {"value": value, "label": "手动" if value == "manual" else value.upper()}
                for value in TRIGGERS
            ],
            "pageOptions": [{"value": 1, "label": "前 1 页"}, {"value": 3, "label": "前 3 页"},
                            {"value": 5, "label": "前 5 页"}, {"value": 10, "label": "前 10 页"},
                            {"value": 0, "label": "全部"}],
            "sources": [
                {
                    "id": source.id,
                    "name": source.name,
                    "kinds": list(source.kinds),
                    "fields": list(source.discover_fields),
                    "categories": source.categories(),
                    "qualityOptions": list(source.quality_options),
                }
                for source in self.sources.values()
                if source.kinds  # 本地等不可发现源不进线索
            ],
        }

    def _stats(self) -> dict[str, Any]:
        rows = self.db.query(
            "SELECT c.fetcher_id, cs.clue_id, c.source, cs.video_id, "
            "       l.key IS NOT NULL AS in_library, w.state AS work_state "
            "FROM clue_seen cs JOIN clues c ON c.id = cs.clue_id "
            "LEFT JOIN library l ON l.key = c.source || ':' || cs.video_id "
            "LEFT JOIN works w ON w.source = c.source AND w.video_id = cs.video_id"
        )
        clue: dict[int, _Tally] = defaultdict(_Tally)
        fetcher: dict[int, _Tally] = defaultdict(_Tally)
        total = _Tally()
        for row in rows:
            key = work_key(row["source"], row["video_id"])
            in_library, failed = bool(row["in_library"]), row["work_state"] == "failed"
            clue[row["clue_id"]].add(key, in_library, failed)
            fetcher[row["fetcher_id"]].add(key, in_library, failed)
            total.add(key, in_library, failed)
        return {"clue": clue, "fetcher": fetcher, "total": total}

    def _fetcher_json(self, row: Any, stats: dict[str, Any]) -> dict[str, Any]:
        tally = stats["fetcher"].get(row["id"])
        return {
            "id": row["id"],
            "name": row["name"],
            "trigger": row["trigger_type"],
            "quality": row["quality"] or "",
            "enabled": bool(row["enabled"]),
            "lastRunAt": row["last_run_at"],
            "status": row["status"] or "idle",
            "lastError": row["last_error"],
            "stats": tally.counts() if tally else dict(EMPTY_STATS),
            "clues": [
                self._clue_json(clue, stats)
                for clue in self.db.query(
                    "SELECT * FROM clues WHERE fetcher_id = ? ORDER BY id", (row["id"],)
                )
            ],
        }

    def _clue_json(self, row: Any, stats: dict[str, Any]) -> dict[str, Any]:
        source = self.sources.get(row["source"])
        query = ClueQuery(
            kind=row["kind"],
            category=row["category"],
            keyword=row["keyword"],
            uploader=row["uploader"],
        )
        title = row["title"] or (
            clue_title(source, query, row["pages"]) if source else row["source"]
        )
        tally = stats["clue"].get(row["id"])
        return {
            "id": row["id"],
            "fetcherId": row["fetcher_id"],
            "derived": row["parent_id"] is not None,
            "source": row["source"],
            "kind": row["kind"],
            "partition": row["category"] or "",
            "keyword": row["keyword"] or "",
            "uploader": row["uploader"] or "",
            "pages": row["pages"],
            "title": title,
            "scanned": bool(row["scanned"]),
            "lastRunAt": row["last_run_at"],
            "status": row["status"] or "idle",
            "lastError": row["last_error"],
            "stats": tally.counts() if tally else dict(EMPTY_STATS),
        }

    # ---- 增删 ----

    def create_fetcher(self, name: str, trigger: str, quality: str | None = None) -> dict[str, Any]:
        if trigger not in TRIGGERS:
            raise ValueError(f"未知的触发时机：{trigger}")
        cur = self.db.execute(
            "INSERT INTO fetchers (name, trigger_type, quality, enabled, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (
                (name or "").strip() or "未命名采集器",
                trigger,
                (quality or "").strip() or None,
                now_iso(),
            ),
        )
        row = self.db.query_one("SELECT * FROM fetchers WHERE id = ?", (cur.lastrowid,))
        return dict(row) if row else {}

    async def delete_fetcher(self, fetcher_id: int) -> bool:
        """删除采集器及其全部线索（作品台账保留，所以不会因此重复下载）。"""
        if self.db.query_one("SELECT 1 FROM fetchers WHERE id = ?", (fetcher_id,)) is None:
            return False
        task = self._runs.pop(fetcher_id, None)
        if task is not None:
            task.cancel()  # 正在跑的先停下，别再往已删除的线索里写东西
            await asyncio.gather(task, return_exceptions=True)
        clue_ids = [
            r["id"] for r in self.db.query("SELECT id FROM clues WHERE fetcher_id = ?", (fetcher_id,))
        ]
        with self.db.transaction() as conn:
            for clue_id in clue_ids:
                conn.execute("DELETE FROM clue_seen WHERE clue_id = ?", (clue_id,))
            conn.execute("DELETE FROM clues WHERE fetcher_id = ?", (fetcher_id,))
            conn.execute("DELETE FROM fetchers WHERE id = ?", (fetcher_id,))
        return True

    def create_clue(
        self,
        fetcher_id: int,
        source_id: str,
        *,
        kind: str = "search",
        category: str | None = None,
        keyword: str | None = None,
        uploader: str | None = None,
        pages: int = 1,
    ) -> dict[str, Any]:
        if self.db.query_one("SELECT 1 FROM fetchers WHERE id = ?", (fetcher_id,)) is None:
            raise ValueError("采集器不存在")
        source = self.sources.get(source_id)
        if source is None:
            raise ValueError(f"未注册的源：{source_id}")
        try:
            depth = int(pages)
        except (TypeError, ValueError):
            raise ValueError("数量必须是整数（0 表示全部）") from None
        if depth < 0 or depth > MAX_SCAN_PAGES:
            raise ValueError(f"数量必须在 0 到 {MAX_SCAN_PAGES} 之间（0 表示全部）")

        query = ClueQuery(
            kind=str(kind or "search"),
            category=_optional(category),
            keyword=_optional(keyword),
            uploader=_optional(uploader),
        )
        source.validate_clue(query, depth)
        if query.category:
            known = {item["id"] for item in source.categories()}
            if known and query.category not in known:
                raise SourceError(f"未知的分区：{query.category}", 400)

        cur = self.db.execute(
            "INSERT INTO clues (fetcher_id, source, kind, category, keyword, uploader, pages, "
            "title, scanned, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)",
            (
                fetcher_id,
                source.id,
                query.kind,
                query.category,
                query.keyword,
                query.uploader,
                depth,
                clue_title(source, query, depth),
                now_iso(),
            ),
        )
        row = self.db.query_one("SELECT * FROM clues WHERE id = ?", (cur.lastrowid,))
        return self._clue_json(row, self._stats()) if row else {}

    async def delete_clue(self, clue_id: int) -> bool:
        """删除线索及其扫描记忆；展开类线索会连同派生的子线索一起删。"""
        row = self.db.query_one("SELECT * FROM clues WHERE id = ?", (clue_id,))
        if row is None:
            return False
        if row["parent_id"] is not None:
            raise SourceError("派生线索请删除它的上级线索", 400)
        ids = [
            clue_id,
            *[r["id"] for r in self.db.query("SELECT id FROM clues WHERE parent_id = ?", (clue_id,))],
        ]
        with self.db.transaction() as conn:
            for cid in ids:
                conn.execute("DELETE FROM clue_seen WHERE clue_id = ?", (cid,))
                conn.execute("DELETE FROM clues WHERE id = ?", (cid,))
        return True


def _unseen(items: list[dict[str, Any]], known: set[str]) -> list[dict[str, Any]]:
    """这一页里该线索没见过的作品（顺手去掉页内重复）。"""
    out: list[dict[str, Any]] = []
    for item in items:
        video_id = str(item["id"])
        if video_id in known:
            continue
        known.add(video_id)
        out.append(item)
    return out


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _text(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__
