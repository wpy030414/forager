"""接口冒烟：不触网，只验证装配与契约形状。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from forager.api.app import create_app
from forager.auth import hash_secret
from forager.config import Settings
from forager.sources.hanime1 import Hanime1Source


TEST_PASSWORD = "test-password-123"


def _login(client, password: str = TEST_PASSWORD) -> None:
    """lifespan 里 bootstrap 生成的密码只在日志里；测试直接改库再走真实登录接口。"""
    db = client.app.state.db
    db.execute("UPDATE users SET password_hash = ? WHERE id = 1", (hash_secret(password),))
    res = client.post("/api/session/login", json={"username": "admin", "password": password})
    assert res.status_code == 200, res.text


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
    )


def test_health_and_sources(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/health").json() == {"status": "ok"}  # 健康检查不设门
        _login(client)
        assert [s["id"] for s in client.get("/api/sources").json()] == ["local", "hanime1", "pixiv"]
        assert client.get("/api/library/items").json() == []
        assert client.get("/api/library/mode").json() == {"offline": False}


def test_unknown_source_is_404_with_error_shape(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        _login(client)
        res = client.get("/api/sources/nope/list")
        assert res.status_code == 404
        assert "error" in res.json()


def test_search_requires_keyword(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        _login(client)
        res = client.get("/api/sources/hanime1/search")
        assert res.status_code == 400
        assert "error" in res.json()


def test_fetchers_and_clues_crud(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        _login(client)
        empty = client.get("/api/fetchers").json()
        assert empty["fetchers"] == []
        assert empty["total"] == {"discovered": 0, "done": 0, "pending": 0, "failed": 0}
        assert [s["id"] for s in empty["sources"]] == ["hanime1", "pixiv"]  # 本地源不进线索
        assert [t["value"] for t in empty["triggers"]] == ["manual", "1h", "1d", "1w", "1m"]

        created = client.post("/api/fetchers", json={"name": "每日", "trigger": "1d"})
        assert created.status_code == 201
        fetcher = created.json()
        assert (fetcher["name"], fetcher["trigger"], fetcher["clues"]) == ("每日", "1d", [])
        assert client.post("/api/fetchers", json={"name": "x", "trigger": "sometimes"}).status_code == 400

        clue = client.post(
            "/api/clues",
            json={
                "fetcherId": fetcher["id"],
                "source": "hanime1",
                "partition": "裏番",
                "keyword": "studio",
                "pages": 5,
            },
        )
        assert clue.status_code == 201
        body = clue.json()
        assert (body["source"], body["partition"], body["keyword"], body["pages"]) == (
            "hanime1", "裏番", "studio", 5,
        )
        assert body["title"] == "裏番 · 关键词「studio」 · 前 5 页"
        assert body["derived"] is False

        listed = client.get("/api/fetchers").json()
        assert [f["id"] for f in listed["fetchers"]] == [fetcher["id"]]
        assert [c["id"] for c in listed["fetchers"][0]["clues"]] == [body["id"]]
        assert listed["sources"][0]["categories"][0] == {"id": "裏番", "name": "裏番"}

        assert client.delete(f"/api/clues/{body['id']}").json() == {"ok": True}
        assert client.get("/api/fetchers").json()["fetchers"][0]["clues"] == []
        assert client.delete(f"/api/clues/{body['id']}").status_code == 404
        assert client.delete(f"/api/fetchers/{fetcher['id']}").json() == {"ok": True}
        assert client.delete(f"/api/fetchers/{fetcher['id']}").status_code == 404
        assert client.get("/api/fetchers").json()["fetchers"] == []


def test_clue_validation(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        _login(client)
        fetcher_id = client.post("/api/fetchers", json={"name": "f"}).json()["id"]

        def post(**extra):
            return client.post("/api/clues", json={"fetcherId": fetcher_id, **extra})

        assert post(source="hanime1", uploader="123").status_code == 400  # Hanime1 没有发布者维度
        assert post(source="hanime1", partition="不存在的分区").status_code == 400
        assert post(source="hanime1", pages=501).status_code == 400
        assert post(source="hanime1", pages="abc").status_code == 400
        assert post(source="pixiv", partition="daily", keyword="miku").status_code == 400  # 只能填一项
        assert post(source="pixiv", partition="daily", pages=0).status_code == 400  # 排行榜没有「全部」
        assert post(source="nope").status_code == 400
        assert client.post("/api/clues", json={"fetcherId": 999, "source": "hanime1"}).status_code == 400

        assert post(source="hanime1", pages=0).status_code == 201  # 什么都不填 = 全站最新
        assert post(source="pixiv", partition="daily", pages=1).status_code == 201
        assert post(source="pixiv", uploader="123").status_code == 201
        assert post(source="pixiv", kind="favorites").status_code == 201


def test_clue_items_browse_without_recording(tmp_path, monkeypatch) -> None:
    """选中线索浏览在线内容：只读，不写线索记忆、不入下载队列。"""
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        fetcher_id = client.post("/api/fetchers", json={"name": "f"}).json()["id"]
        clue = client.post(
            "/api/clues",
            json={"fetcherId": fetcher_id, "source": "hanime1", "partition": "裏番", "pages": 2},
        ).json()

        async def fake_discover(self, query, page: int = 1) -> dict:
            assert (query.category, query.keyword, page) == ("裏番", None, 2)
            return {
                "items": [{"id": "2747", "source": "hanime1", "title": "某作", "kind": "video"}],
                "page": 2,
                "hasMore": False,
            }

        monkeypatch.setattr(Hanime1Source, "discover", fake_discover)
        res = client.get(f"/api/clues/{clue['id']}/items?page=2")
        assert res.status_code == 200
        assert res.json()["items"][0]["kind"] == "video"

        assert app.state.db.query("SELECT * FROM clue_seen") == []
        assert client.get("/api/library/downloads").json() == {
            "jobs": [],
            "counts": {"queued": 0, "downloading": 0, "done": 0, "error": 0},
        }

        assert client.get("/api/clues/99999/items").status_code == 404


def test_library_keys_is_lightweight_list(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
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
        _login(client)
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
        fetcher_id = client.post("/api/fetchers", json={"name": "f"}).json()["id"]
        assert (
            client.post(
                "/api/clues", json={"fetcherId": fetcher_id, "source": "local", "keyword": "x"}
            ).status_code
            == 400
        )


def test_hanime1_source_page_offers_genres(tmp_path) -> None:
    """源浏览页与线索表单共用同一张分区表。"""
    with TestClient(create_app(make_settings(tmp_path))) as client:
        _login(client)
        genres = client.get("/api/sources/hanime1/categories").json()
        assert {"裏番", "Motion Anime", "3DCG"} <= {g["id"] for g in genres}
        assert all(g["id"] == g["name"] for g in genres)


def test_home_continue_and_recent(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
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
        _login(client)
        assert client.get("/api/library/facets").json()["total"] == 0
        assert client.get("/api/library/items?source=hanime1&category=裏番").json() == []
