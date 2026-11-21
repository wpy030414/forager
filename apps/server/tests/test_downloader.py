"""下载队列状态机：失败必须落成任务状态并清理半成品。"""

from __future__ import annotations

import asyncio

import pytest

from forager.db import Database
from forager.library.downloader import Downloader
from forager.sources.base import BaseSource


class BoomSource(BaseSource):
    id = "boom"
    name = "Boom"

    async def detail(self, vid):  # noqa: ANN001
        raise RuntimeError("详情炸了")


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
