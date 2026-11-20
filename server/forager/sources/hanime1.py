"""hanime1.me 源适配器（移植自 libre-tv 的 sources/hanime1/index.ts）。

站点为 Laravel 服务端渲染（Blade），没有可抓的列表/详情 JSON API：
- 浏览/搜索：/search?genre=<分区>&sort=最新上傳&page=N、/search?query=<关键词>&page=N
- 详情/播放：/watch?v=<id>，播放地址直接以 <video id="player"> 下多个 <source size> 给出

结构变更时只需改本文件的选择器表（SELECTORS）。
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from selectolax.parser import HTMLParser

from .base import BaseSource, SourceError

SELECTORS: dict[str, str] = {
    "card": "div.video-item-container",
    "cardLink": 'a.video-link[href*="watch?v="]',
    "cardThumb": "img.main-thumb",
    "cardDuration": ".thumb-container .duration",
    "cardTitle": ".title",
    "cardUploader": ".subtitle a",
    "cardStats": ".stat-item",
    "watchTitle": "h3.video-details-wrapper",
    "playerSources": "#player-div-wrapper video#player source",
    "poster": "#player-div-wrapper video#player",
    "uploader": "#video-artist-name",
    "related": "#related-tabcontent",
    "genreLink": ".meta-stats a[href*='search?genre='], .video-details-wrapper a[href*='search?genre=']",
}


class Hanime1Source(BaseSource):
    id: ClassVar[str] = "hanime1"
    name: ClassVar[str] = "Hanime1"
    image_hosts: ClassVar[tuple[str, ...]] = ("hembed.com", "hanime1.me", "hanimeone.me")

    def __init__(self, client) -> None:
        super().__init__(client)
        self.base_url = client.base_url

    # ---- 三个契约方法 ----

    async def list(self, page: int = 1, category: str | None = None) -> dict[str, Any]:
        page_no = clamp_page(page)
        params = {"sort": "最新上傳", "page": str(page_no)}
        if category:
            params["genre"] = category
        query = "&".join(f"{k}={_quote(v)}" for k, v in params.items())
        html = await self.client.fetch_html(f"/search?{query}")
        items = self.parse_cards(html)
        return {"items": items, "page": page_no, "hasMore": len(items) > 0}

    async def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        page_no = clamp_page(page)
        query = f"query={_quote(keyword)}&page={page_no}"
        html = await self.client.fetch_html(f"/search?{query}")
        items = self.parse_cards(html)
        return {"items": items, "page": page_no, "hasMore": len(items) > 0}

    async def detail(self, vid: str) -> dict[str, Any]:
        clean = vid if vid.isdigit() else extract_vid(vid)
        if not clean:
            raise SourceError(f"无效的视频 ID：{vid}", 400)
        html = await self.client.fetch_html(f"/watch?v={clean}", ttl_ms=300_000)
        return self.parse_watch(clean, html)

    # ---- 纯解析（便于离线验证）----

    def parse_watch(self, vid: str, html: str) -> dict[str, Any]:
        tree = HTMLParser(html)

        qualities = []
        for node in tree.css(SELECTORS["playerSources"]):
            url = node.attrs.get("src") or ""
            if url:
                qualities.append({"label": node.attrs.get("size") or "auto", "url": url})
        qualities.sort(key=lambda q: _num(q["label"]), reverse=True)
        if not qualities:
            raise SourceError("播放地址解析失败（页面结构可能已变更）", 502)

        best = qualities[0]
        title = _clean_text(tree.css_first(SELECTORS["watchTitle"]))
        if not title:
            meta = tree.css_first('meta[property="og:title"]')
            title = (meta.attrs.get("content") or "").strip() if meta else ""
        title = title or vid

        poster_node = tree.css_first(SELECTORS["poster"])
        poster = poster_node.attrs.get("poster") if poster_node else None
        if not poster:
            og = tree.css_first('meta[property="og:image"]')
            poster = og.attrs.get("content") if og else None

        related_node = tree.css_first(SELECTORS["related"])
        related = (
            [v for v in dedupe(self.parse_cards(related_node.html or "")) if v["id"] != vid]
            if related_node
            else []
        )

        uploader = _clean_text(tree.css_first(SELECTORS["uploader"])) or None

        result: dict[str, Any] = {
            "id": vid,
            "source": self.id,
            "title": title,
            "pageUrl": f"{self.base_url}/watch?v={vid}",
            "streamUrl": best["url"],
            "streamProxyUrl": (
                f"/api/sources/{self.id}/stream?v={vid}&quality={_quote(best['label'])}"
            ),
            "qualities": qualities,
            "related": related,
            "tags": self._parse_tags(tree),
        }
        if poster:
            result["coverUrl"] = poster
        if uploader:
            result["uploader"] = uploader
        return result

    def parse_cards(self, html: str) -> list[dict[str, Any]]:
        if not html:
            return []
        tree = HTMLParser(html)
        items: list[dict[str, Any]] = []
        for node in tree.css(SELECTORS["card"]):
            link = node.css_first(SELECTORS["cardLink"])
            if link is None:
                continue  # 广告卡无 watch 链接
            vid = extract_vid(link.attrs.get("href") or "")
            if not vid:
                continue
            thumb = node.css_first(SELECTORS["cardThumb"])
            cover = None
            if thumb is not None:
                cover = thumb.attrs.get("src") or thumb.attrs.get("data-src")
            stats = node.css(SELECTORS["cardStats"])
            item: dict[str, Any] = {
                "id": vid,
                "source": self.id,
                "title": _clean_text(node.css_first(SELECTORS["cardTitle"])) or vid,
            }
            if cover:
                item["coverUrl"] = cover
            duration = parse_duration(_clean_text(node.css_first(SELECTORS["cardDuration"])))
            if duration is not None:
                item["durationSec"] = duration
            views = parse_views(_clean_text(stats[-1])) if stats else None
            if views is not None:
                item["views"] = views
            uploader = _clean_text(node.css_first(SELECTORS["cardUploader"]))
            if uploader:
                item["uploader"] = uploader
            items.append(item)
        return dedupe(items)

    def _parse_tags(self, tree: HTMLParser) -> list[str]:
        tags: list[str] = []
        for node in tree.css(SELECTORS["genreLink"]):
            href = node.attrs.get("href") or ""
            if "genre=" not in href:
                continue
            raw = href.split("genre=")[1].split("&")[0]
            genre = _unquote(raw.replace("+", " "))
            if genre and genre not in tags:
                tags.append(genre)
        return tags


# ---- 工具函数（与原 TS 实现逐条对齐，含测试用例）----

def clamp_page(page: int) -> int:
    try:
        n = int(page)
    except (TypeError, ValueError):
        return 1
    return min(max(n, 1), 500)


def extract_vid(href: str) -> str | None:
    match = re.search(r"[?&]v=(\d+)", href)
    return match.group(1) if match else None


def parse_duration(text: str) -> int | None:
    """'09:34' / '1:03:06' → 秒"""
    match = re.match(r"^(\d+:)?(\d{1,2}):(\d{2})$", (text or "").strip())
    if not match:
        return None
    hours = int(match.group(1)[:-1]) if match.group(1) else 0
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))


def parse_views(text: str) -> int | None:
    """'8.5萬次' / '1.2萬' / '3,412次' → 数字"""
    if not text:
        return None
    compact = re.sub(r"[次\s]", "", text)
    match = re.search(r"([\d.,]+)\s*(萬|万|亿|億|K|M)?", compact)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = match.group(2)
    scale = {"萬": 10_000, "万": 10_000, "億": 100_000_000, "亿": 100_000_000, "K": 1_000, "M": 1_000_000}
    return round(value * scale.get(unit, 1))


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
    return out


def _clean_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(strip=True) or "").strip()


def _num(label: str) -> int:
    try:
        return int(label)
    except (TypeError, ValueError):
        return 0


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


def _unquote(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)
