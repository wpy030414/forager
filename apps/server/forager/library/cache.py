"""元数据与封面缓存（移植自 libre-tv 的 library/cache.ts）。

依据：元信息（标题/封面/标签/上传者/相关推荐）几乎不变，一次载入后即可长期复用；
变化的是播放直链——它带签名与时间戳，过期后需重新解析。

策略：
- 详情：命中 meta_cache 且直链未过期 → 直接返回；直链过期 → 仅刷新直链，元数据仍用缓存。
- 封面：以原图 URL 的哈希为文件名落盘；URL 命中时直接读文件，不再回源。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..db import Database, now_iso

META_FRESH_MS = 7 * 24 * 3600_000  # 元数据 7 天内视为新鲜
STREAM_SKEW_MS = 5 * 60_000       # 直链提前 5 分钟视为过期


class MetaCache:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> tuple[dict[str, Any], bool] | None:
        """返回 (详情, 直链是否仍有效)。"""
        row = self.db.query_one("SELECT payload, expires_at FROM meta_cache WHERE key = ?", (key,))
        if row is None:
            return None
        try:
            detail = json.loads(row["payload"])
        except json.JSONDecodeError:
            return None
        expires_at = row["expires_at"]
        valid = expires_at is None or expires_at > int(time.time() * 1000) + STREAM_SKEW_MS
        return detail, valid

    def put(self, key: str, detail: dict[str, Any]) -> None:
        self._write(key, detail, int(time.time() * 1000) + META_FRESH_MS)

    def patch_stream(self, key: str, fresh: dict[str, Any]) -> dict[str, Any] | None:
        """只更新直链与清晰度，其余元数据沿用缓存。"""
        cached = self.get(key)
        if cached is None:
            return None
        merged = dict(cached[0])
        for field in ("streamUrl", "qualities", "streamProxyUrl"):
            if field in fresh:
                merged[field] = fresh[field]
        self._write(key, merged, int(time.time() * 1000) + META_FRESH_MS)
        return merged

    def _write(self, key: str, detail: dict[str, Any], expires_at: int) -> None:
        self.db.execute(
            "INSERT INTO meta_cache (key, payload, cached_at, expires_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
            "cached_at = excluded.cached_at, expires_at = excluded.expires_at",
            (key, json.dumps(detail, ensure_ascii=False), now_iso(), expires_at),
        )


def stream_expires_at(url: str | None) -> int | None:
    """签名直链里的过期时间戳（毫秒）；取不到时返回 None。"""
    if not url:
        return None
    match = re.search(r"[?&](?:secure|expires?|e)=([^&]+)", url, re.IGNORECASE)
    if not match:
        return None
    # hanime1 的 secure 形如 `<sig>,<unix-seconds>`
    for part in match.group(1).split(","):
        if re.fullmatch(r"\d{10}", part):
            return int(part) * 1000
    return None


class ImageCache:
    """封面磁盘缓存：url → sha1 文件名，索引随写随存。"""

    def __init__(self, cache_dir: Path) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index: dict[str, str] = {}
        self._index_file = self.dir / "index.json"
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._index_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for url, name in raw.items():
            path = self.dir / name
            if path.exists() and path.stat().st_size > 0:
                self._index[url] = name

    def find(self, url: str) -> str | None:
        with self._lock:
            name = self._index.get(url)
        return name if name and (self.dir / name).exists() else None

    def save(self, url: str, data: bytes, ext: str) -> str:
        name = f"{hashlib.sha1(url.encode()).hexdigest()[:20]}.{ext}"
        (self.dir / name).write_bytes(data)
        with self._lock:
            self._index[url] = name
            try:
                self._index_file.write_text(json.dumps(self._index), "utf-8")
            except OSError:
                pass  # 索引写失败不影响读缓存
        return name

    def path(self, name: str) -> Path:
        return self.dir / name
