"""归档副产物：元信息投影、movie NFO、.info.json 与海报命名（纯离线）。"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from forager.library.sidecar import (
    archive_meta,
    movie_nfo,
    poster_filename,
    sidecar_paths,
    write_sidecars,
)

META = {
    "id": "7",
    "source": "hanime1",
    "title": "A & B <Title>",
    "pageUrl": "https://hanime1.me/watch?v=7",
    "uploader": "Studio",
    "tags": ["裏番"],
    "category": "裏番",
    "description": "line\x0bone & two",  # \x0b 是 XML 非法控制字符
    "uploadDate": "2026-06-01",
    "views": 5767,
    "durationSec": 671,
    "hashtags": ["1080p"],
    "coverUrl": "https://x/7.jpg",
}


def test_archive_meta_drops_transport_fields() -> None:
    detail = {**META, "streamUrl": "https://x/7.mp4?secure=a", "streamProxyUrl": "/api/…", "qualities": [{"url": "…"}], "related": [{"id": "8"}]}
    assert archive_meta(detail) == META


def test_movie_nfo_maps_and_escapes_fields() -> None:
    nfo = movie_nfo(META, "vid-7-1080-poster.jpg")
    assert nfo.startswith("<?xml")
    root = ET.fromstring(nfo)
    assert root.tag == "movie"
    assert root.findtext("title") == "A & B <Title>"
    assert root.findtext("plot") == "lineone & two"  # 非法控制字符被剔除，& 正常还原
    assert root.findtext("premiered") == "2026-06-01"
    assert root.findtext("studio") == "Studio"
    assert root.findtext("runtime") == "11"  # 671 秒 → 整分钟
    assert [node.text for node in root.findall("genre")] == ["裏番"]
    assert [node.text for node in root.findall("tag")] == ["1080p"]
    unique = root.find("uniqueid")
    assert (unique.text, unique.get("type"), unique.get("default")) == ("7", "hanime1", "true")
    thumb = root.find("thumb")
    assert (thumb.text, thumb.get("aspect")) == ("vid-7-1080-poster.jpg", "poster")


def test_movie_nfo_skips_missing_fields() -> None:
    root = ET.fromstring(movie_nfo({"id": "1", "source": "pixiv", "title": "t"}))
    assert [child.tag for child in root] == ["title", "uniqueid"]


def test_sidecar_naming() -> None:
    video = Path("media/hanime1-7-1080.mp4")
    assert poster_filename(video, "jpg") == "hanime1-7-1080-poster.jpg"
    assert [p.name for p in sidecar_paths(video)] == ["hanime1-7-1080.info.json", "hanime1-7-1080.nfo"]
    # 图片类条目不写 NFO：媒体库不认
    assert [p.name for p in sidecar_paths(Path("media/pixiv-1-p0.png"))] == ["pixiv-1-p0.info.json"]


def test_write_sidecars(tmp_path) -> None:
    video = tmp_path / "hanime1-7-1080.mp4"
    video.write_bytes(b"v")
    written = write_sidecars(video, META, "hanime1-7-1080-poster.jpg")
    assert [p.name for p in written] == ["hanime1-7-1080.info.json", "hanime1-7-1080.nfo"]
    assert json.loads((tmp_path / "hanime1-7-1080.info.json").read_text("utf-8")) == META
    assert "hanime1-7-1080-poster.jpg" in (tmp_path / "hanime1-7-1080.nfo").read_text("utf-8")

    image = tmp_path / "pixiv-1-p0.png"
    image.write_bytes(b"i")
    assert [p.name for p in write_sidecars(image, {"id": "1", "title": "中文"}, None)] == [
        "pixiv-1-p0.info.json"
    ]
