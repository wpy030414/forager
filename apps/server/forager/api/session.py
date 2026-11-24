"""应用自身的登录：/api/session/*

与 api/source_auth.py（上游源站的扫码登录）无关，这里管的是「谁能进 forager」。
会话 Cookie 永不过期，只有主动退出 / 重设密码 / 删库里的行才会失效。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (
    COOKIE_NAME,
    create_session,
    current_user,
    hash_secret,
    require_auth,
    revoke_all,
    revoke_token,
    verify_secret,
)

router = APIRouter(prefix="/api/session", tags=["session"])

#: 一年；服务端不记录过期，这个值只是让浏览器别早早丢掉
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    current: str
    next: str


class RecoverBody(BaseModel):
    recoveryCode: str
    nextPassword: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _user_row(request: Request) -> Any:
    row = request.app.state.db.query_one("SELECT * FROM users WHERE id = 1")
    if row is None:
        raise HTTPException(status_code=500, detail="账号未初始化")
    return row


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    user = current_user(request)
    return {"authenticated": user is not None, "username": user["username"] if user else None}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    row = _user_row(request)
    ok = body.username == row["username"] and verify_secret(body.password, row["password_hash"])
    if not ok:
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    token = create_session(request.app.state.db, row["id"])
    _set_cookie(response, token)
    return {"authenticated": True, "username": row["username"]}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    revoke_token(request.app.state.db, request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    body: PasswordBody, request: Request, response: Response
) -> dict[str, bool]:
    user = require_auth(request)
    row = _user_row(request)
    if not verify_secret(body.current, row["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if len(body.next) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    request.app.state.db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (hash_secret(body.next), row["id"])
    )
    # 改密后踢掉所有其它会话，只保留当前这台
    revoke_all(request.app.state.db)
    token = create_session(request.app.state.db, user["id"])
    _set_cookie(response, token)
    return {"ok": True}


@router.post("/recover")
async def recover(body: RecoverBody, request: Request, response: Response) -> dict[str, bool]:
    row = _user_row(request)
    if not verify_secret(body.recoveryCode.strip().upper(), row["recovery_hash"]):
        raise HTTPException(status_code=401, detail="恢复码不正确")
    if len(body.nextPassword) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    db = request.app.state.db
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_secret(body.nextPassword), row["id"]),
    )
    revoke_all(db)
    token = create_session(db, row["id"])
    _set_cookie(response, token)
    return {"ok": True}
