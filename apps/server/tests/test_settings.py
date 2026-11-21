"""设置与凭据加密：明文不得落库、接口不得回显。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from forager.api.app import create_app
from forager.config import Settings
from forager.db import Database
from forager.secrets import SecretBox, load_key, mask
from forager.source_config import SourceConfigStore
from forager.sources.bilibili import BilibiliSource
from forager.sources.hanime1 import Hanime1Source
from forager.transport.client import UpstreamClient

COOKIE = "SESSDATA=deadbeef1234567890; bili_jct=abcd"


def make_settings(tmp_path) -> Settings:
    return Settings(
        port=11408,
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
        hanime1_base_url="https://hanime1.me",
        hanime1_proxy=None,
        hanime1_dns_proxy=None,
        hanime1_pinned_ip="127.0.0.1",
        hanime1_cookie=None,
        min_interval_ms=0,
        html_cache_ttl_ms=0,
        download_concurrency=1,
        sub_tick_sec=3600,
        sub_initial=0,
    )


def test_secretbox_roundtrip_and_mask(tmp_path) -> None:
    box = SecretBox(load_key(tmp_path, None))
    blob = box.encrypt(COOKIE)
    assert blob.startswith("enc:v1:")
    assert COOKIE not in blob
    assert box.decrypt(blob) == COOKIE
    assert mask(COOKIE).endswith("abcd")
    assert mask("short") == "****"


def test_wrong_key_raises(tmp_path) -> None:
    blob = SecretBox(load_key(tmp_path / "a", None)).encrypt("x" * 20)
    other = SecretBox(load_key(tmp_path / "b", None))
    with pytest.raises(ValueError):
        other.decrypt(blob)


def test_store_encrypts_secret_and_masks(tmp_path) -> None:
    db = Database(tmp_path / "data")
    store = SourceConfigStore(db, SecretBox(load_key(tmp_path, None)))
    sources = {
        "bilibili": BilibiliSource(UpstreamClient(base_url="https://api.bilibili.com", user_agent="ua")),
    }
    store.save("bilibili", sources["bilibili"].config_fields, {"cookie": COOKIE, "min_interval_ms": "900"})

    raw = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM settings")}
    assert COOKIE not in raw["source.bilibili.cookie"]  # 落库是密文
    assert raw["source.bilibili.min_interval_ms"] == "900"

    values = store.values("bilibili", sources["bilibili"].config_fields)
    assert values["cookie"] == COOKIE
    assert values["min_interval_ms"] == 900

    described = store.describe(sources)[0]
    cookie_field = next(f for f in described["fields"] if f["key"] == "cookie")
    assert cookie_field["configured"] is True
    assert cookie_field["hint"].endswith("abcd")
    assert "value" not in cookie_field  # 秘密字段不回传明文
    db.close()


def test_save_applies_live(tmp_path) -> None:
    db = Database(tmp_path / "data")
    store = SourceConfigStore(db, SecretBox(load_key(tmp_path, None)))
    source = BilibiliSource(UpstreamClient(base_url="https://api.bilibili.com", user_agent="ua"))
    store.save("bilibili", source.config_fields, {"cookie": COOKIE, "min_interval_ms": "2000"})
    store.apply_all({"bilibili": source})
    assert source.client.cookie == COOKIE
    assert source.client.min_interval == 2.0
    db.close()


def test_api_never_returns_plaintext(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        listing = client.get("/api/settings/sources").json()
        assert {s["source"] for s in listing} == {"hanime1", "bilibili"}

        saved = client.put("/api/settings/sources/bilibili", json={"cookie": COOKIE})
        assert saved.status_code == 200
        assert COOKIE not in json.dumps(saved.json(), ensure_ascii=False)

        again = json.dumps(client.get("/api/settings/sources").json(), ensure_ascii=False)
        assert COOKIE not in again
        assert "abcd" in again  # 只回掩码

        cleared = client.put("/api/settings/sources/bilibili", json={"cookie": ""})
        assert cleared.status_code == 200
        field = next(
            f for f in cleared.json()["config"]["fields"] if f["key"] == "cookie"
        )
        assert field["configured"] is False


def test_unknown_source_and_field(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.put("/api/settings/sources/nope", json={}).status_code == 404
        # 未声明的字段被忽略，不报错也不落库
        assert client.put("/api/settings/sources/bilibili", json={"bogus": "x"}).status_code == 200
