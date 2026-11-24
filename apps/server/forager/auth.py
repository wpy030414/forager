"""本地账号认证：Argon2id 密码哈希 + 恢复码 + 永不过期的会话 Cookie。

- 单账号（users 表 CHECK id = 1），无邮箱、无找回；忘记密码只能靠恢复码，
  恢复码也只存哈希，且只在首启日志里出现一次。
- 会话不存过期时间：只要 sessions 里还有那一行就有效；退出即删行。
- 库里只存 token 的 sha256，Cookie 本身泄露后也无法反推库里的值。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request

from .db import Database, now_iso

COOKIE_NAME = "forager_session"
_hasher = PasswordHasher()
log = logging.getLogger("forager.auth")


def hash_secret(raw: str) -> str:
    return _hasher.hash(raw)


def verify_secret(raw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_recovery_code() -> str:
    """恢复码：24 个字符，分四组便于抄写。"""
    raw = secrets.token_hex(12)
    return "-".join(raw[i : i + 6] for i in range(0, len(raw), 6)).upper()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Database, user_id: int = 1) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at) VALUES (?, ?, ?)",
        (_token_hash(token), user_id, now_iso()),
    )
    return token


def user_for_token(db: Database, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    row = db.query_one(
        "SELECT u.id AS id, u.username AS username FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (_token_hash(token),),
    )
    return dict(row) if row else None


def revoke_token(db: Database, token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def revoke_all(db: Database) -> None:
    db.execute("DELETE FROM sessions")


def current_user(request: Request) -> dict[str, Any] | None:
    return user_for_token(request.app.state.db, request.cookies.get(COOKIE_NAME))


def require_auth(request: Request) -> dict[str, Any]:
    """FastAPI 依赖：所有 /api/* 业务路由的守门人。"""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="需要登录")
    request.state.user = user
    return user


def bootstrap(db: Database) -> None:
    """首次启动建账号，初始密码与恢复码只打印一次。"""
    if db.query_one("SELECT id FROM users WHERE id = 1") is not None:
        return
    password = secrets.token_urlsafe(12)
    recovery = new_recovery_code()
    db.execute(
        "INSERT INTO users (id, username, password_hash, recovery_hash, created_at) "
        "VALUES (1, ?, ?, ?, ?)",
        ("admin", hash_secret(password), hash_secret(recovery), now_iso()),
    )
    log.warning(
        "首次启动：已生成管理员账号\n"
        "  用户名：admin\n"
        "  初始密码：%s\n"
        "  恢复码：%s\n"
        "请登录后在设置页修改密码，并妥善保管恢复码（服务端不再显示）。",
        password,
        recovery,
    )
