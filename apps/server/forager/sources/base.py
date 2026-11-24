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
    #: 可在 Web 设置页调整的字段（见 source_config.FIELD_META）
    config_fields: ClassVar[tuple[str, ...]] = ()
    #: 线索支持的发现类型
    kinds: ClassVar[tuple[str, ...]] = ("query",)
    #: 可选清晰度（空表示不限制）
    quality_options: ClassVar[tuple[str, ...]] = ()
    #: 是否支持扫码登录（设置页据此显示按钮）
    supports_qrcode: ClassVar[bool] = False
    #: 网页登录入口；None 表示该源不提供网页登录（设置页只显示状态）
    login_url: ClassVar[str | None] = None

    def __init__(self, client: UpstreamClient) -> None:
        self.client = client

    def apply_config(self, config: dict[str, Any]) -> None:
        """把设置页保存的配置热应用到 client，无需重启容器。"""
        if "cookie" in config:
            self.client.cookie = config["cookie"] or None
        if "proxy" in config:
            self.client.proxy = config["proxy"] or None
        if "dns_proxy" in config:
            self.client.dns_proxy = config["dns_proxy"] or None
        if "pinned_ip" in config:
            self.client.pinned_ip = config["pinned_ip"] or None
        if "min_interval_ms" in config:
            try:
                ms = int(config["min_interval_ms"])
            except (TypeError, ValueError):
                ms = 0
            self.client.min_interval = max(0, ms) / 1000

    # ---- 三个必须实现的方法 ----

    async def list(self, page: int = 1, category: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    async def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        raise NotImplementedError

    async def detail(self, vid: str) -> dict[str, Any]:
        raise NotImplementedError

    # ---- 线索用的发现能力（按源可选实现）----

    async def browse(self, kind: str, value: str, page: int = 1) -> dict[str, Any]:
        """按线索类型浏览在线内容。

        与定时发现分开：browse 面向用户浏览（如番剧按季度展示），
        partition_items 面向持续发现（直接产出可下载的最小单元）。
        """
        if kind == "query":
            return await self.search(value, page)
        if kind == "partition":
            return await self.partition_items(value, page)
        if kind == "uploader":
            return await self.uploader_items(value, page)
        raise SourceError("该线索类型不支持浏览", 400)

    def categories(self) -> list[dict[str, str]]:
        """源站分区列表：id 用于订阅，name 用于展示。"""
        return []

    async def partition_items(self, value: str, page: int = 1) -> dict[str, Any]:
        raise SourceError("该源不支持按分区发现", 400)

    async def uploader_items(self, value: str, page: int = 1) -> dict[str, Any]:
        raise SourceError("该源不支持按发布者发现", 400)

    async def favorite_uploaders(self) -> list[dict[str, str]]:
        raise SourceError("该源不支持收藏夹展开", 400)

    async def following_uploaders(self) -> list[dict[str, str]]:
        raise SourceError("该源不支持关注列表展开", 400)

    async def qrcode_generate(self) -> dict[str, str]:
        raise SourceError("该源不支持扫码登录", 400)

    async def qrcode_poll(self, key: str) -> dict[str, Any]:
        raise SourceError("该源不支持扫码登录", 400)

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
