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
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from ..db import Database, now_iso
from ..sources.base import BaseSource


#: 按直链后缀判断落盘扩展名；图片源（Pixiv）不该存成 .mp4
KNOWN_EXTS = (".mp4", ".m4v", ".webm", ".mkv", ".jpg", ".jpeg", ".png", ".webp", ".gif")


def media_ext(url: str | None, audio_url: str | None) -> str:
    """双轨合并产物固定 mp4；单文件按直链后缀，认不出就当视频。"""
    if audio_url:
        return ".mp4"
    tail = (url or "").split("?")[0].lower()
    for ext in KNOWN_EXTS:
        if tail.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".mp4"


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
        # 本地源的内容已经在盘上，不存在“下载”
        if source == "local":
            return {
                "key": vid,
                "source": source,
                "id": vid,
                "title": "",
                "status": "done",
                "quality": quality or "local",
                "progress": 100,
                "receivedBytes": 0,
                "fromLibrary": True,
            }
        key = f"{source}:{vid}"
        existing = self._jobs.get(key)
        if existing and existing["status"] in {"queued", "downloading"}:
            return existing

        # 已在库：只记录，不重复下载（同一内容被多条线索发现时尤其重要）
        row = self.db.query_one(
            "SELECT meta_json, quality FROM library WHERE key = ?", (key,)
        )
        if row is not None:
            job = existing or {
                "key": key,
                "source": source,
                "id": vid,
                "title": "",
                "quality": quality or "auto",
                "progress": 100,
                "receivedBytes": 0,
            }
            try:
                title = json.loads(row["meta_json"]).get("title") or ""
            except (TypeError, ValueError):
                title = ""
            job.update(
                status="done",
                progress=100,
                fromLibrary=True,
                quality=row["quality"] or job["quality"],
                title=title or job.get("title") or "",
            )
            self._jobs[key] = job
            return job

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
        parts: list[Path] = []
        try:
            job["status"] = "downloading"
            job["startedAt"] = now_iso()

            source = self.sources.get(job["source"])
            if source is None:
                raise RuntimeError(f"未注册的源：{job['source']}")

            # 1) 全量元数据快照
            detail = await source.detail(job["id"])
            job["title"] = detail.get("title") or job["id"]
            qualities = detail.get("qualities") or []
            quality = job["quality"]
            if quality == "auto":
                quality = qualities[0]["label"] if qualities else "auto"
                job["quality"] = quality

            candidate = next((q for q in qualities if q.get("label") == quality), None)
            if candidate is None and qualities:
                # 请求的清晰度源站没有给（未登录/大会员/片源限制）：以真实下发值为准，
                # 不能继续打着请求值入库，否则 8K 的标签会盖在 480P 文件上。
                candidate = qualities[0]
                quality = candidate.get("label") or quality
                job["quality"] = quality
                job["qualityFallback"] = True
                self._log(
                    f"[download] {job['key']} 请求清晰度不可用，降级为 {quality}"
                )

            # 2) 下载：dash 音视频分离 → 双轨 + ffmpeg 合并；durl / 直链 → 单文件落盘
            audio_url = (candidate or {}).get("audioUrl")
            video_url = (candidate or {}).get("url") or await source.stream_url(job["id"], quality)

            # 3) 落盘文件名：图片源要保留 .png/.jpg，不能一律 .mp4
            stem = _file_stem(f"{job['source']}-{job['id']}-{quality}")
            file_name = f"{stem}{media_ext(video_url, audio_url)}"
            target = self.db.media_path(file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            part_path = target.with_name(target.name + ".part")
            parts.append(part_path)

            job["totalBytes"] = 0
            job["receivedBytes"] = 0

            if audio_url:
                video_part = part_path.with_name(target.name + ".video.part")
                audio_part = part_path.with_name(target.name + ".audio.part")
                parts.extend([video_part, audio_part])
                await self._download_to(source, video_url, video_part, job, 0, 85)
                await self._download_to(source, audio_url, audio_part, job, 85, 98)
                job["progress"] = 98
                await self._mux(video_part, audio_part, part_path)
                for extra in (video_part, audio_part):
                    with contextlib.suppress(FileNotFoundError):
                        extra.unlink()
                    parts.remove(extra)
            else:
                await self._download_to(source, video_url, part_path, job, 0, 99)

            os.replace(part_path, target)
            parts.remove(part_path)

            # 3) 封面（失败不阻断）
            cover_file = await self._save_cover(source, detail.get("coverUrl"), stem)

            # 4) 入库（元数据全量快照）
            item_size = target.stat().st_size
            category = detail.get("category")
            if not category:
                tags = detail.get("tags") or []
                category = tags[0] if tags else None
            self.db.execute(
                "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
                "cover_file, size_bytes, downloaded_at, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET meta_json = excluded.meta_json, "
                "quality = excluded.quality, file_name = excluded.file_name, "
                "cover_file = excluded.cover_file, size_bytes = excluded.size_bytes, "
                "downloaded_at = excluded.downloaded_at, category = excluded.category",
                (
                    job["key"], job["source"], job["id"],
                    json.dumps(detail, ensure_ascii=False),
                    quality, file_name, cover_file, item_size, now_iso(), category,
                ),
            )

            job["progress"] = 100
            job["status"] = "done"
            job["finishedAt"] = now_iso()
            self._log(f"[download] {job['key']} 完成 {item_size / 1048576:.1f}MB")

        except asyncio.CancelledError:
            self._cleanup(parts)
            job["status"] = "error"
            job["error"] = "已取消"
            raise
        except Exception as exc:  # noqa: BLE001 —— 任何失败都要落成任务状态
            self._cleanup(parts)
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

    async def _download_to(self, source, url: str, path, job: dict[str, Any], lo: int, hi: int) -> None:
        """把一条轨下到 path；进度映射到 [lo, hi] 区间。"""
        handle = await source.open_stream_url(url)
        try:
            if handle.status >= 400:
                raise RuntimeError(f"上游返回 HTTP {handle.status}")
            total = handle.content_length
            if total:
                job["totalBytes"] = (job.get("totalBytes") or 0) + total
            received = 0
            with open(path, "wb") as out:
                async for chunk in handle.iter_chunks():
                    out.write(chunk)
                    received += len(chunk)
                    job["receivedBytes"] = (job.get("receivedBytes") or 0) + len(chunk)
                    if total:
                        job["progress"] = lo + (hi - lo) * received // total
        finally:
            await handle.aclose()

        if total and received < total:
            raise RuntimeError(f"下载不完整：{received}/{total} 字节")

    async def _mux(self, video, audio, target) -> None:
        """dash 音视频轨合并：只 remux 不重编码，NAS 的 CPU 不该浪费在转码上。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(video), "-i", str(audio),
                "-c", "copy", "-movflags", "+faststart",
                "-f", "mp4", str(target),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 ffmpeg：无法合并 dash 音视频轨（镜像需预装 ffmpeg）") from exc
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 合并失败：{err.decode('utf-8', 'replace')[:200]}")

    @staticmethod
    def _cleanup(parts: list[Path]) -> None:
        for path in parts:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
