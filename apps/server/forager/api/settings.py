"""设置路由：/api/settings/*

安全约定：
- 敏感字段只回显掩码（****abcd），接口永不返回明文
- 只接受源自己声明的 config_fields，其余键一律忽略
- 保存后立即热应用，不重启容器
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/settings", tags=["settings"])

TEST_TIMEOUT_SEC = 25


@router.get("/sources")
async def list_sources_config(request: Request) -> list[dict[str, Any]]:
    app = request.app
    return app.state.source_config.describe(app.state.sources)


@router.put("/sources/{source_id}")
async def save_source_config(request: Request, source_id: str) -> dict[str, Any]:
    app = request.app
    source = app.state.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source_id}")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    app.state.source_config.save(source_id, source.config_fields, payload)
    app.state.source_config.apply_all(app.state.sources)

    updated = next(
        (item for item in app.state.source_config.describe(app.state.sources) if item["source"] == source_id),
        None,
    )
    return {"ok": True, **({"config": updated} if updated else {})}


@router.post("/sources/{source_id}/test")
async def test_source(request: Request, source_id: str) -> dict[str, Any]:
    """用当前配置打一次源站的列表接口，验证凭据 / 代理是否可用。"""
    source = request.app.state.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source_id}")

    started = time.perf_counter()
    try:
        page = await asyncio.wait_for(source.list(1), timeout=TEST_TIMEOUT_SEC)
        elapsed = int((time.perf_counter() - started) * 1000)
        count = len(page.get("items") or [])
        return {"ok": True, "latencyMs": elapsed, "message": f"连接正常，返回 {count} 条"}
    except TimeoutError:
        return {"ok": False, "latencyMs": TEST_TIMEOUT_SEC * 1000, "message": "请求超时"}
    except Exception as exc:  # noqa: BLE001 —— 测试接口把任何失败都变成可读信息
        elapsed = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "latencyMs": elapsed, "message": str(exc) or exc.__class__.__name__}
