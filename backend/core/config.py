from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "HireMate API"
    jwt_secret: str = os.getenv("HIREMATE_JWT_SECRET", "hiremate-dev-secret-change-me")
    jwt_issuer: str = os.getenv("HIREMATE_JWT_ISSUER", "hiremate-api")
    access_cookie_name: str = "hm_access"
    refresh_cookie_name: str = "hm_refresh"
    csrf_cookie_name: str = "hm_csrf"
    access_token_minutes: int = int(os.getenv("HIREMATE_ACCESS_TOKEN_MINUTES", "15") or 15)
    refresh_token_days: int = int(os.getenv("HIREMATE_REFRESH_TOKEN_DAYS", "7") or 7)
    secure_cookies: bool = os.getenv("HIREMATE_SECURE_COOKIES", "1").strip().lower() not in {"0", "false", "no"}
    same_site: str = os.getenv("HIREMATE_COOKIE_SAMESITE", "lax")
    dev_origins_raw: str = os.getenv("HIREMATE_DEV_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    @property
    def dev_origins(self) -> list[str]:
        return [item.strip() for item in self.dev_origins_raw.split(",") if item.strip()]


settings = Settings()

