"""下载队列（移植自 libre-tv 的 library/downloader.ts）。

产物：media/<source>-<id>-<quality>.mp4 + covers/<...>.<ext> + library 表一行（含全量元数据快照）。
约束：
- 一律 `.part` 临时文件 + 原子重命名，半成品不得入库
- 上游给 content-length 时校验大小，不足即判失败并清理
- 封面抓取失败不阻断下载（降级为无封面）
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from typing import Any, Callable

from ..db import Database, now_iso
from ..sources.base import BaseSource


def _file_stem(raw: str) -> str:
    return re.sub(r"[^\w.-]+", "_", raw)


class Downloader:
    def __init__(
        self,
        db: Database,
        sources: dict[str, BaseSource],
        *,
        concurrency: int = 1,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.db = db
        self.sources = sources
        self.concurrency = max(1, concurrency)
        self._log = log or (lambda _msg: None)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False

    # ---- 生命周期 ----

    async def start(self) -> None:
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(), name=f"forager-download-{i}")
            for i in range(self.concurrency)
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._workers.clear()

    # ---- 对外接口 ----

    def enqueue(self, source: str, vid: str, quality: str | None = None) -> dict[str, Any]:
        key = f"{source}:{vid}"
        existing = self._jobs.get(key)
        if existing and existing["status"] in {"queued", "downloading"}:
            return existing
        job: dict[str, Any] = {
            "key": key,
            "source": source,
            "id": vid,
            "title": "",
            "status": "queued",
            "quality": quality or "auto",
            "progress": 0,
            "receivedBytes": 0,
        }
        self._jobs[key] = job
        self._queue.put_nowait(key)
        return job

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._jobs.values(), key=lambda j: j.get("startedAt", ""), reverse=True)

    def get(self, key: str) -> dict[str, Any] | None:
        return self._jobs.get(key)

    def cancel(self, key: str) -> bool:
        job = self._jobs.get(key)
        if job is None:
            return False
        if job["status"] == "queued":
            job["status"] = "error"
            job["error"] = "已取消"
            return True
        if job["status"] == "downloading":
            task = self._tasks.get(key)
            if task is not None:
                task.cancel()
                return True
        return False

    def delete(self, key: str) -> bool:
        row = self.db.query_one("SELECT file_name, cover_file FROM library WHERE key = ?", (key,))
        if row is None:
            return False
        for name in (row["file_name"], row["cover_file"]):
            if name:
                with contextlib.suppress(ValueError, FileNotFoundError):
                    self.db.media_path(name).unlink()
        self.db.execute("DELETE FROM library WHERE key = ?", (key,))
        self._jobs.pop(key, None)
        return True

    # ---- 工作循环 ----

    async def _worker(self) -> None:
        while self._running:
            try:
                key = await self._queue.get()
            except asyncio.CancelledError:
                return
            job = self._jobs.get(key)
            if job is None or job["status"] != "queued":
                continue
            task = asyncio.create_task(self._run(job), name=f"forager-job-{key}")
            self._tasks[key] = task
            try:
                await task
            except asyncio.CancelledError:
                pass  # 单个任务被取消，工作循环继续
            finally:
                self._tasks.pop(key, None)

    async def _run(self, job: dict[str, Any]) -> None:
        part_path = None
        try:
            job["status"] = "downloading"
            job["startedAt"] = now_iso()

            source = self.sources.get(job["source"])
            if source is None:
                raise RuntimeError(f"未注册的源：{job['source']}")

            # 1) 全量元数据快照
            detail = await source.detail(job["id"])
            job["title"] = detail.get("title") or job["id"]
            quality = job["quality"]
            if quality == "auto":
                qualities = detail.get("qualities") or []
                quality = qualities[0]["label"] if qualities else "auto"
                job["quality"] = quality

            stem = _file_stem(f"{job['source']}-{job['id']}-{quality}")
            file_name = f"{stem}.mp4"
            target = self.db.media_path(file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            part_path = target.with_name(target.name + ".part")

            # 2) 流式下载
            stream = await source.open_stream(job["id"], quality)
            try:
                total = stream.content_length
                job["totalBytes"] = total
                received = 0
                with open(part_path, "wb") as handle:
                    async for chunk in stream.iter_chunks():
                        handle.write(chunk)
                        received += len(chunk)
                        job["receivedBytes"] = received
                        job["progress"] = min(99, received * 100 // total) if total else 0
            finally:
                await stream.aclose()

            size = part_path.stat().st_size
            if total and size < total:
                raise RuntimeError(f"下载不完整：{size}/{total} 字节")
            os.replace(part_path, target)
            part_path = None

            # 3) 封面（失败不阻断）
            cover_file = await self._save_cover(source, detail.get("coverUrl"), stem)

            # 4) 入库（元数据全量快照）
            item_size = target.stat().st_size
            self.db.execute(
                "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
                "cover_file, size_bytes, downloaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET meta_json = excluded.meta_json, "
                "quality = excluded.quality, file_name = excluded.file_name, "
                "cover_file = excluded.cover_file, size_bytes = excluded.size_bytes, "
                "downloaded_at = excluded.downloaded_at",
                (
                    job["key"], job["source"], job["id"],
                    __import__("json").dumps(detail, ensure_ascii=False),
                    quality, file_name, cover_file, item_size, now_iso(),
                ),
            )

            job["progress"] = 100
            job["status"] = "done"
            job["finishedAt"] = now_iso()
            self._log(f"[download] {job['key']} 完成 {item_size / 1048576:.1f}MB")

        except asyncio.CancelledError:
            self._cleanup_part(part_path)
            job["status"] = "error"
            job["error"] = "已取消"
            raise
        except Exception as exc:  # noqa: BLE001 —— 任何失败都要落成任务状态
            self._cleanup_part(part_path)
            job["status"] = "error"
            job["error"] = str(exc) or exc.__class__.__name__
            self._log(f"[download] {job['key']} 失败：{job['error']}")

    async def _save_cover(self, source: BaseSource, url: str | None, stem: str) -> str | None:
        if not url:
            return None
        try:
            data = await source.fetch_image(url)
            ext = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
            name = f"covers/{stem}.{ext}"
            path = self.db.media_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return name
        except Exception as exc:  # noqa: BLE001 —— 封面失败不影响主流程
            self._log(f"[download] 封面失败（忽略）：{exc}")
            return None

    @staticmethod
    def _cleanup_part(part_path) -> None:
        if part_path is not None:
            with contextlib.suppress(FileNotFoundError):
                part_path.unlink()
