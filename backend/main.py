from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.router import api_router
from backend.core.config import settings


app = FastAPI(title="HireMate API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.dev_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "HireMate API", "status": "ok"}
