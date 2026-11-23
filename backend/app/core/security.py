from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import os
import secrets
import tempfile

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings
from app.db.database import get_db
from app.db.models import User


@lru_cache
def signing_key():
    configured = settings.jwt_secret.get_secret_value()
    if configured:
        return configured
    path = settings.data_dir / ".jwt-secret"
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(secrets.token_hex(32).encode())
        try:
            os.link(tmp.name, path)
        except FileExistsError:
            pass
        finally:
            Path(tmp.name).unlink()
    return path.read_text().strip()


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def token(user):
    return jwt.encode(
        {"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(hours=24)},
        signing_key(),
        algorithm="HS256",
    )


def current_user(
    auth: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    db=Depends(get_db),
):
    try:
        claims = jwt.decode(
            auth.credentials if auth else "",
            signing_key(),
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
        user = db.get(User, int(claims["sub"]))
    except (jwt.InvalidTokenError, ValueError):
        user = None
    if not user:
        raise HTTPException(401, "请重新登录", headers={"WWW-Authenticate": "Bearer"})
    return user


def admin(user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user
