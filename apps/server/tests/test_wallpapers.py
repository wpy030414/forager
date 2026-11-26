"""背景图：仓库不存原图，首次运行落到本地库；接口按需返回索引与文件。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD, login, make_settings
from forager.api.app import create_app
from forager.auth import hash_secret
from forager.transport.curl import CurlError
from forager.wallpapers import CATALOG, WALLPAPER_DIR, WallpaperStore, warmup

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _app(tmp_path):
    app = create_app(make_settings(tmp_path))
    # bootstrap 只把初始密码写进日志：这里改成已知密码，再走真实登录
    with TestClient(app) as client:
        client.app.state.db.execute(
            "UPDATE users SET password_hash = ? WHERE id = 1", (hash_secret(TEST_PASSWORD),)
        )
    return app


def test_index_lists_catalog_with_weights(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        login(client)
        index = client.get("/api/wallpapers/index").json()
        assert [item["slug"] for item in index] == [w.slug for w in CATALOG]
        assert {item["weight"] for item in index} == {0}  # 一张都没落盘
        assert index[0]["url"] == f"/api/wallpapers/{CATALOG[0].slug}.jpg"


def test_file_404_until_downloaded(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        login(client)
        slug = CATALOG[0].slug
        assert client.get(f"/api/wallpapers/{slug}.jpg").status_code == 404
        assert client.get("/api/wallpapers/../forager.db").status_code == 404

        target = client.app.state.db.media_dir / WALLPAPER_DIR / f"{slug}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(JPEG)

        res = client.get(f"/api/wallpapers/{slug}.jpg")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.content == JPEG
        weights = {item["slug"]: item["weight"] for item in client.get("/api/wallpapers/index").json()}
        assert weights[slug] > 0 and weights[CATALOG[1].slug] == 0


def test_wallpapers_require_login(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/api/wallpapers/index").status_code == 401
        assert client.get(f"/api/wallpapers/{CATALOG[0].slug}.jpg").status_code == 401


async def test_warmup_skips_present_and_records_failures(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        store: WallpaperStore = client.app.state.wallpapers
        first, second = CATALOG[0], CATALOG[1]
        target = store.db.media_dir / first.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(JPEG)

        calls: list[str] = []

        async def fake_download(item, proxy):
            calls.append(item.slug)
            if item.slug == second.slug:
                raise CurlError("网断了")
            return item.path

        logs: list[str] = []
        # 首图已经落盘，不该再被拉；app 启动时的后台补拉可能在测试落盘前已经拉过一张，这里只看补拉列表本身
        store.download = fake_download  # type: ignore[method-assign]
        calls.clear()
        logs.clear()
        await warmup(store, None, logs.append)

        assert first.slug not in calls
        assert calls[: len(CATALOG) - 1] == [w.slug for w in CATALOG if w.slug != first.slug]
        assert any(second.slug in line and "失败" in line for line in logs)