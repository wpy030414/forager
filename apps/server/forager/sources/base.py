"""源适配器契约（移植自 libre-tv 的 SourceAdapter）。

每个源实现 list / search / detail 三个方法，归一化为下列结构后经 /api 暴露；
前端只消费归一化结果，不感知源站差异。

线索发现走 discover()：按 ClueQuery（分区 / 关键词 / 发布者）取一页可下载的最小单元，
发现与浏览共用同一个出口，只是发现会记状态、浏览不记。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlparse

from ..transport.client import UpstreamClient
from ..transport.curl import StreamResponse


class SourceError(Exception):
    """源处理失败：带 HTTP 状态码，供路由统一映射。"""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ClueQuery:
    """线索的发现参数。分区与关键词可以叠加（源站支持时为 AND）。"""

    #: search = 按筛选条件列作品；favorites / following = 展开成发布者子线索
    kind: str = "search"
    #: 分区（hanime1 的 genre / Pixiv 的排行榜）
    category: str | None = None
    keyword: str | None = None
    #: 发布者 / 画师，仅支持该能力的源
    uploader: str | None = None


class BaseSource:
    """所有源的基类。子类只关心「怎么解析」，传输与频控由 client 统一负责。"""

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    #: 允许经图片代理回源的主机后缀（防盗链白名单）
    image_hosts: ClassVar[tuple[str, ...]] = ()
    #: 可在 Web 设置页调整的字段（见 source_config.FIELD_META）
    config_fields: ClassVar[tuple[str, ...]] = ()
    #: 线索支持的类型：search | favorites | following
    kinds: ClassVar[tuple[str, ...]] = ("search",)
    #: 新建线索时表单露出的筛选项：category | keyword | uploader
    discover_fields: ClassVar[tuple[str, ...]] = ("category", "keyword")
    #: 可选清晰度（空表示不限制）
    quality_options: ClassVar[tuple[str, ...]] = ()
    #: 是否支持扫码登录（设置页据此显示按钮）
    supports_qrcode: ClassVar[bool] = False

    def __init__(self, client) -> None:
        self.client = client
        #: 来自环境变量的默认配置（设置页未保存时生效），由 create_sources 填入
        self.env_config: dict[str, Any] = {}

    def apply_config(self, config: dict[str, Any]) -> None:
        """把设置页保存的配置热应用到 client，无需重启容器。

        传入的应当是「环境变量默认值 + 设置页保存值」合并后的完整配置：
        某字段被清空时这里会收到它的默认值，而不是保持旧值。
        """
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

    # ---- 线索用的发现能力（按源可选覆写）----

    async def discover(self, query: ClueQuery, page: int = 1) -> dict[str, Any]:
        """按线索参数取一页可下载的最小单元。

        返回 {items, page, hasMore}；items 为空即到列表末尾。
        源站若把页号夹回上限（返回的 page 小于请求值），调用方视为到底。
        """
        if query.uploader:
            return await self.uploader_items(query.uploader, page)
        if query.keyword:
            return await self.search(query.keyword, page)
        if query.category:
            return await self.partition_items(query.category, page)
        return await self.list(page)

    def validate_clue(self, query: ClueQuery, pages: int) -> None:
        """新建线索前的能力校验：不支持就抛 SourceError(…, 400)。"""
        if query.kind not in self.kinds:
            raise SourceError(f"{self.name} 不支持线索类型：{query.kind}", 400)
        if query.kind != "search":
            return
        labels = {"category": "分区", "keyword": "关键词", "uploader": "发布者"}
        unsupported = [
            labels[field]
            for field in ("category", "keyword", "uploader")
            if getattr(query, field) and field not in self.discover_fields
        ]
        if unsupported:
            raise SourceError(f"{self.name} 不支持按{'、'.join(unsupported)}发现", 400)

    def categories(self) -> list[dict[str, str]]:
        """源站分区列表：id 用于线索，name 用于展示。"""
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

    async def open_stream_url(self, url: str) -> StreamResponse:
        """按绝对地址取流。图片/资源站常需源自己的头（如 Referer），故可覆写。"""
        return await self.client.stream(url)

    async def fetch_image(self, url: str) -> bytes:
        return (await self.client.fetch_bytes(url)).body

    def allows_image_host(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(
            host == allowed or host.endswith("." + allowed) for allowed in self.image_hosts
        )
