"""首页 4K 风景背景：首次运行时从 Unsplash 拉到本地库，仓库里不存原图。

- 清单（slug + Unsplash 图片 ID）是代码常量，体积小、可审阅；
  真正的 JPEG 落在 `<data>/media/wallpapers/`，来源：Unsplash License（可免费使用）。
- 启动后在后台按序补拉，不阻塞服务；缺图时前端只显示深色底，主体内容照常渲染。
- 拉取走 forager 的统一出口（FORAGER_PROXY → mihomo），与其它出网请求同一路径。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .transport.curl import CurlError, curl_request

WALLPAPER_DIR = "wallpapers"
#: 除首图外的热度（前端只预加载下一张）
DEFAULT_WEIGHT = 10
MAX_BYTES = 8 * 1024 * 1024
#: Unsplash 统一请求参数：与前端原图尺寸一致（2400×1350）
UNSPLASH_PARAMS = "auto=format&fit=crop&w=2400&h=1350&q=70"
STARTUP_TIMEOUT = 90


@dataclass(frozen=True)
class Wallpaper:
    slug: str
    photo_id: str
    #: 是否已落盘（每次取索引时现查，不缓存）；不参与比较
    present: bool = field(default=False, compare=False)
    present: bool = False

    @property
    def url(self) -> str:
        return f"https://images.unsplash.com/{self.photo_id}?{UNSPLASH_PARAMS}"

    @property
    def path(self) -> Path:
        return Path(WALLPAPER_DIR) / f"{self.slug}.jpg"

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "url": f"/api/wallpapers/{self.slug}.jpg",
            # 0 = 还没落盘；前端据此把这张排到最后、不预加载
            "weight": DEFAULT_WEIGHT if self.present else 0,
        }


#: 顺序即播放顺序：首张是登录页/首页的底图，必须最先可用
CATALOG: tuple[Wallpaper, ...] = (
    Wallpaper("01-earth", "photo-1451187580459-43490279c0fa"),
    Wallpaper("02-nightlake", "photo-1519681393784-d120267933ba"),
    Wallpaper("03-sea-horizon", "photo-1534447677768-be436bb09401"),
    Wallpaper("04-forest-mist", "photo-1483347756197-71ef80e95f73"),
    Wallpaper("05-lake-dawn", "photo-1439066615861-d1af74d74000"),
    Wallpaper("06-valley", "photo-1464822759023-fed622ff2c3b"),
)


class WallpaperStore:
    """本地库里的背景图：`<media>/wallpapers/<slug>.jpg`。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.dir = db.media_dir / WALLPAPER_DIR

    def path(self, slug: str) -> Path | None:
        """已下载的图片路径；未知 slug 或文件缺失返回 None。"""
        item = next((w for w in CATALOG if w.slug == slug), None)
        if item is None:
            return None
        path = self.db.media_path(item.path.as_posix())
        return path if path.is_file() and path.stat().st_size > 0 else None

    def index(self) -> list[dict]:
        return [
            Wallpaper(item.slug, item.photo_id, present=self.path(item.slug) is not None).to_dict()
            for item in CATALOG
        ]

    def missing(self) -> list[Wallpaper]:
        return [item for item in CATALOG if self.path(item.slug) is None]

    async def download(self, item: Wallpaper, proxy: str | None) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.db.media_path(item.path.as_posix())
        resp = await curl_request(item.url, proxy=proxy, retries=1)
        if (
            resp.status != 200
            or len(resp.body) > MAX_BYTES
            or not resp.body.startswith(b"\xff\xd8")
        ):
            raise CurlError(f"背景图下载失败：{item.slug}（HTTP {resp.status}）", 502)
        # 先写临时文件再改名：中途被杀不会留下半张图
        tmp = target.with_suffix(".jpg.part")
        tmp.write_bytes(resp.body)
        tmp.replace(target)
        return target

    async def ensure_all(self, proxy: str | None, log: Callable[[str], None]) -> None:
        """补齐所有缺失的图片；单张失败只记日志，下次启动再试。"""
        for item in self.missing():
            try:
                await self.download(item, proxy)
                log(f"背景图已下载：{item.slug}")
            except (CurlError, OSError) as exc:
                log(f"背景图下载失败（不影响主体内容）：{item.slug}：{exc}")


async def warmup(
    store: WallpaperStore, proxy: str | None, log: Callable[[str], None]
) -> None:
    """补齐缺失的背景图；超时只放弃剩余部分，下次启动再补。"""
    try:
        await asyncio.wait_for(store.ensure_all(proxy, log), timeout=STARTUP_TIMEOUT)
    except (TimeoutError, asyncio.CancelledError):
        pass


__all__ = ["CATALOG", "WALLPAPER_DIR", "Wallpaper", "WallpaperStore", "warmup"]