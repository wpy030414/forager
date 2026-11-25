"""Pixiv 源适配器：走网页端自用的 /ajax/* 接口，不依赖 OAuth。

为什么不用 app-api + OAuth：那条路要借官方 App 的 client_id/secret，
且 token 轮换逻辑随时可能被收紧。网页端自己在用的接口匿名即可用，
Cookie 只在需要读关注 / 收藏时才需要——与 hanime1 的凭据模型完全一致。

接口一览（全部 JSON，需 `X-Requested-With: XMLHttpRequest` 与 pixiv 的 Referer）：
- 排行      /ranking.php?mode=daily&format=json&p=N
- 搜索      /ajax/search/artworks/<kw>?word=<kw>&order=date_d&p=N&mode=all
- 详情      /ajax/illust/<id> 与 /ajax/illust/<id>/pages
- 画师作品  /ajax/user/<uid>/profile/all 拿全量 id，再 /ajax/user/<uid>/illusts?ids[]=… 补元数据
- 关注列表  /ajax/user/<uid>/following?offset=0&limit=100&rest=show（需登录）
- 收藏夹    /ajax/user/<uid>/illusts/bookmarks?tag=&offset=0&limit=100&rest=show（需登录）

图片防盗链：一律带 `Referer: https://www.pixiv.net/`。
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, ClassVar
from urllib.parse import quote

from ..transport.curl import StreamResponse
from .base import BaseSource, ClueQuery, SourceError

WEB = "https://www.pixiv.net"
IMG_HEADERS = {"Referer": "https://www.pixiv.net/"}
AJAX_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
    "Referer": "https://www.pixiv.net/",
}

#: 一次批量取作品元数据的上限（网页端自己也是 48）
BATCH = 48
#: 画师作品最多翻多少页
MAX_PAGES = 40

RANKING_MODES = {
    "daily": "日榜",
    "weekly": "周榜",
    "monthly": "月榜",
    "rookie": "新人榜",
    "original": "原创榜",
}


class PixivSource(BaseSource):
    id: ClassVar[str] = "pixiv"
    name: ClassVar[str] = "Pixiv"
    image_hosts: ClassVar[tuple[str, ...]] = ("pximg.net", "pixiv.net")
    config_fields: ClassVar[tuple[str, ...]] = (
        "cookie",
        "proxy",
        "dns_proxy",
        "pinned_ip",
        "min_interval_ms",
    )
    kinds: ClassVar[tuple[str, ...]] = ("search", "favorites", "following")
    discover_fields: ClassVar[tuple[str, ...]] = ("category", "keyword", "uploader")
    quality_options: ClassVar[tuple[str, ...]] = ()

    # ---- 契约：浏览 / 搜索 / 详情 ----

    async def list(self, page: int = 1, category: str | None = None) -> dict[str, Any]:
        return await self.partition_items(category or "daily", page)

    async def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        kw = keyword.strip()
        if not kw:
            raise SourceError("搜索关键词不能为空", 400)
        page_no = clamp_page(page)
        body = await self._json(
            f"/ajax/search/artworks/{quote(kw)}"
            f"?word={quote(kw)}&order=date_d&mode=all&p={page_no}&s_mode=s_tag&type=all&lang=zh"
        )
        section = body.get("illustManga") or {}
        return {
            "items": parse_search_items(section.get("data") or [], self.id),
            "page": page_no,
            "hasMore": page_no < int(section.get("lastPage") or 1),
        }

    async def partition_items(self, value: str, page: int = 1) -> dict[str, Any]:
        """分区即排行榜：daily / weekly / monthly / rookie / original。"""
        mode = value if value in RANKING_MODES else "daily"
        page_no = clamp_page(page)
        body = await self._json(f"/ranking.php?mode={mode}&format=json&p={page_no}")
        contents = body.get("contents") or []
        return {
            "items": parse_ranking_items(contents, self.id),
            "page": page_no,
            "hasMore": bool(body.get("next")),
        }

    async def detail(self, vid: str) -> dict[str, Any]:
        clean = normalize_id(vid)
        if not clean:
            raise SourceError(f"无效的作品 ID：{vid}", 400)
        body = await self._json(f"/ajax/illust/{clean}")
        pages = await self._json(f"/ajax/illust/{clean}/pages")
        return build_detail(self.id, clean, body, pages)

    # ---- 契约：线索发现 ----

    def validate_clue(self, query: ClueQuery, pages: int) -> None:
        """Pixiv 的排行榜、搜索、画师是三个互不相干的接口，只能取其一。"""
        super().validate_clue(query, pages)
        if query.kind != "search":
            return
        filled = [
            label
            for label, value in (
                ("分区", query.category),
                ("关键词", query.keyword),
                ("画师", query.uploader),
            )
            if value
        ]
        if len(filled) > 1:
            raise SourceError(f"Pixiv 的分区、关键词、画师只能填一项（现在填了{'、'.join(filled)}）", 400)
        if query.category and pages == 0:
            raise SourceError("Pixiv 排行榜不支持「全部」", 400)

    async def uploader_items(self, value: str, page: int = 1) -> dict[str, Any]:
        uid = normalize_id(value)
        if not uid:
            raise SourceError(f"无效的画师 ID：{value}", 400)
        page_no = clamp_page(page)
        if page_no > MAX_PAGES:
            return {"items": [], "page": page_no, "hasMore": False}  # 翻到顶：给「全部」一个干净的终点
        ids = await self._user_illust_ids(uid, page_no)
        items = await self._hydrate(uid, ids)
        return {"items": items, "page": page_no, "hasMore": len(ids) >= BATCH}

    async def favorite_uploaders(self) -> list[dict[str, str]]:
        """收藏夹 → 自动展开成画师子线索。"""
        uid = self._self_uid()
        return await self._collect_users(
            f"/ajax/user/{uid}/illusts/bookmarks?tag=&offset={{offset}}&limit=100&rest=show"
        )

    async def following_uploaders(self) -> list[dict[str, str]]:
        uid = self._self_uid()
        return await self._collect_users(
            f"/ajax/user/{uid}/following?offset={{offset}}&limit=100&rest=show"
        )

    def categories(self) -> list[dict[str, str]]:
        return [{"id": key, "name": label} for key, label in RANKING_MODES.items()]

    # ---- 图片 / 流 ----

    async def open_stream_url(self, url: str) -> StreamResponse:
        """i.pximg.net 只认带 Referer 的请求，默认的 client.stream 会被 403。"""
        return await self.client.stream(url, headers=IMG_HEADERS)

    async def fetch_image(self, url: str) -> bytes:
        # i.pximg.net 对无 Referer 的请求直接 403
        resp = await self.client.fetch_bytes(url, headers=IMG_HEADERS)
        if resp.status >= 400:
            raise SourceError(f"封面抓取失败（HTTP {resp.status}）", resp.status)
        return resp.body

    # ---- 内部 ----

    async def _json(self, path: str) -> dict[str, Any]:
        raw = await self.client.request(path, headers=AJAX_HEADERS)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise SourceError("Pixiv 返回的不是合法 JSON（可能被登录墙拦截）", 502) from exc
        if data.get("error"):
            msg = data.get("message") or "Pixiv 返回错误"
            raise SourceError(msg, 401 if "login" in msg.lower() else 502)
        body = data.get("body")
        return body if isinstance(body, dict) else data

    def _self_uid(self) -> str:
        """登录接口都要自己的 uid：直接从 Cookie 里拿，省一次请求。"""
        for key in ("pixiv_uid", "pixiv.user.id"):
            value = read_cookie(self.client.cookie, key)
            if value:
                return value
        raise SourceError("该线索需要 Pixiv 登录：请在设置页填写 Cookie", 401)

    async def _user_illust_ids(self, uid: str, page: int) -> list[str]:
        """profile/all 给的是全量 id 列表（值为 null），按 48 个一页切片。"""
        if page > MAX_PAGES:
            raise SourceError("已超过画师作品翻页上限", 404)
        body = await self._json(f"/ajax/user/{uid}/profile/all")
        ids: list[str] = []
        for key in ("illusts", "manga"):
            for work_id in body.get(key) or {}:
                if work_id not in ids:
                    ids.append(str(work_id))
        if not ids:
            raise SourceError("该画师没有公开作品", 404)
        start = (page - 1) * BATCH
        return ids[start : start + BATCH]

    async def _hydrate(self, uid: str, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        query = "&".join(f"ids%5B%5D={quote(str(i))}" for i in ids)
        body = await self._json(f"/ajax/user/{uid}/illusts?{query}")
        return [summary(self.id, body[i]) for i in ids if isinstance(body.get(i), dict)]

    async def _collect_users(self, template: str) -> list[dict[str, str]]:
        users: list[dict[str, str]] = []
        offset = 0
        for _ in range(10):  # 最多 1000 个，够用了
            body = await self._json(template.format(offset=offset))
            batch = body.get("users") or body.get("works") or []
            if not batch:
                break
            for row in batch:
                uid = str(row.get("userId") or "")
                if not uid:
                    continue
                users.append({"id": uid, "title": row.get("userName") or uid})
            if not body.get("nextUrl"):
                break
            offset += 100
        return dedupe_users(users)


# ---- 纯解析（离线可测）----


def parse_search_items(rows: list[dict[str, Any]], source_id: str) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        work_id = str(row.get("id") or "")
        if not work_id:
            continue
        item: dict[str, Any] = {
            "id": work_id,
            "source": source_id,
            "title": row.get("title") or work_id,
        }
        if row.get("url"):
            item["coverUrl"] = row["url"]
        if row.get("userName"):
            item["uploader"] = row["userName"]
        if row.get("pageCount"):
            item["note"] = f"{row['pageCount']} 页"
        items.append(item)
    return dedupe(items)


def parse_ranking_items(rows: list[dict[str, Any]], source_id: str) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        work_id = str(row.get("illust_id") or row.get("id") or "")
        if not work_id:
            continue
        item: dict[str, Any] = {
            "id": work_id,
            "source": source_id,
            "title": row.get("title") or work_id,
        }
        cover = row.get("url") or ""
        if cover:
            # 排行榜给的是 240x480 缩略图，换成 480x960 更耐看
            item["coverUrl"] = cover.replace("/c/240x480/", "/c/480x960/")
        if row.get("user_name"):
            item["uploader"] = row["user_name"]
        items.append(item)
    return dedupe(items)


def summary(source_id: str, work: dict[str, Any]) -> dict[str, Any]:
    work_id = str(work.get("id") or "")
    item: dict[str, Any] = {
        "id": work_id,
        "source": source_id,
        "title": work.get("title") or work_id,
    }
    if work.get("url"):
        item["coverUrl"] = work["url"]
    if work.get("userName"):
        item["uploader"] = work["userName"]
    if work.get("pageCount"):
        item["note"] = f"{work['pageCount']} 页"
    return item


def build_detail(source_id: str, vid: str, body: dict[str, Any], pages: Any) -> dict[str, Any]:
    title = body.get("illustTitle") or body.get("title") or vid
    urls = body.get("urls") or {}
    page_list = pages if isinstance(pages, list) else []

    qualities: list[dict[str, str]] = []
    for index, page in enumerate(page_list):
        url = (page.get("urls") or {}).get("original") or ""
        if url:
            qualities.append({"label": f"p{index} 原图", "url": url})
    if not qualities and urls.get("original"):
        qualities.append({"label": "原图", "url": urls["original"]})
    if not qualities:
        raise SourceError("该作品没有可下载的图片地址", 404)

    tags = [t.get("tag") for t in (body.get("tags") or {}).get("tags") or [] if t.get("tag")]
    detail: dict[str, Any] = {
        "id": vid,
        "source": source_id,
        "title": title,
        "pageUrl": f"{WEB}/artworks/{vid}",
        "streamUrl": qualities[0]["url"],
        "streamProxyUrl": f"/api/sources/{source_id}/stream?v={vid}",
        "qualities": qualities,
        "tags": tags,
    }
    if urls.get("regular"):
        detail["coverUrl"] = urls["regular"]
    if body.get("userName"):
        detail["uploader"] = body["userName"]
    if tags:
        detail["category"] = tags[0]

    # 页面携带的元信息：简介 / 上传时间 / 浏览数 / 画师 ID
    comment = strip_html(body.get("illustComment"))
    if comment:
        detail["description"] = comment
    if body.get("createDate"):
        detail["uploadDate"] = str(body["createDate"])
    if body.get("viewCount") is not None:
        detail["views"] = _to_int(body["viewCount"])
    if body.get("userId"):
        detail["userId"] = str(body["userId"])
    if body.get("illustType") == 2:
        detail["note"] = "ugoira 动图：首版按首帧处理"

    # 多页作品展开成选集，与其它源的 episodes 语义一致
    if len(qualities) > 1:
        detail["episodes"] = [
            {
                "id": f"{vid}:p{index}",
                "title": f"第 {index + 1} 页",
                "coverUrl": urls.get("regular") or urls.get("small"),
            }
            for index in range(len(qualities))
        ]
    return detail


def normalize_id(raw: str) -> str:
    text = (raw or "").strip()
    if text.isdigit():
        return text
    match = re.search(r"/artworks/(\d+)", text) or re.search(r"/users/(\d+)", text)
    return match.group(1) if match else ""


def strip_html(html: str | None) -> str:
    """Pixiv 的简介是 HTML 片段：换行保留，标签去掉，实体还原。"""
    text = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clamp_page(page: int) -> int:
    try:
        n = int(page)
    except (TypeError, ValueError):
        return 1
    return min(max(n, 1), 500)


def read_cookie(cookie: str | None, key: str) -> str | None:
    if not cookie:
        return None
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == key and value:
            return value
    return None


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = item.get("id")
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def dedupe_users(users: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for user in users:
        uid = user.get("id")
        if uid and uid not in seen:
            seen.add(uid)
            out.append(user)
    return out

__all__ = [
    "PixivSource",
    "build_detail",
    "normalize_id",
    "parse_ranking_items",
    "parse_search_items",
]
