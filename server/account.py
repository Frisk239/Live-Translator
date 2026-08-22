from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class AccountIn(BaseModel):
    email: str
    password: str
    rememberMe: bool = False


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return salt, digest


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.accounts = {}
    app.state.tokens = {}

    @app.post("/account/register")
    def register(body: AccountIn):
        email = _normalize_email(body.email)
        if not email or not body.password:
            return JSONResponse({"error": "填邮箱和密码"}, status_code=400)
        if email in app.state.accounts:
            return JSONResponse({"error": "这个邮箱已经有账号，去登录"}, status_code=409)
        salt, digest = _hash_password(body.password)
        app.state.accounts[email] = {"salt": salt, "digest": digest}
        token = secrets.token_urlsafe(32)
        app.state.tokens[token] = email
        return {"email": email, "token": token}

    @app.post("/account/login")
    def login(body: AccountIn):
        email = _normalize_email(body.email)
        row = app.state.accounts.get(email)
        if not row:
            return JSONResponse({"error": "邮箱或密码不对"}, status_code=401)
        _, digest = _hash_password(body.password, row["salt"])
        if not hmac.compare_digest(digest, row["digest"]):
            return JSONResponse({"error": "邮箱或密码不对"}, status_code=401)
        token = secrets.token_urlsafe(32)
        app.state.tokens[token] = email
        return {"email": email, "token": token}

    return app


app = create_app()
