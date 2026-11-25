"""hanime1.me 源适配器（移植自 libre-tv 的 sources/hanime1/index.ts）。

站点为 Laravel 服务端渲染（Blade），没有可抓的列表/详情 JSON API，匿名即可访问：
- 浏览/搜索：/search?genre=<分区>&query=<关键词>&sort=最新上傳&page=N
  （genre 与 query 可同时下发，站内为 AND；列表/搜索/分区是同一个接口）
- 详情/播放：/watch?v=<id>，播放地址直接以 <video id="player"> 下多个 <source size> 给出

结构变更时只需改本文件的选择器表（SELECTORS）。
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any, ClassVar

from selectolax.parser import HTMLParser

from .base import BaseSource, ClueQuery, SourceError

SELECTORS: dict[str, str] = {
    # 站内有两套列表模板，取决于服务端给哪个变体：
    #   标准卡：div.video-item-container > a.video-link（带标题、时长、社团、播放数）
    #   紧凑卡：div.home-rows-videos-wrapper > a[href*=watch?v=] .video-card-inner（只有封面与标题）
    "card": "div.video-item-container",
    "cardLink": 'a.video-link[href*="watch?v="]',
    "cardThumb": "img.main-thumb",
    "cardDuration": ".thumb-container .duration",
    "cardTitle": ".title",
    "cardUploader": ".subtitle a",
    "cardStats": ".stat-item",
    # 紧凑卡：站点在部分分区（如「裏番」）只下发这一套模板
    "compactRoots": "div.home-rows-videos-wrapper, .video-card-inner",
    "compactLink": 'a[href*="watch?v="]',
    "watchTitle": "h3.video-details-wrapper",
    "playerSources": "#player-div-wrapper video#player source",
    "poster": "#player-div-wrapper video#player",
    "uploader": "#video-artist-name",
    "related": "#related-tabcontent",
    "genreLink": ".meta-stats a[href*='search?genre='], .video-details-wrapper a[href*='search?genre=']",
    # 详情页携带的元信息：统计文本形如「觀看次數：5767次  2026-06-01」
    "stats": ".video-description-panel .hidden-xs",
    "statsMobile": "div.video-details-wrapper.hidden-sm.hidden-md.hidden-lg.hidden-xl",
    "description": ".video-description-panel .video-caption-text",
    "tagChip": ".video-tags-wrapper .single-video-tag",
    "duration": 'meta[property="og:video:duration"]',
}

#: 站内 genre（分区）表：id 即 URL 里的 genre 值（繁体原样），2026-11 实测共 10 个
GENRES: tuple[tuple[str, str], ...] = (
    ("裏番", "裏番"),
    ("泡麵番", "泡麵番"),
    ("Motion Anime", "Motion Anime"),
    ("3DCG", "3DCG"),
    ("2D動畫", "2D動畫"),
    ("2.5D", "2.5D"),
    ("MMD", "MMD"),
    ("Cosplay", "Cosplay"),
    ("AI生成", "AI生成"),
    ("新番預告", "新番預告"),
)

#: 「觀看次數：5767次」——数字紧跟标签，后面还跟着日期，不能整串交给 parse_views
_STATS_VIEWS = re.compile(r"觀看次數[：:]\s*([\d.,]+)\s*(萬|万|K|M)?")
_UPLOAD_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Hanime1Source(BaseSource):
    id: ClassVar[str] = "hanime1"
    name: ClassVar[str] = "Hanime1"
    image_hosts: ClassVar[tuple[str, ...]] = ("hembed.com", "hanime1.me", "hanimeone.me")
    config_fields: ClassVar[tuple[str, ...]] = (
        "cookie",
        "proxy",
        "dns_proxy",
        "pinned_ip",
        "quality",
        "min_interval_ms",
    )
    kinds: ClassVar[tuple[str, ...]] = ("search",)
    discover_fields: ClassVar[tuple[str, ...]] = ("category", "keyword")
    quality_options: ClassVar[tuple[str, ...]] = ("1080", "720", "480")

    def __init__(self, client) -> None:
        super().__init__(client)
        self.base_url = client.base_url

    # ---- 三个契约方法 ----

    async def list(self, page: int = 1, category: str | None = None) -> dict[str, Any]:
        return await self._search_page(page=page, category=category)

    async def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        return await self._search_page(page=page, keyword=keyword)

    async def partition_items(self, value: str, page: int = 1) -> dict[str, Any]:
        """hanime1 的分区就是站内 genre，直接复用最新上傳列表。"""
        return await self._search_page(page=page, category=value or None)

    async def discover(self, query: ClueQuery, page: int = 1) -> dict[str, Any]:
        """分区与关键词可以叠加：站内 /search 对 genre + query 取 AND。"""
        return await self._search_page(page=page, category=query.category, keyword=query.keyword)

    def categories(self) -> list[dict[str, str]]:
        return [{"id": genre, "name": label} for genre, label in GENRES]

    async def detail(self, vid: str) -> dict[str, Any]:
        clean = vid if vid.isdigit() else extract_vid(vid)
        if not clean:
            raise SourceError(f"无效的视频 ID：{vid}", 400)
        html = await self.client.fetch_html(f"/watch?v={clean}", ttl_ms=300_000)
        return self.parse_watch(clean, html)

    async def _search_page(
        self, *, page: int = 1, category: str | None = None, keyword: str | None = None
    ) -> dict[str, Any]:
        page_no = clamp_page(page)
        params = {"sort": "最新上傳", "page": str(page_no)}
        if category:
            params["genre"] = category
        word = (keyword or "").strip()
        if word:
            params["query"] = word
        query = "&".join(f"{k}={_quote(v)}" for k, v in params.items())
        html = await self.client.fetch_html(f"/search?{query}")
        items = self.parse_cards(html)
        return {"items": items, "page": page_no, "hasMore": len(items) > 0}

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
        tags = self._parse_tags(tree)

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
            "tags": tags,
        }
        # 分区：hanime1 没有独立分区字段，取首个 genre 作为分区
        if tags:
            result["category"] = tags[0]
        if poster:
            result["coverUrl"] = poster
        if uploader:
            result["uploader"] = uploader

        # 页面携带的元信息：简介 / 上传日期 / 观看数 / 时长 / 自由标签
        description = _block_text(tree.css_first(SELECTORS["description"]))
        if description:
            result["description"] = description

        stats = _clean_text(tree.css_first(SELECTORS["stats"])) or _clean_text(
            tree.css_first(SELECTORS["statsMobile"])
        )
        views = parse_stat_views(stats)
        if views is not None:
            result["views"] = views
        upload_date = parse_upload_date(stats)
        if upload_date:
            result["uploadDate"] = upload_date

        duration = _to_int(_meta_content(tree, "og:video:duration"))
        if duration:
            result["durationSec"] = duration

        hashtags = self._parse_hashtags(tree, exclude=set(tags))
        if hashtags:
            result["hashtags"] = hashtags
        return result

    def parse_cards(self, html: str) -> list[dict[str, Any]]:
        if not html:
            return []
        tree = HTMLParser(html)
        items = self._parse_standard_cards(tree)
        if not items:
            items = self._parse_compact_cards(tree)
        return dedupe(items)

    def _parse_standard_cards(self, tree: HTMLParser) -> list[dict[str, Any]]:
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
        return items

    def _parse_compact_cards(self, tree: HTMLParser) -> list[dict[str, Any]]:
        """紧凑卡模板：结构是 <a href=watch?v=…><img><div class=video-card-inner><div class=….title>…</div></div></a>。"""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in tree.css(SELECTORS["compactLink"]):
            if link.css_first(SELECTORS["card"] + ", .video-card-inner") is None:
                continue  # 与标准卡重叠（标准卡入口本身也带 watch 链接），交给上面那套
            vid = extract_vid(link.attrs.get("href") or "")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            thumb = link.css_first("img")
            cover = None
            if thumb is not None:
                cover = thumb.attrs.get("src") or thumb.attrs.get("data-src")
            title = _compact_title(link)
            item: dict[str, Any] = {"id": vid, "source": self.id, "title": title or vid}
            if cover:
                item["coverUrl"] = cover
            items.append(item)
        return items

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

    def _parse_hashtags(self, tree: HTMLParser, exclude: set[str]) -> list[str]:
        """标签 chips 里 genre 以外的自由标签；「add / remove」是站内的编辑按钮。"""
        out: list[str] = []
        for chip in tree.css(SELECTORS["tagChip"]):
            for icon in chip.css("i"):  # 图标字体（material-icons）的文本是图标名，不是标签
                icon.decompose()
            link = chip.css_first("a")
            label = _clean_text(link if link is not None else chip)
            if not label or label in exclude or label.lower() in {"add", "remove"}:
                continue
            if label not in out:
                out.append(label)
        return out


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


def parse_stat_views(text: str | None) -> int | None:
    """「觀看次數：5767次  2026-06-01」→ 5767。

    先按标签把数字截出来再换算：整串直接交给 parse_views 会把日期粘成 57672026。
    """
    match = _STATS_VIEWS.search(text or "")
    if not match:
        return None
    return parse_views(f"{match.group(1)}{match.group(2) or ''}")


def parse_upload_date(text: str | None) -> str | None:
    """统计文本里的上传日期：'2026-06-01'。"""
    match = _UPLOAD_DATE.search(text or "")
    return match.group(0) if match else None


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


def _block_text(node) -> str:
    """简介这类块级文本：<br> 当作换行、其余标签去掉、实体还原、空白折叠。"""
    if node is None:
        return ""
    html = re.sub(r"<br\s*/?>", " ", node.html or "", flags=re.IGNORECASE)
    text = unescape(re.sub(r"<[^>]+>", "", html))
    return re.sub(r"\s+", " ", text).strip()


def _compact_title(link) -> str:
    """紧凑卡的标题：一般是 .video-card-inner 下的 .xxx.xxx.title；兜底取卡片内最长的一行非空文本。"""
    node = link.css_first(f'{SELECTORS["compactRoots"].split(", ")[1]} [class$="title"], .video-card-inner [class*="title"]')
    title = _clean_text(node)
    if title:
        return title
    candidates = [
        _clean_text(span)
        for span in link.css(".video-card-inner div")
        if 0 < len(_clean_text(span)) <= 200
    ]
    return max(candidates, key=len, default="")


def _num(label: str) -> int:
    try:
        return int(label)
    except (TypeError, ValueError):
        return 0


def _to_int(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


def _meta_content(tree: HTMLParser, name: str) -> str:
    node = tree.css_first(f'meta[property="{name}"], meta[name="{name}"]')
    return (node.attrs.get("content") or "").strip() if node else ""


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


def _unquote(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)
