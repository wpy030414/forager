"""Pixiv 适配器的纯解析与 ID 归一化（不触网）。"""

from __future__ import annotations

import pytest

from forager.sources.base import ClueQuery, SourceError
from forager.sources.pixiv import (
    MAX_PAGES,
    PixivSource,
    build_detail,
    clamp_page,
    dedupe_users,
    normalize_id,
    parse_ranking_items,
    parse_search_items,
    read_cookie,
    strip_html,
)

SEARCH_ROW = {
    "id": "152435730",
    "title": "smile",
    "illustType": 0,
    "url": "https://i.pximg.net/c/250x250_80_a2/img/1_p0_square1200.jpg",
    "userId": "124035946",
    "userName": "HHI",
    "pageCount": 3,
}

RANKING_ROW = {
    "illust_id": "152435730",
    "title": "smile",
    "url": "https://i.pximg.net/c/240x480/img-master/a_p0_master1200.jpg",
    "user_name": "HHI",
}

DETAIL_BODY = {
    "illustId": "152435730",
    "illustTitle": "smile",
    "illustType": 0,
    "urls": {
        "regular": "https://i.pximg.net/img-master/1_p0_master1200.jpg",
        "original": "https://i.pximg.net/img-original/1_p0.png",
    },
    "userName": "HHI",
    "tags": {"tags": [{"tag": "VOCALOID"}, {"tag": "初音ミク"}]},
}

PAGES = [
    {"urls": {"original": "https://i.pximg.net/img-original/1_p0.png"}},
    {"urls": {"original": "https://i.pximg.net/img-original/1_p1.png"}},
]


def test_normalize_id_accepts_numbers_and_urls() -> None:
    assert normalize_id("152435730") == "152435730"
    assert normalize_id("https://www.pixiv.net/artworks/152435730") == "152435730"
    assert normalize_id("https://www.pixiv.net/users/124035946") == "124035946"
    assert normalize_id("") == ""
    assert normalize_id("nonsense") == ""


def test_parse_search_items_maps_fields_and_dedupes() -> None:
    items = parse_search_items([SEARCH_ROW, SEARCH_ROW], "pixiv")
    assert len(items) == 1
    assert items[0] == {
        "id": "152435730",
        "source": "pixiv",
        "title": "smile",
        "coverUrl": SEARCH_ROW["url"],
        "uploader": "HHI",
        "note": "3 页",
    }


def test_parse_ranking_items_upgrades_thumbnail() -> None:
    items = parse_ranking_items([RANKING_ROW], "pixiv")
    assert items[0]["id"] == "152435730"
    assert items[0]["uploader"] == "HHI"
    assert "/c/480x960/" in items[0]["coverUrl"]
    assert "/c/240x480/" not in items[0]["coverUrl"]


def test_build_detail_uses_original_pages() -> None:
    detail = build_detail("pixiv", "152435730", DETAIL_BODY, PAGES)
    assert detail["title"] == "smile"
    assert detail["pageUrl"] == "https://www.pixiv.net/artworks/152435730"
    assert [q["label"] for q in detail["qualities"]] == ["p0 原图", "p1 原图"]
    assert detail["streamUrl"] == PAGES[0]["urls"]["original"]
    assert detail["category"] == "VOCALOID"
    assert [e["id"] for e in detail["episodes"]] == ["152435730:p0", "152435730:p1"]


def test_build_detail_single_page_has_no_episodes() -> None:
    detail = build_detail("pixiv", "1", DETAIL_BODY, PAGES[:1])
    assert len(detail["qualities"]) == 1
    assert "episodes" not in detail


def test_build_detail_falls_back_to_single_original() -> None:
    detail = build_detail("pixiv", "1", DETAIL_BODY, [])
    assert detail["qualities"][0]["label"] == "原图"
    assert detail["streamUrl"].endswith("1_p0.png")


def test_build_detail_without_urls_raises() -> None:
    with pytest.raises(SourceError):
        build_detail("pixiv", "1", {"illustTitle": "x", "urls": {}}, [])


def test_build_detail_keeps_page_metadata() -> None:
    body = {
        **DETAIL_BODY,
        "illustComment": "line1<br>line2 &amp; <b>more</b>",
        "createDate": "2026-11-01T12:00:00+09:00",
        "viewCount": 1234,
        "userId": "42",
    }
    detail = build_detail("pixiv", "1", body, PAGES[:1])
    assert detail["description"] == "line1\nline2 & more"
    assert detail["uploadDate"] == "2026-11-01T12:00:00+09:00"
    assert (detail["views"], detail["userId"]) == (1234, "42")
    assert "description" not in build_detail("pixiv", "1", DETAIL_BODY, PAGES[:1])


def test_strip_html() -> None:
    assert strip_html(None) == ""
    assert strip_html("<a href='x'>A&lt;B</a>") == "A<B"


# ---- 线索能力 ----


def test_validate_clue_matrix() -> None:
    source = PixivSource(client=None)
    source.validate_clue(ClueQuery(keyword="miku"), 0)
    source.validate_clue(ClueQuery(category="daily"), 1)
    source.validate_clue(ClueQuery(uploader="123"), 0)
    source.validate_clue(ClueQuery(kind="favorites"), 1)
    with pytest.raises(SourceError, match="只能填一项"):
        source.validate_clue(ClueQuery(category="daily", keyword="miku"), 1)
    with pytest.raises(SourceError, match="排行榜不支持"):
        source.validate_clue(ClueQuery(category="weekly"), 0)


async def test_discover_dispatches_to_the_matching_endpoint(monkeypatch) -> None:
    source = PixivSource(client=None)
    seen: list[tuple] = []

    async def search(kw, page=1):
        seen.append(("search", kw, page))
        return {"items": [], "page": page, "hasMore": False}

    async def partition(value, page=1):
        seen.append(("partition", value, page))
        return {"items": [], "page": page, "hasMore": False}

    async def uploader(value, page=1):
        seen.append(("uploader", value, page))
        return {"items": [], "page": page, "hasMore": False}

    async def listing(page=1, category=None):
        seen.append(("list", category, page))
        return {"items": [], "page": page, "hasMore": False}

    monkeypatch.setattr(source, "search", search)
    monkeypatch.setattr(source, "partition_items", partition)
    monkeypatch.setattr(source, "uploader_items", uploader)
    monkeypatch.setattr(source, "list", listing)

    await source.discover(ClueQuery(keyword="miku"), 2)
    await source.discover(ClueQuery(category="daily"))
    await source.discover(ClueQuery(uploader="123"))
    await source.discover(ClueQuery())
    assert seen == [
        ("search", "miku", 2),
        ("partition", "daily", 1),
        ("uploader", "123", 1),
        ("list", None, 1),
    ]


async def test_uploader_items_past_cap_is_an_empty_page(monkeypatch) -> None:
    """翻到顶要给「全部」一个干净的终点，而不是抛 404 把线索标成 error。"""
    source = PixivSource(client=None)

    async def never(uid, page):
        raise AssertionError("到顶之后不该再请求")

    monkeypatch.setattr(source, "_user_illust_ids", never)
    assert await source.uploader_items("123", MAX_PAGES + 1) == {
        "items": [],
        "page": MAX_PAGES + 1,
        "hasMore": False,
    }


def test_read_cookie() -> None:
    cookie = "PHPSESSID=abc; pixiv_uid=12345678; other=1"
    assert read_cookie(cookie, "pixiv_uid") == "12345678"
    assert read_cookie(cookie, "missing") is None
    assert read_cookie(None, "pixiv_uid") is None


def test_dedupe_users_keeps_order() -> None:
    users = [
        {"id": "1", "title": "a"},
        {"id": "1", "title": "a-dup"},
        {"id": "2", "title": "b"},
        {"id": "", "title": "skip"},
    ]
    assert [u["id"] for u in dedupe_users(users)] == ["1", "2"]


def test_clamp_page() -> None:
    assert clamp_page(0) == 1
    assert clamp_page("7") == 7
    assert clamp_page(99999) == 500
    assert clamp_page(None) == 1