"""订阅路由：/api/subscriptions/*（forager 新增能力）"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


@router.get("")
async def list_subscriptions(request: Request) -> list[dict[str, Any]]:
    return request.app.state.scheduler.list_all()


@router.post("", status_code=201)
async def create_subscription(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    source_id = body.get("source")
    value = (body.get("value") or "").strip()
    kind = body.get("kind") or "query"
    if not source_id or not value:
        raise HTTPException(status_code=400, detail="需要 source 与 value")
    if source_id not in request.app.state.sources:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source_id}")
    try:
        return request.app.state.scheduler.create(
            source_id,
            kind,
            value,
            int(body.get("intervalSec") or 3600),
            body.get("quality"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{sub_id}")
async def delete_subscription(request: Request, sub_id: int) -> dict[str, bool]:
    if request.app.state.scheduler.delete(sub_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="订阅不存在")


@router.post("/run")
async def run_now(request: Request) -> dict[str, Any]:
    """手动跑一轮订阅（不等定时器）。"""
    enqueued = await request.app.state.scheduler.run_once()
    return {"enqueued": enqueued}
