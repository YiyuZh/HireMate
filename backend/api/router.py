from __future__ import annotations

from fastapi import APIRouter

from backend.api.routes import admin, auth, jobs, screening, workbench

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(jobs.router, tags=["jobs"])
api_router.include_router(screening.router, tags=["screening"])
api_router.include_router(workbench.router, tags=["workbench"])
api_router.include_router(admin.router, tags=["admin"])
