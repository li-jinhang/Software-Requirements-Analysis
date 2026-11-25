"""Select and migrate accounts in the workbench on this computer."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.background import BackgroundTask

from app.core.security import current_user, require_local_workbench, token
from app.db.database import get_db
from app.db.models import Document, RetiredAccountId, User, public
from app.schemas import AuthOut, UserOut
from app.services import account_backup as backup
from app.services.memory_service import delete_documents


router = APIRouter(
    prefix="/local-accounts", tags=["本机账号"], dependencies=[Depends(require_local_workbench)],
)


class AccountName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=64)


class AccountAccess(BaseModel):
    password: str = Field(default="", max_length=72)


class AccountDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(max_length=32)


class LocalAccount(UserOut):
    protected: bool


def session(user):
    return {"access_token": token(user), "user": public(user)}


@router.get("", response_model=list[LocalAccount])
def accounts(db=Depends(get_db)):
    return [
        {**public(user), "protected": bool(user.password_hash) or user.role == "admin"}
        for user in db.scalars(select(User).order_by(User.id))
    ]


@router.post("", status_code=201, response_model=AuthOut)
def create(body: AccountName, db=Depends(get_db)):
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    try:
        user = backup.new_account(db, body.name)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return session(user)


@router.post("/{id}/activate", response_model=AuthOut)
def activate(id: int, body: AccountAccess, db=Depends(get_db)):
    user = db.get(User, id)
    if not user:
        raise HTTPException(404, "账号不存在。")
    if user.password_hash or user.role == "admin":
        password = body.password.encode("utf-8")
        if not user.password_hash or len(password) > 72 or not bcrypt.checkpw(password, user.password_hash.encode()):
            raise HTTPException(401, "请输入该账号的正确密码。")
    return session(user)


@router.get("/export")
def export(db=Depends(get_db), user=Depends(current_user)):
    with NamedTemporaryFile(suffix=".zip", delete=False) as file:
        path = Path(file.name)
    try:
        backup.export_account(db, user, path)
    except (ValueError, OSError) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, "账号无法导出，请检查资料文件和备份大小。") from exc
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path, filename=f"{user.nickname}.zip", media_type="application/zip",
        headers={"Cache-Control": "no-store"}, background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.delete("/current", status_code=204)
def remove_current(body: AccountDeletion, db=Depends(get_db), user=Depends(current_user)):
    if body.confirmation != "确认删除":
        raise HTTPException(422, "请输入完整的「确认删除」。")
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    if db.get(User, user.id, populate_existing=True) is None:
        raise HTTPException(401, "请重新登录。")
    docs = list(db.scalars(select(Document).where(Document.user_id == user.id)))
    if any(doc.parse_status == "processing" for doc in docs):
        raise HTTPException(409, "还有资料正在解析，请解析完成后再删除账号。")
    db.add(RetiredAccountId(id=user.id))
    db.delete(user)
    delete_documents(db, docs)


@router.post("/import", status_code=201, response_model=AuthOut)
def import_backup(file: UploadFile, db=Depends(get_db)):
    if file.size is not None and file.size > backup.MAX_BACKUP:
        raise HTTPException(413, "账号备份超过 512 MB。")
    try:
        user = backup.import_account(db, file.file)
    except (BadZipFile, ValueError, KeyError, OSError, RuntimeError, IntegrityError) as exc:
        raise HTTPException(422, "无法导入，请选择有效的本机账号备份 ZIP 文件。") from exc
    return session(user)
