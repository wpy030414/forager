"""接口冒烟：不触网，只验证装配与契约形状。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from forager.api.app import create_app
from forager.config import Settings


def make_settings(tmp_path) -> Settings:
    return Settings(
        port=11408,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
        hanime1_base_url="https://hanime1.me",
        hanime1_proxy=None,
        hanime1_dns_proxy=None,
        hanime1_pinned_ip="127.0.0.1",  # 防止任何真实 DNS 查询
        hanime1_cookie=None,
        min_interval_ms=0,
        html_cache_ttl_ms=0,
        download_concurrency=1,
        sub_tick_sec=3600,
        sub_initial=0,
    )


def test_health_and_sources(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert [s["id"] for s in client.get("/api/sources").json()] == ["local", "hanime1"]
        assert client.get("/api/library/items").json() == []
        assert client.get("/api/library/mode").json() == {"offline": False}


def test_unknown_source_is_404_with_error_shape(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        res = client.get("/api/sources/nope/list")
        assert res.status_code == 404
        assert "error" in res.json()


def test_search_requires_keyword(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        res = client.get("/api/sources/hanime1/search")
        assert res.status_code == 400
        assert "error" in res.json()


def test_tree_crud(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        created = client.post(
            "/api/tree/nodes",
            json={"source": "hanime1", "kind": "query", "value": "studio", "intervalSec": 600},
        )
        assert created.status_code == 201
        node = created.json()
        assert node["value"] == "studio"
        assert node["kind"] == "query"

        tree = client.get("/api/tree").json()
        assert [g["source"] for g in tree["sources"]] == ["hanime1"]
        assert tree["sources"][0]["total"] == {"discovered": 0, "done": 0}

        assert client.delete(f"/api/tree/nodes/{node['id']}").json() == {"ok": True}
        assert client.get("/api/tree").json()["total"]["discovered"] == 0


def test_tree_rejects_unsupported_kind(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        res = client.post(
            "/api/tree/nodes",
            json={"source": "hanime1", "kind": "favorites", "value": ""},
        )
        assert res.status_code == 400
        assert "不支持" in res.json()["error"]


def test_tree_browse_node_items(tmp_path, monkeypatch) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        node = client.post(
            "/api/tree/nodes",
            json={"source": "hanime1", "kind": "partition", "value": "裏番"},
        ).json()

        async def fake_browse(kind: str, value: str, page: int = 1) -> dict:
            assert (kind, value, page) == ("partition", "裏番", 1)
            return {
                "items": [{"id": "2747", "source": "hanime1", "title": "某作", "kind": "video"}],
                "page": 1,
                "hasMore": False,
            }

        monkeypatch.setattr(app.state.sources["hanime1"], "browse", fake_browse)
        res = client.get(f"/api/tree/nodes/{node['id']}/items")
        assert res.status_code == 200
        assert res.json()["items"][0]["kind"] == "video"

        assert client.get("/api/tree/nodes/99999/items").status_code == 404


def test_library_keys_is_lightweight_list(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/library/keys").json() == []
        app.state.db.execute(
            "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
            "cover_file, size_bytes, downloaded_at, category) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
            ("hanime1:1", "hanime1", "1", "{}", "1080", "a.mp4", 1, "2026-01-01T00:00:00Z", "x"),
        )
        assert client.get("/api/library/keys").json() == ["hanime1:1"]


def test_local_source_is_a_source(tmp_path) -> None:
    """本地即源：列表 / 分区 / 详情 / 播放 / 进度 / 封面都走源契约。"""
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        db.execute(
            "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
            "cover_file, size_bytes, downloaded_at, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "hanime1:1",
                "hanime1",
                "1",
                '{"title": "片一", "durationSec": 120}',
                "1080",
                "a.mp4",
                "covers/a.jpg",
                1,
                "2026-01-01T00:00:00Z",
                "动画",
            ),
        )
        (db.media_dir / "a.mp4").write_bytes(b"video")
        (db.media_dir / "covers" / "a.jpg").write_bytes(b"cover")

        listing = client.get("/api/sources/local/list").json()
        assert [item["id"] for item in listing["items"]] == ["hanime1:1"]
        assert listing["items"][0]["coverUrl"] == "/api/library/media/covers/a.jpg"

        assert client.get("/api/sources/local/categories").json() == [
            {"id": "动画", "name": "动画"}
        ]

        detail = client.get("/api/sources/local/detail/hanime1:1").json()
        assert detail["source"] == "local"
        assert detail["qualities"][0]["url"].endswith("a.mp4")

        play = client.get("/api/library/play/local/hanime1:1").json()
        assert play["source"] == "local"
        assert play["url"].endswith("a.mp4")

        assert client.put(
            "/api/library/progress/local/hanime1:1",
            json={"positionMs": 500, "durationMs": 60000},
        ).json() == {"ok": True}
        assert client.get("/api/library/continue").json()[0]["key"] == "hanime1:1"

        image = client.get("/api/sources/local/image?url=/api/library/media/covers/a.jpg")
        assert image.status_code == 200
        assert image.content == b"cover"

        # 本地源不进设置页，也不参与线索
        assert all(item["source"] != "local" for item in client.get("/api/settings/sources").json())
        assert client.post(
            "/api/tree/nodes",
            json={"source": "local", "kind": "query", "value": "x", "intervalSec": 600},
        ).status_code == 400


def test_home_continue_and_recent(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        db = app.state.db
        for key, title in (("hanime1:1", "片一"), ("hanime1:2", "片二")):
            db.execute(
                "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, "
                "cover_file, size_bytes, downloaded_at, category) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    key,
                    "hanime1",
                    key.split(":")[1],
                    '{"title": "%s"}' % title,
                    "1080",
                    f"{key}.mp4",
                    1,
                    "2026-01-01T00:00:00Z",
                    "动画",
                ),
            )
        # 进度未完成 → 进入继续播放
        db.execute(
            "INSERT INTO progress (key, position_ms, duration_ms, updated_at) "
            "VALUES ('hanime1:1', 60000, 600000, '2026-01-02T00:00:00Z')"
        )
        # 已看完 → 不再出现在继续播放
        db.execute(
            "INSERT INTO progress (key, position_ms, duration_ms, updated_at) "
            "VALUES ('hanime1:2', 599000, 600000, '2026-01-03T00:00:00Z')"
        )

        cont = client.get("/api/library/continue").json()
        assert [item["key"] for item in cont] == ["hanime1:1"]
        assert cont[0]["progress"]["positionMs"] == 60000

        recent = client.get("/api/sources/local/list").json()
        assert recent["items"][0]["id"] == "hanime1:1"


def test_library_facets_and_filters(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/library/facets").json()["total"] == 0
        assert client.get("/api/library/items?source=hanime1&category=裏番").json() == []
