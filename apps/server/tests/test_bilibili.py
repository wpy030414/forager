"""Bilibili 适配器测试：WBI 签名与纯解析逻辑（不触网）。"""

from __future__ import annotations

import hashlib

import pytest

from forager.sources.base import SourceError
from forager.sources.bilibili import (
    MIXIN_KEY_ENC_TAB,
    BilibiliSource,
    build_detail,
    build_pgc_detail,
    build_pgc_episodes,
    build_pgc_series_detail,
    build_pgc_summary,
    build_qualities,
    build_ugc_episodes,
    find_ugc_page,
    latest_pgc_episode,
    mixin_key,
    parse_bvid,
    parse_ep_id,
    parse_part_id,
    parse_pgc_timeline,
    parse_season_id,
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


# ---- PGC（番剧 / 国创 / 综艺）----

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ep7702239", 7702239),
        ("EP123", 123),
        ("https://www.bilibili.com/bangumi/play/ep26988?spm=1", 26988),
        ("ss33415", None),
        ("BV1Zx411v7DF", None),
        ("7702239", None),
    ],
)
def test_parse_ep_id(raw: str, expected: int | None) -> None:
    assert parse_ep_id(raw) == expected


def test_parse_pgc_timeline_filters_and_sorts() -> None:
    days = [
        {
            "episodes": [
                {
                    "episode_id": 1,
                    "published": 1,
                    "pub_ts": 100,
                    "title": "旧番",
                    "pub_index": "第1话",
                    "cover": "http://i0.hdslb.com/a.jpg",
                },
                {"episode_id": 2, "published": 0, "pub_ts": 300, "title": "未播出"},
                {"episode_id": 3, "published": 1, "pub_ts": 900, "title": "未来"},
            ]
        },
        {
            "episodes": [
                {
                    "episode_id": 4,
                    "published": 1,
                    "pub_ts": 200,
                    "title": "新番",
                    "pub_index": "第2话",
                    "ep_cover": "//i0.hdslb.com/b.jpg",
                },
                {"episode_id": 1, "published": 1, "pub_ts": 100, "title": "重复"},
            ]
        },
    ]
    items = parse_pgc_timeline(days, now=500)
    assert [item["id"] for item in items] == ["ep4", "ep1"]
    assert items[0]["title"] == "新番 · 第2话"
    assert items[0]["coverUrl"] == "https://i0.hdslb.com/b.jpg"


def test_build_pgc_summary() -> None:
    season = {"title": "校园迷糊大王", "cover": "https://x/season.png"}
    episode = {
        "ep_id": 26988,
        "title": "1",
        "long_title": "浩浩荡荡的小鹿乱撞",
        "duration": 1405753,
        "pub_time": 1282406160,
        "cover": "https://x/ep.gif",
        "stat": {"view": 999},
    }
    item = build_pgc_summary("bilibili", season, episode)
    assert item["id"] == "ep26988"
    assert item["title"] == "校园迷糊大王 · 第1话 · 浩浩荡荡的小鹿乱撞"
    assert item["durationSec"] == 1405
    assert item["coverUrl"] == "https://x/season.png"
    assert item["views"] == 999


def test_latest_pgc_episode_skips_future() -> None:
    season = {
        "episodes": [
            {"ep_id": 1, "pub_time": 100},
            {"ep_id": 2, "pub_time": 400},
            {"ep_id": 3, "pub_time": 900},
        ]
    }
    assert latest_pgc_episode(season, now=500)["ep_id"] == 2
    assert latest_pgc_episode({"episodes": []}, now=500) is None


def test_build_pgc_detail_uses_real_quality() -> None:
    season = {
        "season_id": 1446,
        "title": "校园迷糊大王",
        "season_type_name": "番剧",
        "cover": "https://x/season.png",
        "stat": {"views": 123},
        "up_info": {"uname": "某 UP"},
    }
    episode = {
        "ep_id": 26988,
        "aid": 12349185,
        "bvid": "BV1Zx411v7DF",
        "cid": 20340857,
        "title": "1",
        "long_title": "浩浩荡荡的小鹿乱撞",
        "duration": 1405753,
        "pub_time": 1282406160,
    }
    play = {"quality": 32, "durl": [{"url": "https://upos.example/480.mp4"}]}

    detail = build_pgc_detail("bilibili", season, episode, play)
    assert detail["id"] == "ep26988"
    assert detail["pageUrl"] == "https://www.bilibili.com/bangumi/play/ep26988"
    assert detail["qualities"][0]["label"] == "480P"
    assert detail["streamProxyUrl"] == "/api/sources/bilibili/stream?v=ep26988&quality=480P"
    assert detail["durationSec"] == 1405
    assert detail["category"] == "番剧"
    assert detail["uploader"] == "某 UP"
    assert detail["views"] == 123


def test_build_pgc_detail_without_streams_raises() -> None:
    with pytest.raises(SourceError, match="播放地址解析失败"):
        build_pgc_detail("bilibili", {"title": "x"}, {"ep_id": 1}, {})


@pytest.mark.asyncio
async def test_partition_uses_pgc_timeline_for_tid_13(monkeypatch) -> None:
    source = BilibiliSource(client=None)  # 纯路由测试，不触网
    calls: list[tuple[str, str]] = []

    async def fake_json(url: str, ttl_ms: int, *, allow=frozenset(), root: str = "data"):
        calls.append((url, root))
        assert "/pgc/web/timeline" in url
        assert "types=1" in url
        return [
            {
                "episodes": [
                    {
                        "episode_id": 7702239,
                        "published": 1,
                        "pub_ts": 1794967200,
                        "title": "小羊肖恩 第七季",
                        "pub_index": "第17话~第18话",
                        "cover": "https://x/cover.jpg",
                    }
                ]
            }
        ]

    monkeypatch.setattr(source, "_json", fake_json)
    result = await source.partition_items("13")

    assert result["items"][0]["id"] == "ep7702239"
    assert len(calls) == 1
    assert calls[0][1] == "result"


@pytest.mark.asyncio
async def test_partition_keeps_ugc_ranking_for_tid_4(monkeypatch) -> None:
    source = BilibiliSource(client=None)
    calls: list[tuple[str, dict]] = []

    async def fake_get(path: str, params=None, ttl_ms: int = 60_000, allow=frozenset()):
        calls.append((path, params or {}))
        return {"list": [{"bvid": "BV1xx411c7mD", "title": "某视频"}]}

    monkeypatch.setattr(source, "_get", fake_get)
    result = await source.partition_items("4")

    assert result["items"][0]["id"] == "BV1xx411c7mD"
    assert calls[0][0] == "/x/web-interface/ranking/v2"
    assert calls[0][1]["rid"] == "4"


# ---- 分P / 季度下探 ----

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BV1xx411c7mD:p2", ("BV1xx411c7mD", 2)),
        ("BV1xx411c7mD?p=12", ("BV1xx411c7mD", 12)),
        ("BV1xx411c7mD", None),
        ("ep123", None),
    ],
)
def test_parse_part_id(raw: str, expected) -> None:
    assert parse_part_id(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ss33415", 33415),
        ("https://www.bilibili.com/bangumi/play/ss33415?spm=1", 33415),
        ("ep123", None),
    ],
)
def test_parse_season_id(raw: str, expected: int | None) -> None:
    assert parse_season_id(raw) == expected


def test_build_ugc_episodes_only_for_multi_part() -> None:
    single = {"pages": [{"page": 1, "cid": 1, "part": "正片", "duration": 120}]}
    assert build_ugc_episodes("BV1xx411c7mD", single) == []

    multi = {
        "pages": [
            {"page": 1, "cid": 1, "part": "上", "duration": 120},
            {"page": 2, "cid": 2, "part": "下", "duration": 90},
        ]
    }
    episodes = build_ugc_episodes("BV1xx411c7mD", multi, cover_url="https://x/c.jpg")
    assert [ep["id"] for ep in episodes] == ["BV1xx411c7mD:p1", "BV1xx411c7mD:p2"]
    assert episodes[1]["title"] == "P2 下"
    assert episodes[1]["durationSec"] == 90
    assert episodes[0]["coverUrl"] == "https://x/c.jpg"


def test_find_ugc_page_falls_back_to_single_part() -> None:
    view = {"cid": 9, "pages": [{"page": 1, "cid": 9}]}
    assert find_ugc_page(view, 1)["cid"] == 9
    assert find_ugc_page(view, 2) is None


def test_build_pgc_episodes_filters_future_and_sorts() -> None:
    season = {
        "cover": "https://x/season.png",
        "episodes": [
            {"ep_id": 2, "title": "2", "long_title": "后篇", "pub_time": 200, "duration": 120000},
            {"ep_id": 3, "title": "3", "long_title": "未播", "pub_time": 900, "duration": 120000},
            {"ep_id": 1, "title": "1", "long_title": "前篇", "pub_time": 100, "duration": 120000},
        ],
    }
    episodes = build_pgc_episodes(season, now=500)
    assert [ep["id"] for ep in episodes] == ["ep1", "ep2"]
    assert episodes[0]["title"] == "第1话 · 前篇"
    assert episodes[0]["coverUrl"] == "https://x/season.png"


def test_build_pgc_series_detail_lists_episodes() -> None:
    season = {
        "season_id": 1446,
        "title": "校园迷糊大王",
        "season_type_name": "番剧",
        "cover": "https://x/season.png",
        "stat": {"views": 123},
        "episodes": [
            {"ep_id": 1, "title": "1", "long_title": "第一话", "pub_time": 100},
            {"ep_id": 2, "title": "2", "long_title": "第二话", "pub_time": 200},
        ],
    }
    detail = build_pgc_series_detail("bilibili", season, now=500)
    assert detail["id"] == "ss1446"
    assert detail["series"] is True
    assert detail["pageUrl"] == "https://www.bilibili.com/bangumi/play/ss1446"
    assert [ep["id"] for ep in detail["episodes"]] == ["ep1", "ep2"]


def test_build_pgc_detail_includes_siblings() -> None:
    season = {
        "season_id": 1446,
        "title": "校园迷糊大王",
        "episodes": [
            {"ep_id": 1, "title": "1", "pub_time": 100},
            {"ep_id": 2, "title": "2", "pub_time": 200},
        ],
    }
    episode = season["episodes"][0]
    detail = build_pgc_detail(
        "bilibili", season, episode, {"quality": 32, "durl": [{"url": "https://x/1.mp4"}]}
    )
    assert detail["series"] is True
    assert [ep["id"] for ep in detail["episodes"]] == ["ep1", "ep2"]
