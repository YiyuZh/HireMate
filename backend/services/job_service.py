from __future__ import annotations

from typing import Any

from src.candidate_store import list_batches_by_jd, load_latest_batch_by_jd
from src.jd_store import (
    delete_jd,
    list_jd_records,
    load_jd,
    load_jd_scoring_config,
    save_jd,
    update_jd,
    upsert_jd_scoring_config,
)


def list_jobs() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in list_jd_records():
        title = str(item.get("title") or "").strip()
        latest_batch = load_latest_batch_by_jd(title) if title else None
        batch_summary = {
            "batch_id": str((latest_batch or {}).get("batch_id") or ""),
            "created_at": str((latest_batch or {}).get("created_at") or ""),
            "pass_count": int((latest_batch or {}).get("pass_count") or 0),
            "review_count": int((latest_batch or {}).get("review_count") or 0),
            "reject_count": int((latest_batch or {}).get("reject_count") or 0),
            "total_resumes": int((latest_batch or {}).get("total_resumes") or 0),
        }
        results.append(
            {
                "title": title,
                "jd_text": str(item.get("text") or ""),
                "openings": int(item.get("openings", 0) or 0),
                "created_by_name": str(item.get("created_by_name") or ""),
                "created_by_email": str(item.get("created_by_email") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "scoring_config": load_jd_scoring_config(title),
                "latest_batch": batch_summary,
            }
        )
    return results


def get_job_detail(title: str) -> dict[str, Any]:
    clean_title = str(title or "").strip()
    record = next((item for item in list_jd_records() if str(item.get("title") or "").strip() == clean_title), {})
    jd_text = load_jd(clean_title)
    return {
        "title": clean_title,
        "jd_text": jd_text,
        "openings": int(record.get("openings", 0) or 0),
        "scoring_config": load_jd_scoring_config(clean_title),
        "batches": list_batches_by_jd(clean_title),
    }


def create_job(payload: dict[str, Any], *, operator: dict[str, Any]) -> dict[str, Any]:
    save_jd(
        payload["title"],
        payload["jd_text"],
        openings=int(payload.get("openings", 0) or 0),
        created_by_user_id=operator["user_id"],
        created_by_name=operator["name"],
        created_by_email=operator["email"],
        updated_by_user_id=operator["user_id"],
        updated_by_name=operator["name"],
        updated_by_email=operator["email"],
    )
    if isinstance(payload.get("scoring_config"), dict):
        upsert_jd_scoring_config(payload["title"], payload["scoring_config"])
    return get_job_detail(payload["title"])


def update_job_detail(title: str, payload: dict[str, Any], *, operator: dict[str, Any]) -> dict[str, Any]:
    update_jd(
        title,
        payload["jd_text"],
        openings=payload.get("openings"),
        updated_by_user_id=operator["user_id"],
        updated_by_name=operator["name"],
        updated_by_email=operator["email"],
    )
    if isinstance(payload.get("scoring_config"), dict):
        upsert_jd_scoring_config(title, payload["scoring_config"])
    return get_job_detail(title)


def delete_job_detail(title: str) -> None:
    delete_jd(title)
