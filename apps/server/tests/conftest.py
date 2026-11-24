"""测试夹具：统一给 TestClient 登录，避免每个用例重复处理鉴权。

账号由 lifespan 里的 bootstrap() 生成、只打印到日志；这里直接从库里改成一个
已知密码，再走真实的 /api/session/login 换取 Cookie——与线上路径一致。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from forager.auth import hash_secret
from forager.api.app import create_app

TEST_PASSWORD = "test-password-123"


def make_settings(tmp_path):
    """与各测试文件里的构造保持一致（放这里供新文件复用）。"""
    from forager.config import Settings

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


def login(client: TestClient, password: str = TEST_PASSWORD) -> None:
    res = client.post("/api/session/login", json={"username": "admin", "password": password})
    assert res.status_code == 200, res.text


@pytest.fixture
def app_client(tmp_path):
    """未登录的客户端：用于验证 401 与登录流程本身。"""
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        app.state.db.execute(
            "UPDATE users SET password_hash = ? WHERE id = 1", (hash_secret(TEST_PASSWORD),)
        )
        yield client


@pytest.fixture
def client(app_client):
    """已登录的客户端：大多数业务用例用这个。"""
    login(app_client)
    return app_client
