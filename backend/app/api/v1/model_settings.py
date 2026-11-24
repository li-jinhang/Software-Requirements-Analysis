from ipaddress import ip_address
from time import monotonic
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.config import settings as app_settings
from app.core.security import current_user
from app.services import chatgpt_login, model_profiles as profiles
from app.services.model_connection import ModelConnection, catalog

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def require_local_settings(request: Request):
    origin = request.headers.get("origin")
    try:
        peer_is_local = request.client and ip_address(request.client.host).is_loopback
        host_is_local = urlsplit("//" + request.headers.get("host", "")).hostname in LOCAL_HOSTS
        origin_is_local = origin is None or (
            urlsplit(origin).hostname in LOCAL_HOSTS
            and (origin == str(request.base_url).rstrip("/") or origin in app_settings.cors_origins)
        )
        if peer_is_local and host_is_local and origin_is_local:
            return
    except ValueError:
        pass
    raise HTTPException(403, "请从运行服务的电脑打开本机工作台管理模型配置。")


router = APIRouter(
    prefix="/model-settings", tags=["模型设置"], dependencies=[Depends(require_local_settings)]
)


@router.get("")
def settings():
    return {**profiles.dashboard(), "chatgpt": chatgpt_login.status()}


@router.get("/export")
def export():
    return profiles.export_archive()


@router.post("/import")
def import_config(archive: profiles.ModelArchive):
    return profiles.import_archive(archive)


@router.post("/profiles", status_code=201)
def create(body: profiles.ProfileInput):
    return {"id": profiles.save_profile(body)}


@router.put("/profiles/{id}")
def edit(id: str, body: profiles.ProfileInput):
    return {"id": profiles.save_profile(body, id)}


@router.delete("/profiles/{id}", status_code=204)
def remove(id: str):
    profiles.delete_profile(id)


class Binding(BaseModel):
    profile_id: str | None


@router.put("/bindings/{purpose}", status_code=204)
def bind(purpose: profiles.Use, body: Binding):
    profiles.bind(purpose, body.profile_id)


@router.get("/profiles/{id}/models")
def models(id: str):
    return catalog(profiles.profile(id))


@router.post("/profiles/{id}/test")
def test(id: str):
    start = monotonic()
    ModelConnection(profiles.profile(id)).text("仅回复 OK。", "连接测试")
    return {"latency_ms": round((monotonic() - start) * 1000)}


@router.post("/chatgpt/login")
def login():
    return {"url": chatgpt_login.begin()}


@router.delete("/chatgpt/login", status_code=204)
def cancel():
    chatgpt_login.end_login()


@router.delete("/chatgpt/account", status_code=204)
def logout():
    chatgpt_login.end_login(logout=True)


public_router = APIRouter(prefix="/model-settings", tags=["模型设置"])


@public_router.get("/availability")
def availability():
    return {purpose: profiles.resolve(purpose) is not None for purpose in profiles.TEACHING_USES}


@public_router.get("/active")
def active(user=Depends(current_user)):
    result = {}
    for purpose in profiles.TEACHING_USES:
        p = profiles.resolve(purpose)
        result[purpose] = (p.get("name", "环境配置") + " · " + p["model"]) if p else "未配置模型"
    return result


class CatalogInput(profiles.ProfileInput):
    id: str | None = None
    model: str = "catalog"


@router.post("/catalog")
def preview_models(body: CatalogInput):
    key = body.api_key or ""
    if body.id and body.mode == "api" and body.api_key is None:
        old = profiles.profile(body.id)
        if old["base_url"] == body.base_url.rstrip("/"):
            key = old["api_key"]
    return catalog({"mode": body.mode, "base_url": body.base_url.rstrip("/"), "api_key": key})
