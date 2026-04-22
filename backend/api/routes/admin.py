from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.schemas import (
    AdminFlagRequest,
    AdminPasswordResetRequest,
    AdminSystemHealthResponse,
    AdminUserCreateRequest,
    AdminUserRowResponse,
    MessageResponse,
)
from backend.api.viewmodels import build_admin_health, build_admin_user_row
from backend.core.deps import get_current_user, require_admin, verify_csrf
from backend.services import admin_service


router = APIRouter(tags=["admin"])


@router.get("/health")
def health() -> dict:
    return {"ok": True, **admin_service.get_system_health()}


@router.get("/admin/system-health", response_model=AdminSystemHealthResponse)
def admin_system_health(user: dict = Depends(get_current_user)) -> AdminSystemHealthResponse:
    require_admin(user)
    return build_admin_health(admin_service.get_system_health())


@router.get("/admin/users", response_model=list[AdminUserRowResponse])
def admin_users(user: dict = Depends(get_current_user)) -> list[AdminUserRowResponse]:
    require_admin(user)
    return [build_admin_user_row(item) for item in admin_service.get_admin_users()]


@router.post("/admin/users", dependencies=[Depends(verify_csrf)], response_model=AdminUserRowResponse)
def admin_create_user(payload: AdminUserCreateRequest, user: dict = Depends(get_current_user)) -> AdminUserRowResponse:
    require_admin(user)
    created = admin_service.create_admin_user(
        email=payload.email,
        name=payload.name,
        password=payload.password,
        is_admin=payload.is_admin,
    )
    return build_admin_user_row(created)


@router.post("/admin/users/{user_id}/reset-password", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def admin_reset_password(user_id: str, payload: AdminPasswordResetRequest, user: dict = Depends(get_current_user)) -> MessageResponse:
    require_admin(user)
    ok = admin_service.reset_admin_user_password(user_id=user_id, new_password=payload.new_password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return MessageResponse(ok=True, message="Password reset")


@router.post("/admin/users/{user_id}/active", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def admin_set_active(user_id: str, payload: AdminFlagRequest, user: dict = Depends(get_current_user)) -> MessageResponse:
    require_admin(user)
    try:
        ok = admin_service.set_admin_user_active(user_id=user_id, is_active=payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return MessageResponse(ok=True, message="Account status updated")


@router.post("/admin/users/{user_id}/admin", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def admin_set_admin(user_id: str, payload: AdminFlagRequest, user: dict = Depends(get_current_user)) -> MessageResponse:
    require_admin(user)
    try:
        ok = admin_service.set_admin_user_admin(user_id=user_id, is_admin=payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return MessageResponse(ok=True, message="Admin role updated")
