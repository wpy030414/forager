"""Pixiv 适配器的纯解析与 ID 归一化（不触网）。"""

from __future__ import annotations

from forager.sources.pixiv import (
    build_detail,
    clamp_page,
    dedupe_users,
    normalize_id,
    parse_ranking_items,
    parse_search_items,
    read_cookie,
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
    import pytest

    from forager.sources.base import SourceError

    with pytest.raises(SourceError):
        build_detail("pixiv", "1", {"illustTitle": "x", "urls": {}}, [])


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