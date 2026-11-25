import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.schemas import Register, Login, UserOut, AuthOut
from app.core.config import settings
from app.core.security import hash_password, token, current_user
from app.db.database import get_db
from app.db.models import DemoWorkspace, User, next_account_id, public

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: Register, db=Depends(get_db)):
    password_hash = hash_password(body.password)
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    u = User(
        id=next_account_id(db), username=body.username, nickname=body.nickname, password_hash=password_hash,
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


class CurrentUserOut(UserOut):
    demo_build: str | None = None


@router.get("/me", response_model=CurrentUserOut, response_model_exclude_none=True)
def me(u=Depends(current_user), db=Depends(get_db)):
    profile = public(u)
    if settings.seed_demo:
        demo = db.get(DemoWorkspace, 1)
        if demo and demo.user_id == u.id:
            profile["demo_build"] = demo.build_id
    return profile
