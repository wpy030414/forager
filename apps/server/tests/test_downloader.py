"""下载队列状态机：失败必须落成任务状态并清理半成品。"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from forager.db import Database
from forager.library.downloader import Downloader
from forager.sources.base import BaseSource


class BoomSource(BaseSource):
    id = "boom"
    name = "Boom"

    async def detail(self, vid):  # noqa: ANN001
        raise RuntimeError("详情炸了")


class FakeStream:
    """假的 curl 流：按块吐数据，可选在某一块之后永远挂住（模拟慢速大文件）。"""

    def __init__(self, chunks, *, hang_after: int | None = None) -> None:
        self.status = 200
        self.headers: dict[str, str] = {}
        self.content_length = None
        self._chunks = chunks
        self._hang_after = hang_after

    async def iter_chunks(self, size: int = 65536):
        for index, chunk in enumerate(self._chunks):
            yield chunk
            if self._hang_after == index:
                await asyncio.Event().wait()

    async def aclose(self) -> None:
        return None


DETAIL = {
    "id": "7",
    "source": "vid",
    "title": "A & B <Title>",
    "pageUrl": "https://x/watch?v=7",
    "streamUrl": "https://x/7-1080p.mp4?secure=a",
    "streamProxyUrl": "/api/sources/vid/stream?v=7",
    "qualities": [{"label": "1080", "url": "https://x/7-1080p.mp4?secure=a"}],
    "related": [{"id": "8", "source": "vid", "title": "相关推荐"}],
    "tags": ["裏番"],
    "category": "裏番",
    "coverUrl": "https://x/7.jpg",
    "uploader": "Studio",
    "description": "简介",
    "uploadDate": "2026-06-01",
    "views": 5767,
    "durationSec": 671,
}


class VideoSource(BaseSource):
    id = "vid"
    name = "Vid"

    def __init__(self, detail: dict, *, hang: bool = False) -> None:
        super().__init__(client=None)
        self._detail = detail
        self._hang = hang

    async def detail(self, vid):  # noqa: ANN001
        return dict(self._detail)

    async def open_stream_url(self, url):  # noqa: ANN001
        return FakeStream([b"vid", b"eo"], hang_after=0 if self._hang else None)

    async def fetch_image(self, url):  # noqa: ANN001
        return b"\xff\xd8\xff\xe0jpeg"


async def _until(job: dict, status: str, timeout: float = 5.0) -> None:
    deadline = time.perf_counter() + timeout
    while job["status"] != status and time.perf_counter() < deadline:
        await asyncio.sleep(0.01)
    assert job["status"] == status, job


def _files(db: Database) -> list[str]:
    return sorted(p.name for p in db.media_dir.iterdir() if p.is_file())


@pytest.mark.asyncio
async def test_failed_download_sets_error(tmp_path) -> None:
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {"boom": BoomSource(client=None)}, concurrency=1)
    await downloader.start()
    try:
        job = downloader.enqueue("boom", "1")
        for _ in range(100):
            await asyncio.sleep(0.01)
            if job["status"] == "error":
                break
        assert job["status"] == "error"
        assert "详情炸了" in job["error"]
    finally:
        await downloader.stop()
        db.close()


@pytest.mark.asyncio
async def test_cancel_queued_job(tmp_path) -> None:
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {}, concurrency=1)  # 不启动 worker，任务停在队列里
    job = downloader.enqueue("ghost", "1")
    assert downloader.cancel(job["key"]) is True
    assert job["status"] == "error"
    assert job["error"] == "已取消"
    db.close()


def test_enqueue_is_idempotent(tmp_path) -> None:
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {}, concurrency=1)
    first = downloader.enqueue("ghost", "1")
    second = downloader.enqueue("ghost", "1")
    assert first is second
    db.close()


def test_enqueue_keeps_card_title_as_placeholder(tmp_path) -> None:
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {}, concurrency=1)
    assert downloader.enqueue("ghost", "1", title="列表页标题")["title"] == "列表页标题"
    assert downloader.enqueue("ghost", "2")["title"] == ""
    db.close()


def test_enqueue_skips_items_already_in_library(tmp_path) -> None:
    db = Database(tmp_path / "data")
    db.execute(
        "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
        "cover_file, size_bytes, downloaded_at, category) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
        ("ghost:1", "ghost", "1", '{"title": "旧条目"}', "480P", "ghost-1-480P.mp4", 1, "2026-01-01T00:00:00Z", "测试"),
    )
    downloader = Downloader(db, {}, concurrency=1)

    job = downloader.enqueue("ghost", "1", "1080P")

    assert job["status"] == "done"
    assert job["fromLibrary"] is True
    assert job["quality"] == "480P"
    assert job["title"] == "旧条目"
    assert downloader._queue.empty()
    db.close()


@pytest.mark.asyncio
async def test_download_puts_poster_and_sidecars_next_to_the_media(tmp_path) -> None:
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {"vid": VideoSource(DETAIL)}, concurrency=1)
    settled: list[tuple] = []
    downloader.on_settled = lambda *event: settled.append(event)
    await downloader.start()
    try:
        job = downloader.enqueue("vid", "7", "1080", title="占位标题")
        await _until(job, "done")
    finally:
        await downloader.stop()

    assert _files(db) == [
        "vid-7-1080-poster.jpg",
        "vid-7-1080.info.json",
        "vid-7-1080.mp4",
        "vid-7-1080.nfo",
    ]
    assert job["title"] == "A & B <Title>"  # 取到详情后覆盖占位标题
    assert settled == [("vid:7", "done", None)]

    row = db.query_one("SELECT * FROM library WHERE key = 'vid:7'")
    assert row["cover_file"] == "vid-7-1080-poster.jpg"
    meta = json.loads(row["meta_json"])
    assert meta == json.loads((db.media_dir / "vid-7-1080.info.json").read_text("utf-8"))
    # 入库快照是页面元信息：会过期的签名直链与「当时的相关推荐」不在里面
    assert not {"streamUrl", "streamProxyUrl", "qualities", "related"} & set(meta)
    assert (meta["title"], meta["views"], meta["uploadDate"]) == ("A & B <Title>", 5767, "2026-06-01")
    nfo = (db.media_dir / "vid-7-1080.nfo").read_text("utf-8")
    assert "<title>A &amp; B &lt;Title&gt;</title>" in nfo
    assert "vid-7-1080-poster.jpg" in nfo

    # 删除：媒体、海报、元信息文件一起清掉，并通知作品台账
    removed: list[str] = []
    downloader.on_removed = removed.append
    assert downloader.delete("vid:7") is True
    assert _files(db) == []
    assert removed == ["vid:7"]
    assert db.query_one("SELECT * FROM library WHERE key = 'vid:7'") is None
    db.close()


@pytest.mark.asyncio
async def test_delete_removes_legacy_cover_under_covers_dir(tmp_path) -> None:
    db = Database(tmp_path / "data")
    for name in ("a.mp4", "covers/a.jpg"):
        (db.media_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (db.media_dir / name).write_bytes(b"x")
    db.execute(
        "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
        "cover_file, size_bytes, downloaded_at, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        ("hanime1:1", "hanime1", "1", "{}", "1080", "a.mp4", "covers/a.jpg", 1, "2026-01-01T00:00:00Z"),
    )
    downloader = Downloader(db, {}, concurrency=1)
    assert downloader.delete("hanime1:1") is True
    assert not (db.media_dir / "a.mp4").exists()
    assert not (db.media_dir / "covers" / "a.jpg").exists()
    db.close()


@pytest.mark.asyncio
async def test_upsert_that_replaces_an_old_file_removes_it(tmp_path) -> None:
    """入队之后库里才出现旧清晰度的行（并发入队 / 另一条线索先下完）：以新的为准，旧文件不留。"""
    db = Database(tmp_path / "data")
    for name in ("vid-7-720.mp4", "vid-7-720.info.json", "vid-7-720.nfo", "vid-7-720-poster.jpg"):
        (db.media_dir / name).write_bytes(b"old")
    downloader = Downloader(db, {"vid": VideoSource(DETAIL)}, concurrency=1)
    job = downloader.enqueue("vid", "7", "1080")  # 先入队：此时库里还没有这一条
    assert job["status"] == "queued"
    db.execute(
        "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
        "cover_file, size_bytes, downloaded_at, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            "vid:7", "vid", "7", "{}", "720", "vid-7-720.mp4", "vid-7-720-poster.jpg",
            4, "2026-01-01T00:00:00Z",
        ),
    )
    await downloader.start()
    try:
        await _until(job, "done")
    finally:
        await downloader.stop()

    assert _files(db) == [
        "vid-7-1080-poster.jpg",
        "vid-7-1080.info.json",
        "vid-7-1080.mp4",
        "vid-7-1080.nfo",
    ]
    assert db.query_one("SELECT quality FROM library WHERE key = 'vid:7'")["quality"] == "1080"
    db.close()


@pytest.mark.asyncio
async def test_settle_outcomes(tmp_path) -> None:
    events: list[tuple] = []
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {"boom": BoomSource(client=None)}, concurrency=1)
    downloader.on_settled = lambda *event: events.append(event)
    await downloader.start()
    try:
        await _until(downloader.enqueue("boom", "1"), "error")
    finally:
        await downloader.stop()
    assert events == [("boom:1", "failed", "详情炸了")]

    idle = Downloader(db, {}, concurrency=1)  # 不启动 worker：任务停在队列里
    idle.on_settled = lambda *event: events.append(event)
    job = idle.enqueue("ghost", "1")
    idle.cancel(job["key"])
    assert events[-1] == ("ghost:1", "cancelled", "已取消")

    assert idle.cancel("ghost:nope") is False
    db.close()


@pytest.mark.asyncio
async def test_shutdown_interrupt_is_not_a_settlement(tmp_path) -> None:
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {"vid": VideoSource(DETAIL, hang=True)}, concurrency=1)
    events: list[tuple] = []
    downloader.on_settled = lambda *event: events.append(event)
    await downloader.start()
    job = downloader.enqueue("vid", "7", "1080")
    deadline = time.perf_counter() + 5
    while job["receivedBytes"] == 0 and time.perf_counter() < deadline:
        await asyncio.sleep(0.01)
    await downloader.stop()  # 停机：不算用户取消，不该把作品标成 cancelled
    assert job["status"] == "error"
    assert events == []
    assert _files(db) == []  # 半成品已清理
    db.close()


def test_snapshot_trims_but_keeps_failures_visible(tmp_path) -> None:
    db = Database(tmp_path / "data")
    downloader = Downloader(db, {}, concurrency=1)
    for index in range(300):
        downloader.enqueue("ghost", f"q{index}")
    for name in ("e0", "e1", "e2"):
        downloader.enqueue("ghost", name)["status"] = "error"

    snap = downloader.snapshot(limit=20)
    assert snap["counts"] == {"queued": 300, "downloading": 0, "done": 0, "error": 3}
    assert len(snap["jobs"]) == 20
    shown = {job["key"] for job in snap["jobs"]}
    assert {"ghost:e0", "ghost:e1", "ghost:e2"} <= shown  # 失败的不能被排队的挤掉

    exact = downloader.snapshot(keys=["ghost:q5", "ghost:nope"])
    assert [job["key"] for job in exact["jobs"]] == ["ghost:q5"]
    db.close()
