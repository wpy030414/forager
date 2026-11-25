"""SQLite 存储层：本地库、播放进度、设置、元数据缓存、采集器与线索。

沿用 libre-tv 的表结构（library / progress / settings / meta_cache），
线索部分是四张表：
- fetchers   采集器：若干线索的并集 + 触发时机
- clues      线索：一条发现规则（来源 + 分区 / 关键词 / 画师 + 数量）
- clue_seen  每条线索自己见过的作品 ID（「全部」的增量早停靠它）
- works      作品台账：全局按 (source, video_id) 去重，并跟踪入库状态

结构升级（含旧表 subscriptions / subscription_seen 的改名与回填）在同一个事务里完成，
中途断电不会留下半截库。
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

#: 必须在事务之外设置的 PRAGMA（journal_mode 在事务内改不了）
PRAGMAS = ("PRAGMA journal_mode = WAL", "PRAGMA foreign_keys = ON")

SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
  key           TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  video_id      TEXT NOT NULL,
  meta_json     TEXT NOT NULL,
  quality       TEXT NOT NULL,
  file_name     TEXT NOT NULL,
  cover_file    TEXT,
  size_bytes    INTEGER NOT NULL,
  downloaded_at TEXT NOT NULL,
  category      TEXT
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

-- 本地账号：单账号（CHECK id = 1），密码与恢复码只存 Argon2id 哈希
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  username      TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  recovery_hash TEXT,
  created_at    TEXT NOT NULL
);

-- 会话：只存 token 的 sha256；没有过期列，删行即失效
CREATE TABLE IF NOT EXISTS sessions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  user_id    INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

-- 采集器：一组线索的并集 + 触发时机（manual | 1h | 1d | 1w | 1m）+ 业务参数
CREATE TABLE IF NOT EXISTS fetchers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  trigger_type TEXT NOT NULL DEFAULT 'manual',
  quality      TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  last_run_at  TEXT,
  status       TEXT,
  last_error   TEXT,
  created_at   TEXT NOT NULL
);

-- 线索：一条发现规则。parent_id 非空表示由「我的收藏 / 我的关注」展开派生
CREATE TABLE IF NOT EXISTS clues (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  fetcher_id  INTEGER NOT NULL,
  parent_id   INTEGER,
  source      TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'search',   -- search | favorites | following
  category    TEXT,                             -- 分区
  keyword     TEXT,
  uploader    TEXT,                             -- 发布者 / 画师
  pages       INTEGER NOT NULL DEFAULT 1,       -- 每轮取前 N 页；0 = 全部
  title       TEXT NOT NULL DEFAULT '',
  scanned     INTEGER NOT NULL DEFAULT 0,       -- pages = 0 的线索是否已完成过整轮扫描
  enabled     INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT,
  status      TEXT,
  last_error  TEXT,
  created_at  TEXT NOT NULL
);

-- 线索记忆：保证同一条线索见过的作品不重复处理，也是「全部」增量早停的依据
CREATE TABLE IF NOT EXISTS clue_seen (
  clue_id       INTEGER NOT NULL,
  video_id      TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  PRIMARY KEY (clue_id, video_id)
);

-- 作品台账：全局按作品 ID 去重；state ∈ pending | done | cancelled | removed | failed
CREATE TABLE IF NOT EXISTS works (
  source        TEXT NOT NULL,
  video_id      TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  fetcher_id    INTEGER,
  clue_id       INTEGER,
  card_json     TEXT,
  quality       TEXT,
  state         TEXT NOT NULL DEFAULT 'pending',
  attempts      INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  updated_at    TEXT,
  PRIMARY KEY (source, video_id)
);

CREATE INDEX IF NOT EXISTS idx_library_source ON library(source);
CREATE INDEX IF NOT EXISTS idx_library_facet ON library(source, category);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_clues_fetcher ON clues(fetcher_id, id);
CREATE INDEX IF NOT EXISTS idx_works_state ON works(state);
"""

#: clues 的增量列：由旧表 subscriptions 升级来的库按这个顺序补齐
CLUE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("parent_id", "INTEGER"),
    ("title", "TEXT"),
    ("status", "TEXT"),
    ("last_error", "TEXT"),
    ("fetcher_id", "INTEGER"),
    ("category", "TEXT"),
    ("keyword", "TEXT"),
    ("uploader", "TEXT"),
    ("pages", "INTEGER NOT NULL DEFAULT 1"),
    ("scanned", "INTEGER NOT NULL DEFAULT 0"),
)

#: 旧线索（subscriptions 行）迁入一个「默认采集器」：参数按旧 kind 拆开，pages = 1
_LEGACY_BACKFILL = """
INSERT INTO fetchers (name, trigger_type, quality, enabled, last_run_at, created_at)
SELECT '默认采集器',
       CASE WHEN MIN(interval_sec) <= 3600 THEN '1h'
            WHEN MIN(interval_sec) <= 86400 THEN '1d'
            WHEN MIN(interval_sec) <= 604800 THEN '1w'
            ELSE '1m' END,
       (SELECT quality FROM clues
        WHERE fetcher_id IS NULL AND quality IS NOT NULL AND quality != ''
        ORDER BY id LIMIT 1),
       1,
       MAX(last_run_at),
       '{now}'
FROM clues
WHERE fetcher_id IS NULL
HAVING COUNT(*) > 0;

UPDATE clues SET
  fetcher_id = (SELECT MAX(id) FROM fetchers),
  kind = CASE WHEN kind IN ('uploader', 'query', 'partition') THEN 'search' ELSE kind END,
  keyword = CASE WHEN kind = 'query' THEN value END,
  category = CASE WHEN kind = 'partition' THEN value END,
  uploader = CASE WHEN kind = 'uploader' THEN value END,
  title = CASE WHEN title IS NULL OR title = '' THEN COALESCE(value, '') ELSE title END,
  pages = 1,
  scanned = CASE WHEN EXISTS (SELECT 1 FROM clue_seen cs WHERE cs.clue_id = clues.id)
                 THEN 1 ELSE 0 END
WHERE fetcher_id IS NULL;
"""

#: 已入库的作品进台账：之后再被任何线索发现都直接 skip。
#: 只按 library 回填——旧 clue_seen 分不清「见过没下」和「用户删过」
_WORKS_BACKFILL = """
INSERT OR IGNORE INTO works (source, video_id, first_seen_at, state, attempts, updated_at)
SELECT source, video_id, downloaded_at, 'done', 0, '{now}' FROM library;
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
        self.db_file = self.root / "forager.db"
        self.conn = sqlite3.connect(self.db_file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            for pragma in PRAGMAS:
                self.conn.execute(pragma)
            self._upgrade()
        self._backfill_categories()

    # ---- 结构升级 ----

    def _upgrade(self) -> None:
        """把库结构升到当前版本：改名、建表、加列、回填包在一个事务里。"""
        conn = self.conn
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        steps: list[str] = []

        # 1) 旧表改名：各自按结构判定，半途断电后重启能接着做完
        rename_clues = "subscriptions" in tables and "clues" not in tables
        rename_seen = "subscription_seen" in tables and "clue_seen" not in tables
        if rename_clues or rename_seen:
            self._backup_once()
        if rename_clues:
            steps.append("ALTER TABLE subscriptions RENAME TO clues;")
        if rename_seen:
            steps.append("ALTER TABLE subscription_seen RENAME TO clue_seen;")
        if "idx_seen_sub" in indexes:
            steps.append("DROP INDEX IF EXISTS idx_seen_sub;")

        # 2) 加列：改名的旧表与新表在这里拉齐（必须先于建索引，索引引用新列）
        clue_table = "subscriptions" if rename_clues else "clues"
        legacy_clues = clue_table in tables
        clue_cols = self._columns(clue_table) if legacy_clues else set()
        for column, ddl in CLUE_COLUMNS:
            if legacy_clues and column not in clue_cols:
                steps.append(f"ALTER TABLE clues ADD COLUMN {column} {ddl};")

        seen_table = "subscription_seen" if rename_seen else "clue_seen"
        if seen_table in tables:
            seen_cols = self._columns(seen_table)
            if "sub_id" in seen_cols and "clue_id" not in seen_cols:
                steps.append("ALTER TABLE clue_seen RENAME COLUMN sub_id TO clue_id;")

        if "library" in tables and "category" not in self._columns("library"):
            steps.append("ALTER TABLE library ADD COLUMN category TEXT;")

        # 3) 建缺失的表与索引
        steps.append(SCHEMA)

        # 4) 回填
        if {"value", "interval_sec"} <= clue_cols:
            steps.append(_LEGACY_BACKFILL.format(now=now_iso()))
        steps.append(_WORKS_BACKFILL.format(now=now_iso()))

        script = "BEGIN;\n" + "\n".join(steps) + "\nCOMMIT;"
        try:
            conn.executescript(script)
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _backup_once(self) -> None:
        """改表名是不可逆操作：首次升级前留一份库文件，给回滚留后路。"""
        backup = self.db_file.with_name(self.db_file.name + ".bak")
        if backup.exists():
            return
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(self.db_file, backup)

    def _backfill_categories(self) -> None:
        """旧记录的分区从 meta_json 里补：优先 category，退回 tname / tags[0]。"""
        import json as _json

        rows = self.query("SELECT key, meta_json FROM library WHERE category IS NULL")
        for row in rows:
            try:
                meta = _json.loads(row["meta_json"])
            except Exception:  # noqa: BLE001
                continue
            category = meta.get("category") or meta.get("tname")
            if not category:
                tags = meta.get("tags") or []
                category = tags[0] if tags else None
            if category:
                self.execute(
                    "UPDATE library SET category = ? WHERE key = ?", (str(category), row["key"])
                )

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

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """多步写入并成一个事务：块内直接用返回的连接执行，异常整体回滚。

        块内不要调用本类的 execute / executemany（它们会提前提交）。
        """
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self.conn.execute("ROLLBACK")
                raise

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
