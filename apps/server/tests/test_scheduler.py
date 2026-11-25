"""线索调度：触发、翻页、增量早停、并集去重、补投。全部用假源，不触网。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD, login, make_settings
from forager.api.app import create_app
from forager.auth import hash_secret
from forager.clues.scheduler import FetcherScheduler, clue_title
from forager.db import Database
from forager.library.downloader import Downloader
from forager.sources.base import BaseSource, ClueQuery, SourceError
from forager.sources.hanime1 import Hanime1Source

STAMP = "2026-01-01T00:00:00Z"


def _key(query: ClueQuery) -> str:
    return f"{query.kind}|{query.category}|{query.keyword}|{query.uploader}"


class FakeSource(BaseSource):
    """按「查询条件 → 若干页 ID」喂内容的假源，并记录每次 discover 调用。"""

    id = "fake"
    name = "Fake"
    kinds = ("search", "favorites", "following")
    discover_fields = ("category", "keyword", "uploader")
    quality_options = ("1080", "720")

    def __init__(self) -> None:
        super().__init__(client=None)
        self.books: dict[str, list[list[str]]] = {}
        self.calls: list[tuple[str, int]] = []
        self.uploaders: list[dict[str, str]] = []

    def feed(self, query: ClueQuery, *pages: list[str]) -> None:
        self.books[_key(query)] = [list(page) for page in pages]

    def categories(self) -> list[dict[str, str]]:
        return [{"id": "A", "name": "分区 A"}]

    async def discover(self, query: ClueQuery, page: int = 1) -> dict:
        self.calls.append((_key(query), page))
        book = self.books.get(_key(query)) or []
        ids = book[page - 1] if page <= len(book) else []
        return {
            "items": [{"id": vid, "source": "fake", "title": f"作品 {vid}"} for vid in ids],
            "page": page,
            "hasMore": page < len(book),
        }

    async def favorite_uploaders(self) -> list[dict[str, str]]:
        return list(self.uploaders)

    async def following_uploaders(self) -> list[dict[str, str]]:
        return list(self.uploaders)


class Rig:
    def __init__(self, root, source_cls: type[FakeSource] = FakeSource) -> None:
        self.db = Database(root / "data")
        self.source = source_cls()
        self.downloader = Downloader(self.db, {"fake": self.source})  # 不启动 worker：任务停在队列里
        self.scheduler = FetcherScheduler(self.db, {"fake": self.source}, self.downloader, tick=3600)

    def fetcher(self, name: str = "采集器", trigger: str = "manual") -> dict:
        return self.scheduler.create_fetcher(name, trigger)

    def clue(self, fetcher_id: int, **params) -> dict:
        return self.scheduler.create_clue(fetcher_id, "fake", **params)

    def queued(self) -> list[str]:
        return sorted(job["key"] for job in self.downloader.list() if job["status"] == "queued")

    def add_work(self, video_id: str, state: str = "pending", attempts: int = 0) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO works (source, video_id, first_seen_at, state, attempts, "
            "card_json, quality) VALUES ('fake', ?, ?, ?, ?, ?, '1080')",
            (video_id, STAMP, state, attempts, json.dumps({"title": f"作品 {video_id}"})),
        )

    def work(self, video_id: str):
        return self.db.query_one("SELECT * FROM works WHERE video_id = ?", (video_id,))


@pytest.fixture()
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.db.close()


def _pages(rig: Rig) -> list[int]:
    return [page for _, page in rig.source.calls]


def _wait(condition, timeout: float = 5.0) -> None:
    deadline = time.perf_counter() + timeout
    while not condition() and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert condition()


async def _scan_all(tmp_path, source_cls: type[FakeSource], *pages: list[str]) -> list[int]:
    """用指定的源把一条「全部」线索跑一轮，返回翻过的页号。"""
    rig = Rig(tmp_path, source_cls)
    try:
        rig.source.feed(ClueQuery(category="A"), *pages)
        fid = rig.fetcher()["id"]
        rig.clue(fid, category="A", pages=0)
        await rig.scheduler.run_fetcher(fid)
        return _pages(rig)
    finally:
        rig.db.close()


# ---- 翻页与「全部」----


async def test_pages_caps_how_far_a_clue_scans(rig: Rig) -> None:
    query = ClueQuery(category="A")
    rig.source.feed(query, ["1", "2"], ["3", "4"], ["5", "6"])
    fid = rig.fetcher()["id"]
    clue = rig.clue(fid, category="A", pages=2)
    assert clue["title"] == "分区 A · 前 2 页"

    assert await rig.scheduler.run_fetcher(fid) == {"failed": 0}

    assert rig.source.calls == [(_key(query), 1), (_key(query), 2)]
    assert rig.queued() == ["fake:1", "fake:2", "fake:3", "fake:4"]
    assert rig.db.query_one("SELECT scanned FROM clues")["scanned"] == 0


async def test_all_scans_to_the_end_then_stops_early(rig: Rig) -> None:
    query = ClueQuery(keyword="k")
    rig.source.feed(query, ["1"], ["2"], ["3"])
    fid = rig.fetcher()["id"]
    clue = rig.clue(fid, keyword="k", pages=0)
    assert clue["title"] == "关键词「k」 · 全部页数"

    await rig.scheduler.run_fetcher(fid)  # 首轮翻到底
    assert _pages(rig) == [1, 2, 3]
    assert rig.db.query_one("SELECT scanned FROM clues")["scanned"] == 1

    await rig.scheduler.run_fetcher(fid)  # 第 1 页全是老面孔 → 立刻停
    assert _pages(rig) == [1, 2, 3, 1]

    rig.source.feed(query, ["0", "1"], ["2"], ["3"])  # 第 1 页出了新作 → 翻到第 2 页才停
    await rig.scheduler.run_fetcher(fid)
    assert _pages(rig) == [1, 2, 3, 1, 1, 2]
    assert rig.queued() == ["fake:0", "fake:1", "fake:2", "fake:3"]


async def test_scan_stops_when_the_source_clamps_the_page_number(tmp_path) -> None:
    """源把页号夹回上限却一直说「还有」：不能就此死循环。"""

    class Clamped(FakeSource):
        async def discover(self, query, page: int = 1) -> dict:
            return {**await super().discover(query, page), "page": min(page, 2), "hasMore": True}

    assert await _scan_all(tmp_path, Clamped, ["1"], ["2"], ["3"]) == [1, 2, 3]


async def test_scan_stops_on_an_empty_page_even_if_has_more(tmp_path) -> None:
    class Endless(FakeSource):
        async def discover(self, query, page: int = 1) -> dict:
            return {**await super().discover(query, page), "hasMore": True}

    assert await _scan_all(tmp_path, Endless, ["1"], ["2"]) == [1, 2, 3]


# ---- 并集去重 ----


async def test_union_dedupe_across_clues_and_fetchers(rig: Rig) -> None:
    by_category, by_keyword, by_uploader = ClueQuery(category="A"), ClueQuery(keyword="k"), ClueQuery(uploader="u")
    rig.source.feed(by_category, ["1", "2", "3"])
    rig.source.feed(by_keyword, ["3", "2", "9"])
    first = rig.fetcher("甲")["id"]
    rig.clue(first, category="A")
    rig.clue(first, keyword="k")
    await rig.scheduler.run_fetcher(first)
    assert rig.queued() == ["fake:1", "fake:2", "fake:3", "fake:9"]  # 同一采集器内交叉的 2、3 只入队一次

    rig.source.feed(by_uploader, ["1", "9", "77"])
    second = rig.fetcher("乙")["id"]
    rig.clue(second, uploader="u")
    await rig.scheduler.run_fetcher(second)
    assert rig.queued() == ["fake:1", "fake:2", "fake:3", "fake:77", "fake:9"]  # 1、9 已在台账：skip

    # 线索记忆各自独立，统计按作品去重
    seen = {r["clue_id"]: r["n"] for r in rig.db.query("SELECT clue_id, COUNT(*) AS n FROM clue_seen GROUP BY clue_id")}
    assert sorted(seen.values()) == [3, 3, 3]
    overview = rig.scheduler.overview()
    assert overview["total"] == {"discovered": 5, "done": 0, "pending": 5, "failed": 0}
    by_name = {f["name"]: f for f in overview["fetchers"]}
    assert by_name["甲"]["stats"]["discovered"] == 4
    assert by_name["乙"]["stats"]["discovered"] == 3
    assert [c["stats"]["discovered"] for c in by_name["甲"]["clues"]] == [3, 3]

    # 台账记着首次发现它的采集器 / 线索、列表页卡片摘要与清晰度
    work = rig.work("3")
    assert (work["state"], work["attempts"], work["fetcher_id"]) == ("pending", 0, first)
    assert json.loads(work["card_json"]) == {"id": "3", "source": "fake", "title": "作品 3"}


async def test_fetcher_quality_is_passed_to_every_download(rig: Rig) -> None:
    query = ClueQuery(category="A")
    rig.source.feed(query, ["1"])
    fid = rig.scheduler.create_fetcher("高清", "manual", quality="720")["id"]
    rig.clue(fid, category="A")
    await rig.scheduler.run_fetcher(fid)
    assert [job["quality"] for job in rig.downloader.list()] == ["720"]
    assert rig.work("1")["quality"] == "720"


async def test_stats_follow_the_library(rig: Rig) -> None:
    query = ClueQuery(category="A")
    rig.source.feed(query, ["1", "2"])
    fid = rig.fetcher()["id"]
    rig.clue(fid, category="A")
    await rig.scheduler.run_fetcher(fid)
    rig.db.execute(
        "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, size_bytes, "
        "downloaded_at) VALUES ('fake:1', 'fake', '1', '{}', '1080', 'f.mp4', 1, ?)",
        (STAMP,),
    )
    rig.scheduler.ledger.settle("fake:2", "failed", "x")
    rig.scheduler.ledger.settle("fake:2", "failed", "x")
    rig.scheduler.ledger.settle("fake:2", "failed", "x")
    overview = rig.scheduler.overview()
    assert overview["total"] == {"discovered": 2, "done": 1, "pending": 0, "failed": 1}
    assert overview["fetchers"][0]["clues"][0]["stats"] == overview["total"]
    assert overview["fetchers"][0]["lastError"] is None


# ---- 展开类线索 ----


async def test_expand_derives_uploader_clues_inside_the_same_fetcher(rig: Rig) -> None:
    rig.source.uploaders = [{"id": "100", "title": "画师甲"}, {"id": "200", "title": "画师乙"}]
    rig.source.feed(ClueQuery(uploader="100"), ["1", "2"])
    rig.source.feed(ClueQuery(uploader="200"), ["8"])
    fid = rig.fetcher("丙")["id"]
    expander = rig.scheduler.create_clue(fid, "fake", kind="favorites", pages=1)
    assert expander["title"] == "我的收藏"

    await rig.scheduler.run_fetcher(fid)

    children = [dict(r) for r in rig.db.query("SELECT * FROM clues WHERE parent_id = ?", (expander["id"],))]
    assert [(c["uploader"], c["pages"], c["fetcher_id"], c["title"]) for c in children] == [
        ("100", 1, fid, "画师 画师甲 · 前 1 页"),
        ("200", 1, fid, "画师 画师乙 · 前 1 页"),
    ]
    assert rig.queued() == ["fake:1", "fake:2", "fake:8"]

    await rig.scheduler.run_fetcher(fid)  # 再跑一轮不会重复派生
    assert rig.db.query_one("SELECT COUNT(*) AS n FROM clues")["n"] == 3
    assert [c["derived"] for c in rig.scheduler.overview()["fetchers"][0]["clues"]] == [False, True, True]

    with pytest.raises(SourceError, match="派生"):
        await rig.scheduler.delete_clue(children[0]["id"])
    await rig.scheduler.delete_clue(expander["id"])  # 删上级才级联
    assert rig.db.query("SELECT * FROM clues") == []
    assert rig.db.query("SELECT * FROM clue_seen") == []
    assert rig.db.query_one("SELECT COUNT(*) AS n FROM works")["n"] == 3  # 台账保留


# ---- 触发 ----


def test_only_scheduled_fetchers_become_due(rig: Rig) -> None:
    manual = rig.fetcher("m", "manual")["id"]
    hourly = rig.fetcher("h", "1h")["id"]
    assert rig.scheduler._due() == [hourly]  # 从没跑过的到点就跑；manual 永不到期

    def ran(seconds_ago: int) -> None:
        stamp = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")
        rig.db.execute("UPDATE fetchers SET last_run_at = ? WHERE id = ?", (stamp, hourly))

    ran(3599)
    assert rig.scheduler._due() == []
    ran(3601)
    assert rig.scheduler._due() == [hourly]
    rig.db.execute("UPDATE fetchers SET enabled = 0 WHERE id = ?", (hourly,))
    assert rig.scheduler._due() == []
    assert manual


def test_startup_resets_stale_running_status(rig: Rig) -> None:
    fid = rig.fetcher()["id"]
    rig.db.execute("UPDATE fetchers SET status = 'running' WHERE id = ?", (fid,))
    rig.scheduler._reset_running()
    assert rig.scheduler.overview()["fetchers"][0]["status"] == "idle"


async def test_manual_trigger_runs_in_background_and_is_exclusive(rig: Rig) -> None:
    query = ClueQuery(category="A")
    rig.source.feed(query, ["1"])
    fid = rig.fetcher()["id"]
    rig.clue(fid, category="A")
    gate = asyncio.Event()
    original = rig.source.discover

    async def gated(query, page: int = 1) -> dict:
        await gate.wait()
        return await original(query, page)

    rig.source.discover = gated
    assert rig.scheduler.trigger(fid) == {"ok": True, "started": True}
    await asyncio.sleep(0.01)
    assert rig.scheduler.trigger(fid) == {"ok": True, "started": False}  # 正在跑：不重复启动
    assert rig.db.query_one("SELECT status FROM fetchers WHERE id = ?", (fid,))["status"] == "running"

    gate.set()
    deadline = time.perf_counter() + 5
    while rig.db.query_one("SELECT status FROM fetchers WHERE id = ?", (fid,))["status"] != "ok":
        assert time.perf_counter() < deadline
        await asyncio.sleep(0.01)
    assert rig.queued() == ["fake:1"]
    with pytest.raises(KeyError):
        rig.scheduler.trigger(999)


async def test_deleting_a_fetcher_mid_scan_leaves_no_orphans(rig: Rig) -> None:
    query = ClueQuery(category="A")
    rig.source.feed(query, ["1"], ["2"])
    fid = rig.fetcher()["id"]
    rig.clue(fid, category="A", pages=0)
    gate = asyncio.Event()
    original = rig.source.discover

    async def gated(query, page: int = 1) -> dict:
        await gate.wait()
        return await original(query, page)

    rig.source.discover = gated
    task = asyncio.create_task(rig.scheduler.run_fetcher(fid))  # 模拟定时循环里的运行：取消不到
    await asyncio.sleep(0.01)

    assert await rig.scheduler.delete_fetcher(fid) is True
    gate.set()
    await task

    assert rig.db.query("SELECT * FROM clues") == []
    assert rig.db.query("SELECT * FROM clue_seen") == []
    assert rig.db.query("SELECT * FROM works") == []
    assert rig.queued() == []
    assert await rig.scheduler.delete_fetcher(fid) is False


async def test_delete_fetcher_cancels_a_manual_run(rig: Rig) -> None:
    query = ClueQuery(category="A")
    rig.source.feed(query, ["1"])
    fid = rig.fetcher()["id"]
    rig.clue(fid, category="A")
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def never(query, page: int = 1) -> dict:
        entered.set()
        await gate.wait()
        return {"items": [], "page": page, "hasMore": False}

    rig.source.discover = never
    rig.scheduler.trigger(fid)
    await asyncio.wait_for(entered.wait(), 5)
    assert await rig.scheduler.delete_fetcher(fid) is True  # 先取消再删，调用方不会一直等
    gate.set()


# ---- 中断续跑与补投 ----


async def test_seen_and_ledger_are_committed_before_enqueue(rig: Rig) -> None:
    query = ClueQuery(keyword="k")
    rig.source.feed(query, ["1", "2"], ["3"])
    fid = rig.fetcher()["id"]
    rig.clue(fid, keyword="k", pages=0)
    original_enqueue = rig.downloader.enqueue

    def broken(*args, **kwargs):
        raise RuntimeError("入队失败（模拟提交之后、入队之前停机）")

    rig.downloader.enqueue = broken
    assert await rig.scheduler.run_fetcher(fid) == {"failed": 1}
    overview = rig.scheduler.overview()
    assert (overview["fetchers"][0]["status"], overview["fetchers"][0]["clues"][0]["status"]) == ("error", "error")
    # 线索记忆与作品台账已经一起落库，不会出现「已见但不在台账」
    assert sorted(r["video_id"] for r in rig.db.query("SELECT video_id FROM clue_seen")) == ["1", "2"]
    assert sorted(r["video_id"] for r in rig.db.query("SELECT video_id FROM works")) == ["1", "2"]
    assert rig.db.query_one("SELECT scanned FROM clues")["scanned"] == 0

    rig.downloader.enqueue = original_enqueue
    await rig.scheduler.run_fetcher(fid)  # 续跑：首轮没扫完，第 1 页全是老面孔也要继续往下翻
    assert _pages(rig) == [1, 1, 2]
    assert rig.queued() == ["fake:1", "fake:2", "fake:3"]
    assert rig.db.query_one("SELECT COUNT(*) AS n FROM works")["n"] == 3  # 不重不漏
    assert rig.scheduler.overview()["fetchers"][0]["status"] == "ok"


def test_reconcile_requeues_only_pending_under_the_failure_cap(rig: Rig) -> None:
    for video_id, state, attempts in (
        ("1", "pending", 0), ("2", "pending", 2), ("3", "failed", 3),
        ("4", "cancelled", 0), ("5", "removed", 0), ("6", "done", 0),
    ):
        rig.add_work(video_id, state, attempts)

    assert rig.scheduler._reconcile() == 2
    assert rig.queued() == ["fake:1", "fake:2"]
    assert [job["title"] for job in rig.downloader.list()] == ["作品 1", "作品 2"]  # 卡片标题占位
    assert [job["quality"] for job in rig.downloader.list()] == ["1080", "1080"]

    assert rig.scheduler._reconcile() == 0  # 都还在队列里：跳过，不重复入队
    assert len(rig.downloader.list()) == 2


def test_ledger_counts_failures_not_enqueues(rig: Rig) -> None:
    """重启会让内存队列清空：未完成的作品必须能无惩罚地重新入队，所以次数只记失败。"""
    rig.add_work("1")
    ledger = rig.scheduler.ledger
    for _ in range(2):
        ledger.settle("fake:1", "failed", "boom")
    assert (rig.work("1")["state"], rig.work("1")["attempts"]) == ("pending", 2)
    assert rig.work("1")["last_error"] == "boom"

    ledger.settle("fake:1", "failed", "boom")
    assert (rig.work("1")["state"], rig.work("1")["attempts"]) == ("failed", 3)
    ledger.settle("fake:1", "failed", "迟到的回调")  # 已耗尽：不再累加
    assert (rig.work("1")["state"], rig.work("1")["attempts"]) == ("failed", 3)
    assert ledger.due() == []

    ledger.settle("fake:1", "done")  # 手动重试成功
    assert rig.work("1")["state"] == "done"
    ledger.settle("fake:404", "done")  # 不在台账里的（手动下载）静默忽略

    rig.add_work("2")
    ledger.settle("fake:2", "cancelled", "已取消")
    assert rig.work("2")["state"] == "cancelled"
    ledger.mark_removed("fake:2")
    assert rig.work("2")["state"] == "removed"


async def test_startup_reconcile_restores_the_queue_after_a_restart(rig: Rig) -> None:
    rig.add_work("1")
    rig.add_work("2")
    rig.add_work("3", state="done")
    rig.add_work("4", state="cancelled")

    # 模拟重启：新的进程，内存队列是空的
    fresh_downloader = Downloader(rig.db, {"fake": rig.source})
    fresh = FetcherScheduler(rig.db, {"fake": rig.source}, fresh_downloader, tick=3600)
    await fresh.start()
    try:
        assert sorted(job["key"] for job in fresh_downloader.list()) == ["fake:1", "fake:2"]
        assert [rig.work(v)["attempts"] for v in ("1", "2")] == [0, 0]
    finally:
        await fresh.stop()


# ---- 端到端：失败 → 补投 → 耗尽 ----


def test_failed_downloads_are_retried_on_later_runs_until_the_cap(tmp_path, monkeypatch) -> None:
    app = create_app(make_settings(tmp_path))

    discover_calls: list[int] = []

    async def fake_discover(self, query, page: int = 1) -> dict:
        discover_calls.append(page)
        return {"items": [{"id": "2747", "source": "hanime1", "title": "某作"}], "page": 1, "hasMore": False}

    async def gone(self, vid):
        raise SourceError("作品已下架", 404)

    monkeypatch.setattr(Hanime1Source, "discover", fake_discover)
    monkeypatch.setattr(Hanime1Source, "detail", gone)

    def work() -> dict:
        row = app.state.db.query_one("SELECT * FROM works WHERE video_id = '2747'")
        return dict(row) if row else {}

    def job_started_at() -> str:
        jobs = app.state.downloader.snapshot(keys=["hanime1:2747"])["jobs"]
        return jobs[0].get("startedAt", "")

    with TestClient(app) as client:
        # lifespan 里 bootstrap 生成的密码只在日志里：直接改库再走真实登录接口
        app.state.db.execute(
            "UPDATE users SET password_hash = ? WHERE id = 1", (hash_secret(TEST_PASSWORD),)
        )
        login(client)
        fid = client.post("/api/fetchers", json={"name": "f"}).json()["id"]
        client.post("/api/clues", json={"fetcherId": fid, "source": "hanime1", "keyword": "k"})

        for attempt in (1, 2, 3):
            assert client.post(f"/api/fetchers/{fid}/run").json() == {"ok": True, "started": True}
            _wait(lambda: work().get("attempts") == attempt)
        assert (work()["state"], work()["last_error"]) == ("failed", "作品已下架")

        overview = client.get("/api/fetchers").json()
        clue = overview["fetchers"][0]["clues"][0]
        assert clue["stats"] == {"discovered": 1, "done": 0, "pending": 0, "failed": 1}
        downloads = client.get("/api/library/downloads").json()
        assert downloads["counts"]["error"] == 1  # 同一任务的多次重试共用一条记录
        assert [job["title"] for job in downloads["jobs"]] == ["某作"]  # 入队时就有卡片标题

        started_at = job_started_at()
        client.post(f"/api/fetchers/{fid}/run")  # 耗尽之后不再补投
        _wait(lambda: len(discover_calls) == 4)  # 这一轮确实跑到了扫描阶段
        time.sleep(0.3)
        assert job_started_at() == started_at  # 没有再发起下载
        assert work()["attempts"] == 3


def test_clue_title_examples() -> None:
    source = FakeSource()
    assert clue_title(source, ClueQuery(), 1) == "全部分区 · 前 1 页"
    assert clue_title(source, ClueQuery(category="A", keyword="x"), 0) == "分区 A · 关键词「x」 · 全部页数"
    assert clue_title(source, ClueQuery(uploader="123"), 5) == "画师 123 · 前 5 页"
    assert clue_title(source, ClueQuery(kind="following"), 1) == "我的关注"
