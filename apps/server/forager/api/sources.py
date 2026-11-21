"""源相关路由：/api/sources/*"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from ..sources.base import BaseSource, SourceError
from ..transport.curl import CurlError

router = APIRouter(prefix="/api/sources", tags=["sources"])

#: 透传给下游的响应头
PROXY_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "etag",
    "last-modified",
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def get_source(request: Request, source_id: str) -> BaseSource:
    source = request.app.state.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"未注册的源：{source_id}")
    return source


@router.get("")
async def list_sources(request: Request) -> list[dict[str, str]]:
    return [{"id": s.id, "name": s.name} for s in request.app.state.sources.values()]


@router.get("/{source_id}/list")
async def source_list(
    request: Request, source_id: str, page: int = 1, category: str | None = None
) -> dict:
    source = get_source(request, source_id)
    return await source.list(page, category or None)


@router.get("/{source_id}/search")
async def source_search(
    request: Request, source_id: str, kw: str | None = None, page: int = 1
) -> dict:
    source = get_source(request, source_id)
    keyword = (kw or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="缺少关键词参数 kw")
    return await source.search(keyword, page)


@router.get("/{source_id}/detail/{vid}")
async def source_detail(request: Request, source_id: str, vid: str, response: Response) -> dict:
    """详情：元数据缓存命中即零回源；直链过期只重新解析播放地址。"""
    source = get_source(request, source_id)
    key = f"{source_id}:{vid}"
    meta = request.app.state.meta

    cached = meta.get(key)
    if cached is not None and cached[0].get("series"):
        cached = None  # 分集列表需要实时性，旧缓存直接绕过
    if cached is not None:
        detail, stream_valid = cached
        if stream_valid:
            response.headers["x-cache"] = "HIT"
            return detail
        try:
            fresh = await source.detail(vid)
            merged = meta.patch_stream(key, fresh)
            response.headers["x-cache"] = "PARTIAL"
            return merged or fresh
        except (SourceError, CurlError):
            # 刷新失败仍返回缓存：元数据可用，只是暂时不能播放
            response.headers["x-cache"] = "STALE"
            return detail

    detail = await source.detail(vid)
    # 季度/合集的分集列表会随更新变化，不能按元数据缓存 7 天
    if not detail.get("series"):
        meta.put(key, detail)
    response.headers["x-cache"] = "MISS"
    return detail


@router.get("/{source_id}/stream")
async def source_stream(
    request: Request, source_id: str, v: str | None = None, quality: str | None = None
) -> StreamingResponse:
    """流代理：Range 透传，规避 CORS / 防盗链，不落盘。"""
    source = get_source(request, source_id)
    if not v:
        raise HTTPException(status_code=400, detail="缺少参数 v")

    handle = await source.open_stream(v, quality, range_=request.headers.get("range"))
    if handle.status == 0 or handle.status >= 400:
        status = handle.status
        await handle.aclose()
        raise HTTPException(
            status_code=404 if status == 404 else 502, detail=f"上游返回 {status or '无响应'}"
        )

    headers = {k: val for k, val in handle.headers.items() if k in PROXY_HEADERS}
    headers["cache-control"] = "public, max-age=3600"
    # 体为流式时下游按 keep-alive 复用连接可能读到残余字节，明确关闭
    headers["connection"] = "close"
    return StreamingResponse(
        handle.iter_chunks(),
        status_code=handle.status,
        headers=headers,
        background=BackgroundTask(handle.aclose),
    )


@router.get("/{source_id}/image")
async def source_image(request: Request, source_id: str, url: str | None = None) -> Response:
    """图片代理：仅白名单主机，顺带做磁盘缓存（封面基本不变）。"""
    source = get_source(request, source_id)
    if not url:
        raise HTTPException(status_code=400, detail="缺少参数 url")

    parsed = urlparse(url)
    if parsed.scheme != "https" or not source.allows_image_host(url):
        raise HTTPException(status_code=403, detail="不允许代理该主机")

    cache = request.app.state.images
    hit = cache.find(url)
    if hit:
        data = cache.path(hit).read_bytes()
        return _image_response(data, hit, hit_miss="HIT", immutable=True)

    data = await source.fetch_image(url)
    ext = "png" if data[:8] == PNG_SIGNATURE else "jpg"
    cache.save(url, data, ext)
    return _image_response(data, ext, hit_miss="MISS", immutable=True)


def _image_response(data: bytes, name: str, *, hit_miss: str, immutable: bool) -> Response:
    ext = name.rsplit(".", 1)[-1].lower()
    media_type = "image/png" if ext == "png" else "image/jpeg"
    headers = {
        "content-length": str(len(data)),
        "x-cache": hit_miss,
        "cache-control": "public, max-age=31536000, immutable" if immutable else "no-cache",
    }
    return Response(content=data, media_type=media_type, headers=headers)
