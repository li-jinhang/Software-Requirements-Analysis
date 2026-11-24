import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.schemas import Register, Login, UserOut, AuthOut
from app.core.security import hash_password, token, current_user
from app.db.database import get_db
from app.db.models import User, public

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: Register, db=Depends(get_db)):
    u = User(
        username=body.username, nickname=body.nickname, password_hash=hash_password(body.password)
    )
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名已存在")
    return public(u)


@router.post("/login", response_model=AuthOut)
def login(body: Login, db=Depends(get_db)):
    u = db.scalar(select(User).where(User.username == body.username))
    if u is None or not u.password_hash or not bcrypt.checkpw(body.password.encode(), u.password_hash.encode()):
        raise HTTPException(401, "账号或密码错误")
    return {"access_token": token(u), "user": public(u)}


@router.get("/me", response_model=UserOut)
def me(u=Depends(current_user)):
    return public(u)
