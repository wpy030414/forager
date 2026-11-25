"""hanime1 解析测试（逐条移植自 apps/server/tests/hanime1-parse.test.ts）。

构造最小 HTML 片段（结构取自 hanime1.me 真实页面，文本用占位）。
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from forager.sources.base import ClueQuery, SourceError
from forager.sources.hanime1 import (
    GENRES,
    Hanime1Source,
    extract_vid,
    parse_duration,
    parse_stat_views,
    parse_upload_date,
    parse_views,
)


def card(vid: int, extra: str = "") -> str:
    return f"""
  <div class="video-item-container">
    <div class="horizontal-card">
      <a href="https://hanime1.me/watch?v={vid}" class="video-link">
        <div class="thumb-container">
          <img class="main-thumb" src="https://vdownload.hembed.com/image/thumbnail/{vid}l.jpg?secure=x">
          <div class="duration">{extra or "12:34"}</div>
          <div class="stats-container">
            <div class="stat-item"><i class="material-icons">thumb_up</i>96%</div>
            <div class="stat-item">8.5萬次</div>
          </div>
        </div>
        <div class="title">Title {vid}</div>
      </a>
      <div class="subtitle">
        <a href="https://hanime1.me/search?query=studio">studio-{vid}</a>
        <span class="subtitle-time">• 3週前</span>
      </div>
    </div>
  </div>"""


AD_CARD = """
  <div class="video-item-container">
    <div class="horizontal-card">
      <a href="https://l.erodalabs.com/s/abc" class="video-link">
        <div class="thumb-container"><img class="main-thumb" src="https://vdownload.hembed.com/ad.jpg"></div>
        <div class="title">赞助广告</div>
      </a>
    </div>
  </div>"""


@pytest.fixture()
def source() -> Hanime1Source:
    class _Client:
        base_url = "https://hanime1.me"

    return Hanime1Source(_Client())


def test_cards_extract_required_fields(source: Hanime1Source) -> None:
    items = source.parse_cards(card(1) + card(2))
    assert len(items) == 2
    assert items[0] == {
        "id": "1",
        "source": "hanime1",
        "title": "Title 1",
        "coverUrl": "https://vdownload.hembed.com/image/thumbnail/1l.jpg?secure=x",
        "durationSec": 754,
        "views": 85000,
        "uploader": "studio-1",
    }


def test_cards_skip_ads(source: Hanime1Source) -> None:
    items = source.parse_cards(AD_CARD + card(3))
    assert [i["id"] for i in items] == ["3"]


def test_cards_dedupe(source: Hanime1Source) -> None:
    assert len(source.parse_cards(card(7) + card(7))) == 1


def test_cards_empty_page(source: Hanime1Source) -> None:
    assert source.parse_cards("<html><body></body></html>") == []


# 紧凑卡：站点在部分分区（如「裏番」）只下发这一套模板（结构取自真实页面，文本用占位）
COMPACT_CARD = """
  <div class="home-rows-videos-wrapper">
    <a href="https://hanime1.me/watch?v={vid}" style="display: block;">
      <div class="home-rows-videos-div search-videos hover-lighter">
        <div class="video-card-inner" style="padding-bottom: 133.33%;">
          <img class="lazyload" src="https://vdownload.hembed.com/image/thumbnail/{vid}l.jpg?secure=x">
          <div class="video-date-title">
            Title compact {vid}
          </div>
        </div>
      </div>
    </a>
  </div>"""


def test_compact_cards_are_parsed(source: Hanime1Source) -> None:
    """分区页用紧凑卡模板：必须也能解析出 id / 标题 / 封面。"""
    items = source.parse_cards(COMPACT_CARD.format(vid=11) + COMPACT_CARD.format(vid=12) + COMPACT_CARD.format(vid=11))
    assert items == [
        {
            "id": "11",
            "source": "hanime1",
            "title": "Title compact 11",
            "coverUrl": "https://vdownload.hembed.com/image/thumbnail/11l.jpg?secure=x",
        },
        {
            "id": "12",
            "source": "hanime1",
            "title": "Title compact 12",
            "coverUrl": "https://vdownload.hembed.com/image/thumbnail/12l.jpg?secure=x",
        },
    ]


def test_compact_card_without_title_falls_back_to_id(source: Hanime1Source) -> None:
    html = '<a href="https://hanime1.me/watch?v=9"><div class="video-card-inner"><img src="https://x/9.jpg"></div></a>'
    assert source.parse_cards(html) == [{"id": "9", "source": "hanime1", "title": "9", "coverUrl": "https://x/9.jpg"}]


def test_standard_cards_win_over_compact_scan(source: Hanime1Source) -> None:
    """标准卡页面里也带 watch 链接：不能混进紧凑卡那套解析里造成重复。"""
    items = source.parse_cards(card(1) + COMPACT_CARD.format(vid=1))
    assert [item["id"] for item in items] == ["1"]
    assert "durationSec" in items[0]


WATCH = """
  <html><body>
    <h3 class="video-details-wrapper">[Studio] Some Title</h3>
    <a id="video-artist-name" href="#">Studio Name</a>
    <div id="player-div-wrapper">
      <video id="player" poster="https://vdownload.hembed.com/image/thumbnail/42h.jpg?secure=b">
        <source src="https://vdownload.hembed.com/42-720p.mp4?secure=a" type="video/mp4" size="720">
        <source src="https://vdownload.hembed.com/42-1080p.mp4?secure=c" type="video/mp4" size="1080">
        <source src="https://vdownload.hembed.com/42-480p.mp4?secure=d" type="video/mp4" size="480">
      </video>
    </div>
    <div id="related-tabcontent">__RELATED__</div>
  </body></html>"""


def test_watch_parse(source: Hanime1Source) -> None:
    detail = source.parse_watch("42", WATCH.replace("__RELATED__", card(43)))
    assert detail["title"] == "[Studio] Some Title"
    assert detail["uploader"] == "Studio Name"
    assert [q["label"] for q in detail["qualities"]] == ["1080", "720", "480"]
    assert "1080p" in detail["streamUrl"]
    assert detail["streamProxyUrl"] == "/api/sources/hanime1/stream?v=42&quality=1080"
    assert [r["id"] for r in detail["related"]] == ["43"]
    assert detail["coverUrl"].startswith("https://vdownload.hembed.com/")


def test_watch_without_sources_raises(source: Hanime1Source) -> None:
    with pytest.raises(SourceError, match="播放地址解析失败"):
        source.parse_watch("42", '<video id="player"></video>')


# 详情页携带的元信息（结构取自真实页面，文本用占位）
WATCH_META = """
    <div class="video-details-wrapper">
      <div class="video-description-panel video-description-panel-hover no-select">
        <div class="hidden-xs">觀看次數：5767次&nbsp;&nbsp;2026-06-01</div>
        <div class="video-caption-text caption-ellipsis">First line<br>Second line &amp; more</div>
      </div>
    </div>
    <div class="video-details-wrapper video-tags-wrapper">
      <div class="single-video-tag"><a href="https://hanime1.me/search?genre=%E8%A3%8F%E7%95%AA">裏番</a><span>(1)</span></div>
      <div class="single-video-tag"><a href="https://hanime1.me/search?tags=1080p">1080p</a><span>(1)</span></div>
      <div class="single-video-tag"><a href="#"><i class="material-icons">add</i>add</a><span></span></div>
    </div>
    <meta property="og:video:duration" content="671">
"""


def test_watch_keeps_page_metadata(source: Hanime1Source) -> None:
    detail = source.parse_watch("42", WATCH.replace("  </body>", WATCH_META + "  </body>"))
    assert detail["description"] == "First line Second line & more"
    assert detail["views"] == 5767
    assert detail["uploadDate"] == "2026-06-01"
    assert detail["durationSec"] == 671
    # tags 只放 genre（category 由它推导），自由标签另放 hashtags
    assert detail["tags"] == ["裏番"]
    assert detail["category"] == "裏番"
    assert detail["hashtags"] == ["1080p"]


def test_watch_without_metadata_omits_fields(source: Hanime1Source) -> None:
    detail = source.parse_watch("42", WATCH.replace("__RELATED__", ""))
    for field in ("description", "views", "uploadDate", "durationSec", "hashtags"):
        assert field not in detail


def test_stat_parsers_do_not_glue_date_into_views() -> None:
    stats = "觀看次數：5767次\xa0\xa02026-06-01"
    assert parse_stat_views(stats) == 5767  # 而不是 57672026
    assert parse_stat_views("觀看次數：1.2萬次  2026-01-01") == 12000
    assert parse_stat_views("") is None
    assert parse_upload_date(stats) == "2026-06-01"
    assert parse_upload_date("沒有日期") is None


class _Client:
    base_url = "https://hanime1.me"

    def __init__(self, html: str) -> None:
        self.paths: list[str] = []
        self._html = html

    async def fetch_html(self, path: str, ttl_ms: int | None = None) -> str:
        self.paths.append(path)
        return self._html


async def test_discover_sends_genre_and_query_together() -> None:
    client = _Client(card(1))
    source = Hanime1Source(client)
    page = await source.discover(ClueQuery(category="裏番", keyword="studio"), 3)
    query = parse_qs(urlsplit(client.paths[0]).query)
    assert query == {
        "sort": ["最新上傳"],
        "page": ["3"],
        "genre": ["裏番"],
        "query": ["studio"],
    }
    assert page["page"] == 3 and page["hasMore"] is True
    assert [item["id"] for item in page["items"]] == ["1"]


async def test_discover_without_filters_is_site_wide_latest() -> None:
    client = _Client("<html></html>")
    source = Hanime1Source(client)
    page = await source.discover(ClueQuery(), 1)
    assert parse_qs(urlsplit(client.paths[0]).query) == {"sort": ["最新上傳"], "page": ["1"]}
    assert page == {"items": [], "page": 1, "hasMore": False}


async def test_source_page_and_keyword_search_share_one_endpoint() -> None:
    client = _Client(card(1))
    source = Hanime1Source(client)
    await source.list(2, "3DCG")
    await source.search("studio", 1)
    await source.partition_items("MMD", 1)
    assert [parse_qs(urlsplit(p).query) for p in client.paths] == [
        {"sort": ["最新上傳"], "page": ["2"], "genre": ["3DCG"]},
        {"sort": ["最新上傳"], "page": ["1"], "query": ["studio"]},
        {"sort": ["最新上傳"], "page": ["1"], "genre": ["MMD"]},
    ]


def test_genres_are_the_site_genres() -> None:
    assert {"裏番", "Motion Anime", "3DCG"} <= {genre for genre, _ in GENRES}
    assert Hanime1Source(_Client("")).categories() == [
        {"id": genre, "name": label} for genre, label in GENRES
    ]


def test_validate_clue_rejects_uploader_and_expand_kinds() -> None:
    source = Hanime1Source(_Client(""))
    source.validate_clue(ClueQuery(category="裏番", keyword="x"), 0)  # 叠加与「全部」都允许
    with pytest.raises(SourceError, match="发布者"):
        source.validate_clue(ClueQuery(uploader="1"), 1)
    with pytest.raises(SourceError, match="线索类型"):
        source.validate_clue(ClueQuery(kind="favorites"), 1)


def test_extract_vid() -> None:
    assert extract_vid("https://hanime1.me/watch?v=409030") == "409030"
    assert extract_vid("/watch?x=1&v=12") == "12"
    assert extract_vid("https://example.com/") is None


def test_parse_duration() -> None:
    assert parse_duration("09:34") == 574
    assert parse_duration("1:03:06") == 3786
    assert parse_duration("bad") is None


def test_parse_views() -> None:
    assert parse_views("8.5萬次") == 85000
    assert parse_views("1.2万") == 12000
    assert parse_views("3,412次") == 3412
    assert parse_views("12.3M") == 12300000
