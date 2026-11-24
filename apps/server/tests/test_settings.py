"""设置与凭据加密：明文不得落库、接口不得回显。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from forager.api.app import create_app
from forager.auth import hash_secret
from forager.config import Settings
from forager.db import Database
from forager.secrets import SecretBox, load_key, mask
from forager.source_config import SourceConfigStore
from forager.sources.hanime1 import Hanime1Source
from forager.transport.client import UpstreamClient

TEST_PASSWORD = "test-password-123"


def _login(client, password: str = TEST_PASSWORD) -> None:
    """lifespan 里 bootstrap 生成的密码只在日志里；测试直接改库再走真实登录接口。"""
    db = client.app.state.db
    db.execute("UPDATE users SET password_hash = ? WHERE id = 1", (hash_secret(password),))
    res = client.post("/api/session/login", json={"username": "admin", "password": password})
    assert res.status_code == 200, res.text


COOKIE = "cf_clearance=deadbeef1234567890abcd"


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
        "hanime1": Hanime1Source(UpstreamClient(base_url="https://hanime1.me", user_agent="ua")),
    }
    store.save("hanime1", sources["hanime1"].config_fields, {"cookie": COOKIE, "min_interval_ms": "900"})

    raw = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM settings")}
    assert COOKIE not in raw["source.hanime1.cookie"]  # 落库是密文
    assert raw["source.hanime1.min_interval_ms"] == "900"

    values = store.values("hanime1", sources["hanime1"].config_fields)
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
    source = Hanime1Source(UpstreamClient(base_url="https://hanime1.me", user_agent="ua"))
    store.save("hanime1", source.config_fields, {"cookie": COOKIE, "min_interval_ms": "2000"})
    store.apply_all({"hanime1": source})
    assert source.client.cookie == COOKIE
    assert source.client.min_interval == 2.0
    db.close()


def test_api_never_returns_plaintext(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        _login(client)
        listing = client.get("/api/settings/sources").json()
        assert {s["source"] for s in listing} == {"hanime1"}

        saved = client.put("/api/settings/sources/hanime1", json={"cookie": COOKIE})
        assert saved.status_code == 200
        assert COOKIE not in json.dumps(saved.json(), ensure_ascii=False)

        again = json.dumps(client.get("/api/settings/sources").json(), ensure_ascii=False)
        assert COOKIE not in again
        assert "abcd" in again  # 只回掩码

        cleared = client.put("/api/settings/sources/hanime1", json={"cookie": ""})
        assert cleared.status_code == 200
        field = next(
            f for f in cleared.json()["config"]["fields"] if f["key"] == "cookie"
        )
        assert field["configured"] is False


def test_unknown_source_and_field(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        _login(client)
        assert client.put("/api/settings/sources/nope", json={}).status_code == 404
        # 未声明的字段被忽略，不报错也不落库
        assert client.put("/api/settings/sources/hanime1", json={"bogus": "x"}).status_code == 200
