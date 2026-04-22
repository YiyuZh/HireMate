from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.schemas import (
    BatchSummaryResponse,
    JobDetailResponse,
    JobSummaryResponse,
    JobUpdateRequest,
    JobWriteRequest,
    MessageResponse,
)
from backend.api.viewmodels import build_batch_summary, build_job_detail, build_job_summary
from backend.core.deps import get_current_user, verify_csrf
from backend.services import job_service


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobSummaryResponse])
def list_jobs(user: dict = Depends(get_current_user)) -> list[JobSummaryResponse]:
    return [build_job_summary(item) for item in job_service.list_jobs()]


@router.post("", dependencies=[Depends(verify_csrf)], response_model=JobDetailResponse)
def create_job(payload: JobWriteRequest, user: dict = Depends(get_current_user)) -> JobDetailResponse:
    return build_job_detail(job_service.create_job(payload.model_dump(), operator=user))


@router.get("/{jd_title:path}/batches", response_model=list[BatchSummaryResponse])
def list_job_batches(jd_title: str, user: dict = Depends(get_current_user)) -> list[BatchSummaryResponse]:
    detail = job_service.get_job_detail(jd_title)
    return [build_batch_summary(item) for item in (detail.get("batches") or [])]


@router.get("/{jd_title:path}", response_model=JobDetailResponse)
def get_job(jd_title: str, user: dict = Depends(get_current_user)) -> JobDetailResponse:
    return build_job_detail(job_service.get_job_detail(jd_title))


@router.put("/{jd_title:path}", dependencies=[Depends(verify_csrf)], response_model=JobDetailResponse)
def update_job(jd_title: str, payload: JobUpdateRequest, user: dict = Depends(get_current_user)) -> JobDetailResponse:
    return build_job_detail(job_service.update_job_detail(jd_title, payload.model_dump(), operator=user))


@router.delete("/{jd_title:path}", dependencies=[Depends(verify_csrf)], response_model=MessageResponse)
def delete_job(jd_title: str, user: dict = Depends(get_current_user)) -> MessageResponse:
    job_service.delete_job_detail(jd_title)
    return MessageResponse(ok=True, message="Job deleted")
