"""线索树接口：/api/tree/*"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/tree", tags=["tree"])


@router.get("")
async def get_tree(request: Request) -> dict[str, Any]:
    return request.app.state.scheduler.tree()


@router.post("/nodes", status_code=201)
async def create_node(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    try:
        row = request.app.state.scheduler.create(
            str(body.get("source") or ""),
            str(body.get("kind") or ""),
            str(body.get("value") or ""),
            int(body.get("intervalSec") or 3600),
            body.get("quality") or None,
            body.get("title") or None,
            body.get("parentId") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    node = _find_node(request.app.state.scheduler.tree(), int(row["id"]))
    return node or {"id": row["id"]}


@router.delete("/nodes/{node_id}")
async def delete_node(request: Request, node_id: int) -> dict[str, bool]:
    if request.app.state.scheduler.delete(node_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="线索不存在")


@router.post("/nodes/{node_id}/sync")
async def sync_node(request: Request, node_id: int) -> dict[str, Any]:
    try:
        return await request.app.state.scheduler.sync_node(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="线索不存在") from None


def _find_node(tree: dict[str, Any], node_id: int) -> dict[str, Any] | None:
    def walk(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
        for node in nodes:
            if node["id"] == node_id:
                return node
            found = walk(node.get("children") or [])
            if found:
                return found
        return None

    for group in tree.get("sources") or []:
        found = walk(group.get("nodes") or [])
        if found:
            return found
    return None
