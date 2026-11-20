"""源适配器契约（移植自 libre-tv 的 SourceAdapter）。

每个源实现 list / search / detail 三个方法，归一化为下列结构后经 /api 暴露；
前端只消费归一化结果，不感知源站差异。
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlparse

from ..transport.client import UpstreamClient
from ..transport.curl import StreamResponse


class SourceError(Exception):
    """源处理失败：带 HTTP 状态码，供路由统一映射。"""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class BaseSource:
    """所有源的基类。子类只关心「怎么解析」，传输与频控由 client 统一负责。"""

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    #: 允许经图片代理回源的主机后缀（防盗链白名单）
    image_hosts: ClassVar[tuple[str, ...]] = ()

    def __init__(self, client: UpstreamClient) -> None:
        self.client = client

    # ---- 三个必须实现的方法 ----

    async def list(self, page: int = 1, category: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    async def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        raise NotImplementedError

    async def detail(self, vid: str) -> dict[str, Any]:
        raise NotImplementedError

    # ---- 播放 / 图片 ----

    async def stream_url(self, vid: str, quality: str | None = None) -> str:
        detail = await self.detail(vid)
        qualities = detail.get("qualities") or []
        if quality:
            for item in qualities:
                if item.get("label") == quality:
                    return item["url"]
        if detail.get("streamUrl"):
            return detail["streamUrl"]
        raise SourceError("该视频没有可用的播放地址", 404)

    async def open_stream(
        self, vid: str, quality: str | None = None, range_: str | None = None
    ) -> StreamResponse:
        return await self.client.stream(await self.stream_url(vid, quality), range_=range_)

    async def fetch_image(self, url: str) -> bytes:
        return (await self.client.fetch_bytes(url)).body

    def allows_image_host(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(
            host == allowed or host.endswith("." + allowed) for allowed in self.image_hosts
        )
