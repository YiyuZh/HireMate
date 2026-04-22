from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from backend.api.schemas import AuthResponse, LoginRequest, UserOut
from backend.core.deps import get_current_user, get_refresh_payload, verify_csrf
from backend.core.security import clear_auth_cookies, set_auth_cookies
from backend.services import auth_service


router = APIRouter(prefix="/auth", tags=["auth"])


def _to_user_out(user: dict) -> UserOut:
    return UserOut(
        user_id=str(user.get("user_id") or ""),
        email=str(user.get("email") or ""),
        name=str(user.get("name") or ""),
        is_active=bool(user.get("is_active")),
        is_admin=bool(user.get("is_admin")),
        created_at=str(user.get("created_at") or ""),
        updated_at=str(user.get("updated_at") or ""),
        last_login_at=str(user.get("last_login_at") or ""),
    )


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, response: Response) -> AuthResponse:
    user, tokens = auth_service.login(payload.email, payload.password)
    set_auth_cookies(response, tokens)
    return AuthResponse(user=_to_user_out(user), csrf_token=str(tokens.get("csrf_token") or ""))


@router.post("/refresh", response_model=AuthResponse)
def refresh(response: Response, refresh_payload: dict = Depends(get_refresh_payload)) -> AuthResponse:
    user, tokens = auth_service.refresh_from_payload(refresh_payload)
    set_auth_cookies(response, tokens)
    return AuthResponse(user=_to_user_out(user), csrf_token=str(tokens.get("csrf_token") or ""))


@router.post("/logout")
def logout(response: Response, _: None = Depends(verify_csrf)) -> dict[str, bool]:
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)) -> UserOut:
    return _to_user_out(user)
