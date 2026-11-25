"""作品台账：全局按 (source, video_id) 去重，并跟踪每个作品的入库状态。

线索发现作品时先入账再入队；之后无论被哪条线索、哪个采集器再次发现，都直接 skip。

状态机：
  pending   已发现、尚未入库（含「失败待补投」）
  done      已入库
  cancelled 用户在下载面板取消了它
  removed   用户删除了库里的条目
  failed    失败次数耗尽

attempts 记的是失败次数而不是入队次数：重启后内存队列是空的，
未完成的作品必须能无惩罚地重新入队。
"""

from __future__ import annotations

import json
import sqlite3

from ..db import Database, now_iso

#: 失败几次之后不再自动补投
MAX_ATTEMPTS = 3


def work_key(source: str, video_id: str) -> str:
    return f"{source}:{video_id}"


def card_title(card_json: str | None) -> str:
    """列表页卡片摘要里的标题（补投时给下载任务占位用）。"""
    try:
        return str(json.loads(card_json or "{}").get("title") or "")
    except (TypeError, ValueError):
        return ""


class Ledger:
    def __init__(self, db: Database) -> None:
        self.db = db

    def keys(self) -> set[str]:
        """台账里全部作品的键（source:video_id）；发现时用它判断是否该 skip。"""
        return {
            work_key(r["source"], r["video_id"])
            for r in self.db.query("SELECT source, video_id FROM works")
        }

    def settle(self, key: str, outcome: str, error: str | None = None) -> None:
        """下载任务到达终态：把结果写回台账。不在台账里的（手动下载）静默忽略。"""
        source, _, video_id = key.partition(":")
        stamp = now_iso()
        if outcome == "done":
            self.db.execute(
                "UPDATE works SET state = 'done', last_error = NULL, updated_at = ? "
                "WHERE source = ? AND video_id = ?",
                (stamp, source, video_id),
            )
        elif outcome == "cancelled":
            self.db.execute(
                "UPDATE works SET state = 'cancelled', last_error = ?, updated_at = ? "
                "WHERE source = ? AND video_id = ? AND state = 'pending'",
                (error, stamp, source, video_id),
            )
        elif outcome == "failed":
            # 失败次数 +1；耗尽才落 failed，否则留 pending 等下一轮补投。
            # 只动 pending：用户删过、已入库的作品不因迟到的失败回调改状态。
            self.db.execute(
                "UPDATE works SET attempts = attempts + 1, last_error = ?, updated_at = ?, "
                "state = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'pending' END "
                "WHERE source = ? AND video_id = ? AND state = 'pending'",
                (error, stamp, MAX_ATTEMPTS, source, video_id),
            )

    def mark_removed(self, key: str) -> None:
        source, _, video_id = key.partition(":")
        self.db.execute(
            "UPDATE works SET state = 'removed', updated_at = ? WHERE source = ? AND video_id = ?",
            (now_iso(), source, video_id),
        )

    def due(self) -> list[sqlite3.Row]:
        """待补投：已发现未入库、失败次数未耗尽。"""
        return self.db.query(
            "SELECT source, video_id, card_json, quality FROM works "
            "WHERE state = 'pending' AND attempts < ? ORDER BY first_seen_at",
            (MAX_ATTEMPTS,),
        )
