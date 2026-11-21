"""本地库路径安全与文件服务（移植自 libre-tv 的 library.test.ts 要点）。"""

from __future__ import annotations

import pytest

from forager.db import Database


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "data")
    yield database
    database.close()


def test_media_path_inside_root(db) -> None:
    assert db.media_path("covers/a.jpg") == db.media_dir / "covers" / "a.jpg"


@pytest.mark.parametrize("name", ["../escape.txt", "covers/../../escape.txt"])
def test_media_path_rejects_traversal(db, name: str) -> None:
    with pytest.raises(ValueError):
        db.media_path(name)


def test_settings_roundtrip(db) -> None:
    assert db.get_setting("mode.offline") is None
    db.set_setting("mode.offline", "1")
    assert db.get_setting("mode.offline") == "1"
