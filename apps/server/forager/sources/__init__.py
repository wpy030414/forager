"""源注册表：新增源只需在 REGISTRY 里登记，路由与前端无需改动。

本地库也是一个源（local），与在线源同级；界面统一从源取内容。
"""

from __future__ import annotations

from ..config import Settings
from ..db import Database
from ..transport.client import UpstreamClient
from .base import BaseSource, SourceError
from .hanime1 import Hanime1Source
from .local import LocalSource

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def create_sources(settings: Settings, db: Database | None = None) -> dict[str, BaseSource]:
    hanime1 = Hanime1Source(
        UpstreamClient(
            base_url=settings.hanime1_base_url,
            user_agent=DEFAULT_UA,
            proxy=settings.hanime1_proxy,
            dns_proxy=settings.hanime1_dns_proxy,
            pinned_ip=settings.hanime1_pinned_ip,
            cookie=settings.hanime1_cookie,
            min_interval_ms=settings.min_interval_ms,
            cache_ttl_ms=settings.html_cache_ttl_ms,
        )
    )
    sources: list[BaseSource] = [hanime1]
    if db is not None:
        sources.insert(0, LocalSource(db))
    return {source.id: source for source in sources}


__all__ = ["BaseSource", "SourceError", "create_sources"]
