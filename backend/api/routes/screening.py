from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from backend.api.schemas import BatchCreateResponse, BatchSummaryResponse, PrecheckItemResponse, RuntimeConnectionRequest
from backend.api.viewmodels import build_batch_create_response, build_batch_summary, build_precheck_item
from backend.core.deps import get_current_user, verify_csrf
from backend.services import screening_service
from backend.services.ai_review_service import test_runtime_connection


router = APIRouter(tags=["screening"])


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    results: list[tuple[str, bytes]] = []
    for upload in files:
        results.append((str(upload.filename or "upload.bin"), await upload.read()))
    return results


@router.post("/screening/precheck", dependencies=[Depends(verify_csrf)], response_model=list[PrecheckItemResponse])
async def screening_precheck(
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
) -> list[PrecheckItemResponse]:
    uploads = await _read_uploads(files)
    return [build_precheck_item(item) for item in screening_service.preview_files(uploads)]


@router.post("/screening/ai/test-connection", dependencies=[Depends(verify_csrf)])
def screening_ai_test_connection(payload: RuntimeConnectionRequest, user: dict = Depends(get_current_user)) -> dict:
    return test_runtime_connection(payload.runtime_config, purpose=payload.purpose or "batch_runtime")


@router.post("/batches", dependencies=[Depends(verify_csrf)], response_model=BatchCreateResponse)
async def create_batch(
    jd_title: str = Form(...),
    jd_text: str = Form(...),
    runtime_config_json: str = Form(default="{}"),
    force_allow_weak: bool = Form(default=False),
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
) -> BatchCreateResponse:
    uploads = await _read_uploads(files)
    try:
        runtime_cfg = json.loads(runtime_config_json or "{}")
    except json.JSONDecodeError as exc:  # noqa: PERF203
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid runtime_config_json") from exc
    payload = screening_service.create_batch(
        jd_title=jd_title,
        jd_text=jd_text,
        files=uploads,
        operator=user,
        batch_ai_runtime_cfg=runtime_cfg,
        force_allow_weak=force_allow_weak,
    )
    return build_batch_create_response(payload)


@router.get("/batches/{batch_id}", response_model=BatchSummaryResponse)
def get_batch(batch_id: str, user: dict = Depends(get_current_user)) -> BatchSummaryResponse:
    payload = screening_service.get_batch(batch_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return build_batch_summary(payload)


@router.delete("/batches/{batch_id}", dependencies=[Depends(verify_csrf)])
def delete_batch(batch_id: str, user: dict = Depends(get_current_user)) -> dict[str, bool]:
    ok = screening_service.remove_batch(batch_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return {"ok": True}


@router.post("/batches/{batch_id}/ai/test-connection", dependencies=[Depends(verify_csrf)])
def batch_ai_test_connection(
    batch_id: str,
    payload: RuntimeConnectionRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    return test_runtime_connection(payload.runtime_config, purpose=payload.purpose or "batch_runtime")
