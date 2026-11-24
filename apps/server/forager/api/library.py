"""本地库与播放路由：/api/library/*（契约与 libre-tv 完全一致）"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..db import now_iso

router = APIRouter(prefix="/api/library", tags=["library"])

MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _mime(path: Path) -> str:
    return MIME.get(path.suffix.lower(), "application/octet-stream")


def media_key(source: str, vid: str) -> str:
    """进度/播放的库 key：本地源的条目 id 本身就是库 key，不再二次拼接。"""
    return vid if source == "local" else f"{source}:{vid}"


def _row_to_item(row) -> dict[str, Any]:
    return {
        "key": row["key"],
        "source": row["source"],
        "id": row["video_id"],
        "meta": json.loads(row["meta_json"]),
        "quality": row["quality"],
        "fileName": row["file_name"],
        "coverFile": row["cover_file"] or None,
        "sizeBytes": row["size_bytes"],
        "downloadedAt": row["downloaded_at"],
        "category": row["category"] if "category" in row.keys() else None,
    }


# ---- 离线开关 ----

@router.get("/mode")
async def get_mode(request: Request) -> dict[str, bool]:
    return {"offline": request.app.state.db.get_setting("mode.offline") == "1"}


@router.put("/mode")
async def set_mode(request: Request) -> dict[str, bool]:
    body = await _json(request)
    offline = bool(body.get("offline"))
    request.app.state.db.set_setting("mode.offline", "1" if offline else "0")
    return {"offline": offline}


# ---- 本地库条目 ----

@router.get("/keys")
async def library_keys(request: Request) -> list[str]:
    """轻量键列表：线索页用来判断哪些最小单元已经在库。"""
    return [
        row["key"]
        for row in request.app.state.db.query("SELECT key FROM library ORDER BY downloaded_at DESC")
    ]


@router.get("/items")
async def list_items(
    request: Request,
    source: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if source:
        where.append("source = ?")
        params.append(source)
    if category:
        where.append("category = ?")
        params.append(category)
    if q:
        where.append("meta_json LIKE ?")
        params.append(f"%{q}%")
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    rows = request.app.state.db.query(
        f"SELECT * FROM library{clause} ORDER BY downloaded_at DESC", tuple(params)
    )
    return [_row_to_item(row) for row in rows]


@router.get("/continue")
async def continue_watching(request: Request, limit: int = 12) -> list[dict[str, Any]]:
    """继续播放：有进度且未看完的库条目，按最近播放排序。"""
    size = min(max(int(limit or 12), 1), 50)
    rows = request.app.state.db.query(
        "SELECT l.*, p.position_ms, p.duration_ms, p.updated_at AS progress_at "
        "FROM progress p JOIN library l ON l.key = p.key "
        "WHERE p.position_ms > 0 "
        "  AND (p.duration_ms IS NULL OR p.position_ms < p.duration_ms - 10000) "
        "ORDER BY p.updated_at DESC LIMIT ?",
        (size,),
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_item(row)
        item["progress"] = {
            "positionMs": row["position_ms"],
            "durationMs": row["duration_ms"],
            "updatedAt": row["progress_at"],
        }
        items.append(item)
    return items


@router.get("/facets")
async def facets(request: Request) -> dict[str, Any]:
    """库的分面：来源与分区（分区来自源站，不是 tag）。"""
    db = request.app.state.db
    sources = [
        {"source": r["source"], "count": r["n"]}
        for r in db.query("SELECT source, COUNT(*) AS n FROM library GROUP BY source")
    ]
    categories = [
        {"source": r["source"], "category": r["category"], "count": r["n"]}
        for r in db.query(
            "SELECT source, category, COUNT(*) AS n FROM library "
            "WHERE category IS NOT NULL AND category != '' "
            "GROUP BY source, category ORDER BY n DESC"
        )
    ]
    return {"sources": sources, "categories": categories, "total": sum(s["count"] for s in sources)}


@router.get("/items/{key}")
async def get_item(request: Request, key: str) -> dict[str, Any]:
    row = request.app.state.db.query_one("SELECT * FROM library WHERE key = ?", (key,))
    if row is None:
        raise HTTPException(status_code=404, detail="本地库无此条目")
    return _row_to_item(row)


@router.delete("/items/{key}")
async def delete_item(request: Request, key: str) -> dict[str, bool]:
    if request.app.state.downloader.delete(key):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="本地库无此条目")


# ---- 下载任务 ----

@router.post("/downloads", status_code=202)
async def create_download(request: Request) -> dict[str, Any]:
    body = await _json(request)
    source = body.get("source")
    vid = body.get("id")
    if not source or not vid:
        raise HTTPException(status_code=400, detail="需要 source 与 id")
    if source not in request.app.state.sources:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source}")
    return request.app.state.downloader.enqueue(str(source), str(vid), body.get("quality"))


@router.get("/downloads")
async def list_downloads(request: Request) -> list[dict[str, Any]]:
    return request.app.state.downloader.list()


@router.get("/downloads/{key}")
async def get_download(request: Request, key: str) -> dict[str, Any]:
    job = request.app.state.downloader.get(key)
    if job is None:
        raise HTTPException(status_code=404, detail="无此任务")
    return job


@router.delete("/downloads/{key}")
async def cancel_download(request: Request, key: str) -> dict[str, bool]:
    if request.app.state.downloader.cancel(key):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="任务不存在或已结束")


# ---- 本地媒体文件（本地播放 / 本地封面，支持 Range）----

@router.get("/media/{file:path}")
async def media(request: Request, file: str) -> Response:
    try:
        path = request.app.state.db.media_path(file)
    except ValueError:
        raise HTTPException(status_code=404, detail="非法路径") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return _serve_file(path, request.headers.get("range"))


# ---- 播放来源解析（双播模式的核心出口）----

@router.get("/play/{source}/{vid}")
async def play(request: Request, source: str, vid: str, quality: str | None = None) -> dict[str, Any]:
    app = request.app
    db = app.state.db
    key = media_key(source, vid)
    offline = db.get_setting("mode.offline") == "1"

    row = db.query_one("SELECT * FROM library WHERE key = ?", (key,))
    if row is not None:
        item = _row_to_item(row)
        return {
            "url": f"/api/library/media/{item['fileName']}",
            "source": "local",
            "quality": item["quality"],
            "localQualities": [item["quality"]],
        }
    if offline:
        raise HTTPException(status_code=404, detail="离线模式下该视频未下载")

    detail = await _cached_detail(app, source, vid)
    qualities = detail.get("qualities") or []
    chosen = quality or (qualities[0]["label"] if qualities else "auto")
    return {
        "url": f"/api/sources/{source}/stream?v={vid}&quality={chosen}",
        "source": "cdn",
        "quality": chosen,
        "localQualities": [],
    }


# ---- 播放进度 ----

@router.get("/progress/{source}/{vid}")
async def get_progress(request: Request, source: str, vid: str) -> dict[str, Any] | None:
    row = request.app.state.db.query_one(
        "SELECT * FROM progress WHERE key = ?", (media_key(source, vid),)
    )
    if row is None:
        return None
    return {
        "positionMs": row["position_ms"],
        "durationMs": row["duration_ms"],
        "updatedAt": row["updated_at"],
    }


@router.put("/progress/{source}/{vid}")
async def save_progress(request: Request, source: str, vid: str) -> dict[str, bool]:
    body = await _json(request)
    position = max(0, round(float(body.get("positionMs") or 0)))
    duration = body.get("durationMs")
    key = media_key(source, vid)
    request.app.state.db.execute(
        "INSERT INTO progress (key, position_ms, duration_ms, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET position_ms = excluded.position_ms, "
        "duration_ms = excluded.duration_ms, updated_at = excluded.updated_at",
        (key, position, round(float(duration)) if duration else None, now_iso()),
    )
    return {"ok": True}


# ---- 内部工具 ----

async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 —— 空体或非法 JSON 都按空对象处理
        return {}
    return data if isinstance(data, dict) else {}


async def _cached_detail(app, source_id: str, vid: str) -> dict[str, Any]:
    source = app.state.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source_id}")
    key = f"{source_id}:{vid}"
    cached = app.state.meta.get(key)
    if cached is not None and cached[1]:
        return cached[0]
    detail = await source.detail(vid)
    app.state.meta.put(key, detail)
    return detail


def _serve_file(path: Path, range_header: str | None) -> Response:
    size = path.stat().st_size
    if not range_header:
        return FileResponse(
            path, media_type=_mime(path), headers={"accept-ranges": "bytes"}
        )

    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not match:
        return Response(status_code=416, headers={"content-range": f"bytes */{size}"})
    start = int(match.group(1)) if match.group(1) else 0
    end = int(match.group(2)) if match.group(2) else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"content-range": f"bytes */{size}"})

    return StreamingResponse(
        _iter_slice(path, start, end),
        status_code=206,
        media_type=_mime(path),
        headers={
            "content-length": str(end - start + 1),
            "content-range": f"bytes {start}-{end}/{size}",
            "accept-ranges": "bytes",
        },
    )


async def _iter_slice(path: Path, start: int, end: int, block: int = 262144) -> AsyncIterator[bytes]:
    remaining = end - start + 1
    with open(path, "rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(block, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
