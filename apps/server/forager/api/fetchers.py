"""线索接口：/api/fetchers/*（采集器）与 /api/clues/*（线索）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..clues.scheduler import EXPAND_KINDS
from ..sources.base import ClueQuery

router = APIRouter(prefix="/api", tags=["clues"])


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 —— 空体或非法 JSON 都按空对象处理
        return {}
    return data if isinstance(data, dict) else {}


# ---- 采集器 ----

@router.get("/fetchers")
async def get_fetchers(request: Request) -> dict[str, Any]:
    """采集器（含线索与进度）、触发/数量选项、各源可选分区与筛选项。"""
    return request.app.state.scheduler.overview()


@router.post("/fetchers", status_code=201)
async def create_fetcher(request: Request) -> dict[str, Any]:
    body = await _json(request)
    try:
        row = request.app.state.scheduler.create_fetcher(
            str(body.get("name") or ""),
            str(body.get("trigger") or "manual"),
            body.get("quality"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    found = _find(request.app.state.scheduler.overview(), int(row["id"]))
    return found or dict(row)


@router.delete("/fetchers/{fetcher_id}")
async def delete_fetcher(request: Request, fetcher_id: int) -> dict[str, bool]:
    if await request.app.state.scheduler.delete_fetcher(fetcher_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="采集器不存在")


@router.post("/fetchers/{fetcher_id}/run")
async def run_fetcher(request: Request, fetcher_id: int) -> dict[str, Any]:
    """立即运行：接口立刻返回，采集器在后台跑，进度看 GET /api/fetchers。"""
    try:
        return request.app.state.scheduler.trigger(fetcher_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="采集器不存在") from None


# ---- 线索 ----

@router.post("/clues", status_code=201)
async def create_clue(request: Request) -> dict[str, Any]:
    body = await _json(request)
    try:
        fetcher_id = int(body.get("fetcherId") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="fetcherId 必须是整数") from None
    try:
        return request.app.state.scheduler.create_clue(
            fetcher_id,
            str(body.get("source") or ""),
            kind=str(body.get("kind") or "search"),
            category=body.get("partition"),
            keyword=body.get("keyword"),
            uploader=body.get("uploader"),
            pages=body.get("pages", 1),  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/clues/{clue_id}")
async def delete_clue(request: Request, clue_id: int) -> dict[str, bool]:
    if await request.app.state.scheduler.delete_clue(clue_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="线索不存在")


@router.get("/clues/{clue_id}/items")
async def clue_items(request: Request, clue_id: int, page: int = 1) -> dict[str, Any]:
    """浏览该线索对应的在线内容（不写入发现记录、不自动下载）。"""
    row = request.app.state.db.query_one("SELECT * FROM clues WHERE id = ?", (clue_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="线索不存在")
    if row["kind"] in EXPAND_KINDS:
        raise HTTPException(status_code=400, detail="展开类线索不直接产出作品")
    source = request.app.state.sources.get(row["source"])
    if source is None:
        raise HTTPException(status_code=404, detail=f"未注册的源：{row['source']}")
    query = ClueQuery(
        kind=row["kind"],
        category=row["category"],
        keyword=row["keyword"],
        uploader=row["uploader"],
    )
    return await source.discover(query, max(1, page))


def _find(overview: dict[str, Any], fetcher_id: int) -> dict[str, Any] | None:
    return next(
        (item for item in overview.get("fetchers") or [] if item["id"] == fetcher_id), None
    )
