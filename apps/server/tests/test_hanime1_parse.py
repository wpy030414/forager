"""hanime1 解析测试（逐条移植自 apps/server/tests/hanime1-parse.test.ts）。

构造最小 HTML 片段（结构取自 hanime1.me 真实页面，文本用占位）。
"""

from __future__ import annotations

import pytest

from forager.sources.hanime1 import (
    Hanime1Source,
    extract_vid,
    parse_duration,
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
    from forager.sources.base import SourceError

    with pytest.raises(SourceError, match="播放地址解析失败"):
        source.parse_watch("42", '<video id="player"></video>')


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
