"""扫码登录：/api/auth/<source>/qrcode*

流程：前端拿到 url 渲染二维码 → 轮询 poll → 成功后服务端直接保存 Cookie（不经过前端）。
"""

from __future__ import annotations

import base64
import io
from typing import Any

import segno

from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/{source_id}/qrcode")
async def qrcode(request: Request, source_id: str) -> dict[str, Any]:
    source = request.app.state.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source_id}")
    data = await source.qrcode_generate()
    if not data.get("key"):
        raise HTTPException(status_code=502, detail="获取二维码失败")
    data["image"] = _qr_data_uri(data.get("url") or "")
    return data


@router.get("/{source_id}/qrcode/poll")
async def poll(request: Request, source_id: str, key: str, response: Response) -> dict[str, Any]:
    source = request.app.state.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source_id}")

    result = await source.qrcode_poll(key)
    # 轮询结果绝不能进任何缓存，否则会一直看到第一次的 86101
    response.headers["cache-control"] = "no-store, max-age=0"
    if result.get("cookie"):
        # 凭据只落库，绝不回传前端
        request.app.state.source_config.save(
            source_id, source.config_fields, {"cookie": result.pop("cookie")}
        )
        request.app.state.source_config.apply_all(request.app.state.sources)
        result["saved"] = True
    return result


def _qr_data_uri(text: str) -> str:
    """服务端生成 SVG 二维码，前端直接当图片用，省掉一个前端依赖。"""
    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="svg", scale=6, border=2)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()
