"""SQLite 存储层：本地库、播放进度、设置、元数据缓存、订阅。

沿用 libre-tv 的表结构（library / progress / settings / meta_cache），
新增 subscriptions 与 subscription_seen 两张订阅表。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS library (
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

CREATE TABLE IF NOT EXISTS progress (
  key         TEXT PRIMARY KEY,
  position_ms INTEGER NOT NULL,
  duration_ms INTEGER,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_cache (
  key       TEXT PRIMARY KEY,
  payload   TEXT NOT NULL,
  cached_at TEXT NOT NULL,
  expires_at INTEGER
);

-- 订阅：按关键词或分区定期轮询源站，发现新条目即入队下载
CREATE TABLE IF NOT EXISTS subscriptions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source       TEXT NOT NULL,
  kind         TEXT NOT NULL,            -- query | genre
  value        TEXT NOT NULL,
  interval_sec INTEGER NOT NULL DEFAULT 3600,
  quality      TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  last_run_at  TEXT,
  created_at   TEXT NOT NULL
);

-- 订阅已见条目：保证同一个视频永远只入队一次
CREATE TABLE IF NOT EXISTS subscription_seen (
  sub_id        INTEGER NOT NULL,
  video_id      TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  PRIMARY KEY (sub_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_library_source ON library(source);
CREATE INDEX IF NOT EXISTS idx_seen_sub ON subscription_seen(sub_id);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Database:
    """单进程内共享的 SQLite 连接；所有写操作加锁串行化。"""

    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.media_dir = self.root / "media"
        (self.media_dir / "covers").mkdir(parents=True, exist_ok=True)
        (self.media_dir / ".cover-cache").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.root / "forager.db", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---- 读写 ----
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---- 设置 ----
    def get_setting(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ---- 路径安全 ----
    def media_path(self, name: str) -> Path:
        """把库内相对路径解析成绝对路径，禁止越界（目录穿越防护）。"""
        root = self.media_dir.resolve()
        target = (root / name).resolve()
        if target != root and root not in target.parents:
            raise ValueError("非法文件路径")
        return target


def ensure_dir(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
