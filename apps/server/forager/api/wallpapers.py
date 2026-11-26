"""背景图路由：/api/wallpapers/*

图片本身也要登录：背景只是页面的装饰，未登录不该能缓存走全部素材。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..auth import require_auth

router = APIRouter(prefix="/api/wallpapers", tags=["wallpapers"], dependencies=[Depends(require_auth)])


@router.get("/index")
async def wallpaper_index(request: Request) -> list[dict]:
    """播放清单：按固定顺序；weight = 0 表示还没下载好。"""
    return request.app.state.wallpapers.index()


@router.get("/{slug}.jpg")
async def wallpaper_file(request: Request, slug: str) -> FileResponse:
    path = request.app.state.wallpapers.path(slug)
    if path is None:
        # 还没下载完（启动时的后台补拉还没轮到 / 还没成功）
        raise HTTPException(status_code=404, detail="背景图尚未就绪")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"cache-control": "public, max-age=31536000, immutable"},
    )