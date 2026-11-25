"""结构升级：旧的 subscriptions / subscription_seen 必须无损迁入采集器与线索模型。"""

from __future__ import annotations

import sqlite3

import pytest

from forager.db import Database

#: 升级前的表结构（含历次加列），用来造一个旧库
LEGACY_SCHEMA = """
CREATE TABLE library (
  key           TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  video_id      TEXT NOT NULL,
  meta_json     TEXT NOT NULL,
  quality       TEXT NOT NULL,
  file_name     TEXT NOT NULL,
  cover_file    TEXT,
  size_bytes    INTEGER NOT NULL,
  downloaded_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE subscriptions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source       TEXT NOT NULL,
  kind         TEXT NOT NULL,
  value        TEXT NOT NULL,
  interval_sec INTEGER NOT NULL DEFAULT 3600,
  quality      TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  last_run_at  TEXT,
  created_at   TEXT NOT NULL,
  parent_id    INTEGER,
  title        TEXT,
  status       TEXT,
  last_error   TEXT
);
CREATE TABLE subscription_seen (
  sub_id        INTEGER NOT NULL,
  video_id      TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  PRIMARY KEY (sub_id, video_id)
);
CREATE INDEX idx_seen_sub ON subscription_seen(sub_id);
"""


def make_legacy_db(path, *, rename_first_table: bool = False) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.executemany(
        "INSERT INTO subscriptions "
        "(id, source, kind, value, interval_sec, quality, enabled, last_run_at, created_at, parent_id, title) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
        [
            (1, "hanime1", "query", "studio", 1800, None, "2026-11-20T00:00:00Z", "2026-11-01T00:00:00Z", None, "studio"),
            (2, "hanime1", "partition", "裏番", 86400, "1080", None, "2026-11-01T00:00:00Z", None, "裏番"),
            (3, "pixiv", "favorites", "", 43200, None, None, "2026-11-01T00:00:00Z", None, ""),
            (4, "pixiv", "uploader", "123", 43200, None, None, "2026-11-01T00:00:00Z", 3, "画师 A"),
        ],
    )
    conn.executemany(
        "INSERT INTO subscription_seen (sub_id, video_id, first_seen_at) VALUES (?, ?, ?)",
        [(2, "100", "2026-11-02T00:00:00Z"), (2, "101", "2026-11-02T00:00:00Z"), (4, "555", "2026-11-03T00:00:00Z")],
    )
    conn.execute(
        "INSERT INTO library (key, source, video_id, meta_json, quality, file_name, cover_file, "
        "size_bytes, downloaded_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        ("hanime1:100", "hanime1", "100", '{"title": "片一", "tags": ["裏番"]}', "1080", "a.mp4", 1, "2026-11-04T00:00:00Z"),
    )
    conn.commit()
    if rename_first_table:  # 模拟「只改完第一张表就断电」
        conn.execute("ALTER TABLE subscriptions RENAME TO clues")
        conn.commit()
    conn.close()


def tables(db: Database) -> set[str]:
    return {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}


def assert_migrated(db: Database) -> None:
    names = tables(db)
    assert {"fetchers", "clues", "clue_seen", "works"} <= names
    assert "subscriptions" not in names and "subscription_seen" not in names

    fetchers = [dict(r) for r in db.query("SELECT * FROM fetchers")]
    assert len(fetchers) == 1
    assert fetchers[0]["name"] == "默认采集器"
    assert fetchers[0]["trigger_type"] == "1h"  # 最小间隔 1800s → 每小时
    assert fetchers[0]["quality"] == "1080"  # 第一条非空清晰度
    assert fetchers[0]["last_run_at"] == "2026-11-20T00:00:00Z"

    clues = {r["id"]: dict(r) for r in db.query("SELECT * FROM clues")}
    assert {c["fetcher_id"] for c in clues.values()} == {fetchers[0]["id"]}
    assert (clues[1]["kind"], clues[1]["keyword"], clues[1]["category"]) == ("search", "studio", None)
    assert (clues[2]["kind"], clues[2]["category"], clues[2]["keyword"]) == ("search", "裏番", None)
    assert clues[2]["scanned"] == 1 and clues[1]["scanned"] == 0  # 有扫描记忆的才走增量
    assert clues[3]["kind"] == "favorites"
    assert (clues[4]["kind"], clues[4]["uploader"], clues[4]["parent_id"]) == ("search", "123", 3)
    assert all(c["pages"] == 1 for c in clues.values())

    seen = {(r["clue_id"], r["video_id"]) for r in db.query("SELECT * FROM clue_seen")}
    assert seen == {(2, "100"), (2, "101"), (4, "555")}

    # 只按 library 回填：已入库的记 done，旧 seen 里没下载的不进台账
    assert [dict(r) for r in db.query("SELECT source, video_id, state FROM works")] == [
        {"source": "hanime1", "video_id": "100", "state": "done"}
    ]

    # library.category 列补齐并回填
    assert [r["category"] for r in db.query("SELECT category FROM library")] == ["裏番"]


def test_legacy_db_is_migrated_in_one_piece(tmp_path) -> None:
    make_legacy_db(tmp_path / "forager.db")
    db = Database(tmp_path)
    try:
        assert_migrated(db)
        assert (tmp_path / "forager.db.bak").exists()  # 首次升级前留了备份
    finally:
        db.close()


def test_upgrade_is_idempotent(tmp_path) -> None:
    make_legacy_db(tmp_path / "forager.db")
    first = Database(tmp_path)
    assert_migrated(first)
    first.close()

    again = Database(tmp_path)
    try:
        assert_migrated(again)  # 二次启动不会再建采集器、不会重复回填
    finally:
        again.close()


def test_half_renamed_db_self_heals(tmp_path) -> None:
    make_legacy_db(tmp_path / "forager.db", rename_first_table=True)
    db = Database(tmp_path)
    try:
        assert_migrated(db)
    finally:
        db.close()


def test_fresh_database_has_no_legacy_tables(tmp_path) -> None:
    db = Database(tmp_path)
    try:
        names = tables(db)
        assert {"fetchers", "clues", "clue_seen", "works", "library"} <= names
        assert "subscriptions" not in names
        assert db.query("SELECT * FROM fetchers") == []
        assert not (tmp_path / "forager.db.bak").exists()
    finally:
        db.close()


def test_transaction_commits_and_rolls_back(tmp_path) -> None:
    db = Database(tmp_path)
    try:
        with db.transaction() as conn:
            conn.execute("INSERT INTO fetchers (name, trigger_type, created_at) VALUES ('a', '1d', 'now')")
        assert [r["name"] for r in db.query("SELECT name FROM fetchers")] == ["a"]

        with pytest.raises(RuntimeError, match="boom"):
            with db.transaction() as conn:
                conn.execute("INSERT INTO fetchers (name, trigger_type, created_at) VALUES ('b', '1d', 'now')")
                raise RuntimeError("boom")
        assert [r["name"] for r in db.query("SELECT name FROM fetchers")] == ["a"]
    finally:
        db.close()
