"""归档副产物：媒体文件旁的元信息。

除了媒体本身，页面携带的元信息还落在本地三处：
- library 表的 meta_json：查询与浏览用（见 archive_meta）
- <stem>.info.json：同一份快照的无损副本，库丢了文件也能自解释
- <stem>.nfo：视频类条目再写一份 Kodi / Emby / Jellyfin 认的 movie NFO
海报由 Downloader 落成 <stem>-poster.<ext>，媒体库能直接识别。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

#: 传输层字段：签名直链一天就过期，related 只是当时的推荐，都不是「页面携带的元信息」
TRANSPORT_FIELDS = frozenset({"streamUrl", "streamProxyUrl", "qualities", "related"})

#: 只有视频类条目写 NFO；图片（Pixiv）写了媒体库也不认
VIDEO_EXTS = (".mp4", ".m4v", ".webm", ".mkv")

_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def archive_meta(detail: dict[str, Any]) -> dict[str, Any]:
    """入库快照 = 页面元信息投影：去掉会过期与纯传输用的字段。"""
    return {key: value for key, value in detail.items() if key not in TRANSPORT_FIELDS}


def is_video(target: Path) -> bool:
    return target.suffix.lower() in VIDEO_EXTS


def poster_filename(target: Path, ext: str) -> str:
    return f"{target.stem}-poster.{ext}"


def info_path(target: Path) -> Path:
    return target.with_name(f"{target.stem}.info.json")


def nfo_path(target: Path) -> Path:
    return target.with_name(f"{target.stem}.nfo")


def sidecar_paths(target: Path) -> list[Path]:
    """某个媒体文件应有的全部元信息文件（不含海报，海报名记在 library.cover_file）。"""
    paths = [info_path(target)]
    if is_video(target):
        paths.append(nfo_path(target))
    return paths


def movie_nfo(meta: dict[str, Any], poster: str | None = None) -> str:
    """Kodi / Emby / Jellyfin 通用的 movie NFO。"""
    root = ET.Element("movie")

    def put(tag: str, value: Any) -> None:
        text = _plain(value)
        if text:
            ET.SubElement(root, tag).text = text

    put("title", meta.get("title"))
    put("plot", meta.get("description"))
    put("premiered", meta.get("uploadDate"))
    put("studio", meta.get("uploader"))
    duration = _int(meta.get("durationSec"))
    if duration:
        put("runtime", max(1, round(duration / 60)))  # Kodi 的 runtime 是整分钟
    for genre in meta.get("tags") or []:
        put("genre", genre)
    for tag in meta.get("hashtags") or []:
        put("tag", tag)

    unique = ET.SubElement(root, "uniqueid", {"type": str(meta.get("source") or ""), "default": "true"})
    unique.text = _plain(meta.get("id"))
    if poster:
        ET.SubElement(root, "thumb", {"aspect": "poster"}).text = poster
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def write_sidecars(target: Path, meta: dict[str, Any], poster: str | None = None) -> list[Path]:
    """在媒体文件旁写 .info.json（必有）与 .nfo（仅视频）。"""
    info_path(target).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    written = [info_path(target)]
    if is_video(target):
        nfo_path(target).write_text(movie_nfo(meta, poster), encoding="utf-8")
        written.append(nfo_path(target))
    return written


def _plain(value: Any) -> str:
    if value is None:
        return ""
    return _XML_ILLEGAL.sub("", str(value)).strip()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
