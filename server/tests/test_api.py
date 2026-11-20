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
        assert [s["id"] for s in client.get("/api/sources").json()] == ["hanime1", "bilibili"]
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
        assert [g["source"] for g in tree["sources"]] == ["hanime1", "bilibili"]
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


def test_library_facets_and_filters(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/library/facets").json()["total"] == 0
        assert client.get("/api/library/items?source=bilibili&category=动画").json() == []
