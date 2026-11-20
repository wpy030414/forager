"""Bilibili 适配器测试：WBI 签名与纯解析逻辑（不触网）。"""

from __future__ import annotations

import hashlib

import pytest

from forager.sources.base import SourceError
from forager.sources.bilibili import (
    MIXIN_KEY_ENC_TAB,
    build_detail,
    build_qualities,
    mixin_key,
    parse_bvid,
    sign_query,
    _duration_seconds,
    _https,
    _text,
)


# ---- WBI ----

def test_mixin_key_permutation() -> None:
    # 真实密钥各 32 位，拼起来 64 位，正好覆盖整张置换表
    img = "0123456789abcdef0123456789abcdef"
    sub = "fedcba9876543210fedcba9876543210"
    raw = img + sub
    key = mixin_key(img, sub)
    assert len(key) == 32
    assert key == "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def test_sign_query_is_deterministic_and_sorted() -> None:
    params = {"qn": 80, "cid": 123, "bvid": "BV1xx411c7mD"}
    key = "0123456789abcdef0123456789abcdef"
    query = sign_query(params, key, wts=1_700_000_000)

    base = "bvid=BV1xx411c7mD&cid=123&qn=80&wts=1700000000"
    assert query == f"{base}&w_rid={hashlib.md5((base + key).encode()).hexdigest()}"


def test_sign_query_strips_forbidden_chars() -> None:
    query = sign_query({"keyword": "a!b(c)*d'"}, "k", wts=1)
    assert query.startswith("keyword=abcd&wts=1")


# ---- 解析 ----

PLAYURL_DASH = {
    "dash": {
        "duration": 123,
        "video": [
            {"id": 80, "baseUrl": "https://upos.example/v80-avc.m4s", "bandwidth": 2_000_000},
            {"id": 120, "baseUrl": "https://upos.example/v120.m4s", "bandwidth": 9_000_000},
            {"id": 80, "baseUrl": "https://upos.example/v80-hevc.m4s", "bandwidth": 1_500_000},
        ],
        "audio": [
            {"id": 30280, "baseUrl": "https://upos.example/a128.m4s", "bandwidth": 128_000},
            {"id": 30250, "baseUrl": "https://upos.example/a64.m4s", "bandwidth": 64_000},
        ],
    }
}

VIEW = {
    "bvid": "BV1xx411c7mD",
    "aid": 170001,
    "cid": 456,
    "title": "  某个视频  ",
    "pic": "http://i0.hdslb.com/bfs/archive/cover.jpg",
    "duration": 123,
    "desc": "简介",
    "pubdate": 1_700_000_000,
    "tname": "动画",
    "owner": {"name": "某 UP"},
    "stat": {"view": 12345},
}


def test_build_qualities_dedupes_and_sorts() -> None:
    qualities = build_qualities(PLAYURL_DASH)
    assert [q["label"] for q in qualities] == ["4K", "1080P"]
    assert qualities[0]["qn"] == 120
    # 同清晰度只保留第一条（avc 优先），且都带上最佳音轨
    assert qualities[1]["url"].endswith("v80-avc.m4s")
    assert qualities[0]["audioUrl"].endswith("a128.m4s")


def test_build_qualities_falls_back_to_durl() -> None:
    qualities = build_qualities({"durl": [{"url": "https://upos.example/seg1.mp4"}]})
    assert qualities == [{"label": "auto", "url": "https://upos.example/seg1.mp4", "qn": 0}]


def test_build_detail_maps_fields() -> None:
    detail = build_detail("bilibili", VIEW, PLAYURL_DASH)
    assert detail["id"] == "BV1xx411c7mD"
    assert detail["title"] == "某个视频"
    assert detail["coverUrl"] == "https://i0.hdslb.com/bfs/archive/cover.jpg"
    assert detail["uploader"] == "某 UP"
    assert detail["views"] == 12345
    assert detail["durationSec"] == 123
    assert detail["tags"] == ["动画"]
    assert detail["pageUrl"] == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert detail["streamProxyUrl"] == "/api/sources/bilibili/stream?v=BV1xx411c7mD&quality=4K"
    assert detail["qualities"][0]["label"] == "4K"


def test_build_detail_without_streams_raises() -> None:
    with pytest.raises(SourceError, match="播放地址解析失败"):
        build_detail("bilibili", VIEW, {})


# ---- 工具 ----

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BV1xx411c7mD", ("BV1xx411c7mD", None)),
        ("av170001", (None, 170001)),
        ("170001", (None, 170001)),
        ("https://www.bilibili.com/video/BV1xx411c7mD?spm=1", ("BV1xx411c7mD", None)),
        ("nonsense", (None, None)),
    ],
)
def test_parse_bvid(raw: str, expected) -> None:
    assert parse_bvid(raw) == expected


def test_minor_helpers() -> None:
    assert _text("<em class='keyword'>鬼</em>灭 之刃") == "鬼灭 之刃"
    assert _https("//i0.hdslb.com/x.jpg") == "https://i0.hdslb.com/x.jpg"
    assert _https("http://i0.hdslb.com/x.jpg") == "https://i0.hdslb.com/x.jpg"
    assert _duration_seconds(123) == 123
    assert _duration_seconds("1:02:03") == 3723
    assert _duration_seconds("12:34") == 754
    assert _duration_seconds(0) is None


def test_build_qualities_prefers_avc() -> None:
    payload = {
        "dash": {
            "video": [
                {"id": 80, "baseUrl": "https://upos.example/hevc.m4s", "codecs": "hev1.1.6"},
                {"id": 80, "baseUrl": "https://upos.example/avc.m4s", "codecs": "avc1.64001f"},
            ],
            "audio": [{"id": 30280, "baseUrl": "https://upos.example/a.m4s", "bandwidth": 128000}],
        }
    }
    qualities = build_qualities(payload)
    assert len(qualities) == 1
    assert qualities[0]["url"].endswith("avc.m4s")
