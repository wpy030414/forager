"""本地源：把已入库的内容暴露成一个普通源。

产品哲学：界面上的一切内容都来自「源」——本地库与哔哩哔哩、Hanime1 同级。
本地源的条目 id 就是库的 key（如 ``bilibili:BV1xx``），封面与播放都指向本地文件；
不支持线索发现（kinds 为空），也不参与设置页（无 config_fields）。
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ..db import Database
from .base import BaseSource, SourceError

PAGE_SIZE = 24
MEDIA_PREFIX = "/api/library/media/"


class LocalSource(BaseSource):
    id: ClassVar[str] = "local"
    name: ClassVar[str] = "本地"
    image_hosts: ClassVar[tuple[str, ...]] = ()
    config_fields: ClassVar[tuple[str, ...]] = ()
    #: 本地不产生新内容，不参与线索发现
    kinds: ClassVar[tuple[str, ...]] = ()
    quality_options: ClassVar[tuple[str, ...]] = ()

    def __init__(self, db: Database) -> None:
        super().__init__(client=None)  # 本地源不需要传输层
        self.db = db

    # ---- 契约方法 ----

    async def list(self, page: int = 1, category: str | None = None) -> dict[str, Any]:
        page_no = max(1, int(page or 1))
        rows = self._rows(page_no, category)
        return {
            "items": [self._summary(row) for row in rows],
            "page": page_no,
            "hasMore": len(rows) == PAGE_SIZE,
        }

    async def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        page_no = max(1, int(page or 1))
        rows = self.db.query(
            "SELECT * FROM library WHERE meta_json LIKE ? ORDER BY downloaded_at DESC "
            "LIMIT ? OFFSET ?",
            (f"%{keyword}%", PAGE_SIZE, (page_no - 1) * PAGE_SIZE),
        )
        return {
            "items": [self._summary(row) for row in rows],
            "page": page_no,
            "hasMore": len(rows) == PAGE_SIZE,
        }

    async def detail(self, vid: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM library WHERE key = ?", (vid,))
        if row is None:
            raise SourceError("本地库中没有该条目", 404)
        try:
            meta = json.loads(row["meta_json"])
        except json.JSONDecodeError:
            meta = {}
        media = f"{MEDIA_PREFIX}{row['file_name']}"
        detail: dict[str, Any] = {
            **meta,
            "id": vid,
            "source": self.id,
            "streamUrl": media,
            "streamProxyUrl": media,
            "qualities": [{"label": row["quality"], "url": media}],
        }
        if row["cover_file"]:
            detail["coverUrl"] = f"{MEDIA_PREFIX}{row['cover_file']}"
        return detail

    async def stream_url(self, vid: str, quality: str | None = None) -> str:
        row = self.db.query_one("SELECT file_name FROM library WHERE key = ?", (vid,))
        if row is None:
            raise SourceError("本地库中没有该条目", 404)
        return f"{MEDIA_PREFIX}{row['file_name']}"

    def categories(self) -> list[dict[str, str]]:
        return [
            {"id": row["category"], "name": row["category"]}
            for row in self.db.query(
                "SELECT category FROM library "
                "WHERE category IS NOT NULL AND category != '' "
                "GROUP BY category ORDER BY COUNT(*) DESC"
            )
        ]

    # ---- 图片：封面直接读本地文件 ----

    def allows_image_host(self, url: str) -> bool:
        return url.startswith(MEDIA_PREFIX)

    async def fetch_image(self, url: str) -> bytes:
        name = url[len(MEDIA_PREFIX) :]
        path = self.db.media_path(name)
        if not path.is_file():
            raise SourceError("封面不存在", 404)
        return path.read_bytes()

    # ---- 内部 ----

    def _rows(self, page_no: int, category: str | None):
        clause = ""
        params: tuple[Any, ...] = ()
        if category:
            clause = " WHERE category = ?"
            params = (category,)
        return self.db.query(
            f"SELECT * FROM library{clause} ORDER BY downloaded_at DESC LIMIT ? OFFSET ?",
            (*params, PAGE_SIZE, (page_no - 1) * PAGE_SIZE),
        )

    def _summary(self, row) -> dict[str, Any]:
        try:
            meta = json.loads(row["meta_json"])
        except json.JSONDecodeError:
            meta = {}
        item: dict[str, Any] = {
            "id": row["key"],
            "source": self.id,
            "title": meta.get("title") or row["key"],
            "note": row["quality"],
        }
        if row["cover_file"]:
            item["coverUrl"] = f"{MEDIA_PREFIX}{row['cover_file']}"
        for field in ("durationSec", "views", "uploader"):
            if meta.get(field) is not None:
                item[field] = meta[field]
        return item
