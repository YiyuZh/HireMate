from __future__ import annotations

from typing import Any

from .chunker import build_chunks_from_evidence, build_chunks_from_jd, build_chunks_from_resume
from .indexer import index_documents


def _normalize_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_chunks_from_runtime_context(
    *,
    parsed_jd: dict[str, Any] | None = None,
    parsed_resume: dict[str, Any] | None = None,
    resume_text: str = "",
    score_details: dict[str, Any] | None = None,
    evidence_snippets: list[dict[str, Any]] | None = None,
    screening_reasons: list[str] | None = None,
    candidate_id: str = "",
    job_id: str = "",
) -> list[dict[str, Any]]:
    jd_payload = _normalize_payload(parsed_jd)
    resume_payload = _normalize_payload(parsed_resume)
    chunks: list[dict[str, Any]] = []

    if jd_payload:
        chunks.extend(build_chunks_from_jd(jd_payload.get("raw_jd_text") or "", jd_payload, job_id=job_id))
    if resume_payload:
        effective_resume_text = (
            resume_text
            or str(resume_payload.get("normalized_resume_text") or "")
            or str(resume_payload.get("raw_resume_text") or "")
        )
        chunks.extend(
            build_chunks_from_resume(
                effective_resume_text,
                resume_payload,
                parsed_jd=jd_payload,
                candidate_id=candidate_id,
                job_id=job_id,
            )
        )
    if score_details or evidence_snippets or screening_reasons:
        chunks.extend(
            build_chunks_from_evidence(
                evidence_snippets=evidence_snippets,
                score_details=score_details,
                screening_reasons=screening_reasons,
                parsed_jd=jd_payload,
                candidate_id=candidate_id,
                job_id=job_id,
            )
        )
    return chunks


def build_chunks_from_review_record(review_record: dict[str, Any]) -> list[dict[str, Any]]:
    record = _normalize_payload(review_record)
    jd_title = str(record.get("jd_title") or "").strip()
    resume_name = str(record.get("resume_name") or record.get("resume_file") or "").strip()
    parsed_jd = {
        "job_title": jd_title,
        "required_skills": [],
        "bonus_skills": [],
        "competency_requirements": [],
        "scoring_config": {},
        "raw_jd_text": jd_title,
    }
    parsed_resume = {
        "name": resume_name,
        "education": "",
        "degree": "",
        "major": "",
        "graduation_date": "",
        "internships": [],
        "projects": [],
        "skills": [],
        "awards": [],
        "languages": [],
        "normalized_resume_text": "",
    }
    score_details = record.get("scores") if isinstance(record.get("scores"), dict) else {}
    evidence_snippets = record.get("evidence_snippets") if isinstance(record.get("evidence_snippets"), list) else []
    screening_reasons = record.get("screening_reasons") if isinstance(record.get("screening_reasons"), list) else []
    return build_chunks_from_runtime_context(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        resume_text="",
        score_details=score_details,
        evidence_snippets=evidence_snippets,
        screening_reasons=screening_reasons,
        candidate_id=resume_name or str(record.get("review_id") or ""),
        job_id=jd_title,
    )


def build_chunks_from_batch_candidate(
    *,
    jd_title: str,
    candidate_row: dict[str, Any] | None = None,
    detail_payload: dict[str, Any] | None = None,
    batch_id: str = "",
) -> list[dict[str, Any]]:
    row = _normalize_payload(candidate_row)
    detail = _normalize_payload(detail_payload)
    parsed_jd = _normalize_payload(detail.get("parsed_jd"))
    parsed_resume = _normalize_payload(detail.get("parsed_resume"))
    extract_info = _normalize_payload(detail.get("extract_info"))
    score_details = _normalize_payload(detail.get("score_details"))
    screening_result = _normalize_payload(detail.get("screening_result"))
    evidence_snippets = detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else []
    screening_reasons = screening_result.get("screening_reasons") if isinstance(screening_result.get("screening_reasons"), list) else []
    resume_text = str(
        detail.get("normalized_resume_text")
        or detail.get("raw_resume_text")
        or extract_info.get("normalized_ocr_text")
        or extract_info.get("text")
        or ""
    )

    if not parsed_jd:
        parsed_jd = {
            "job_title": jd_title,
            "required_skills": [],
            "bonus_skills": [],
            "competency_requirements": [],
            "scoring_config": _normalize_payload(detail.get("scoring_config")),
            "raw_jd_text": "",
        }

    candidate_id = str(
        row.get("candidate_id")
        or detail.get("candidate_id")
        or parsed_resume.get("name")
        or row.get("姓名")
        or row.get("candidate_name")
        or ""
    ).strip()
    job_anchor = str(parsed_jd.get("job_title") or jd_title or batch_id or "").strip()

    return build_chunks_from_runtime_context(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        resume_text=resume_text,
        score_details=score_details,
        evidence_snippets=evidence_snippets,
        screening_reasons=screening_reasons,
        candidate_id=candidate_id,
        job_id=job_anchor,
    )


def index_runtime_context(
    *,
    parsed_jd: dict[str, Any] | None = None,
    parsed_resume: dict[str, Any] | None = None,
    resume_text: str = "",
    score_details: dict[str, Any] | None = None,
    evidence_snippets: list[dict[str, Any]] | None = None,
    screening_reasons: list[str] | None = None,
    candidate_id: str = "",
    job_id: str = "",
    store_path: str | None = None,
    collection: str = "default",
    embedding_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    documents = build_chunks_from_runtime_context(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        resume_text=resume_text,
        score_details=score_details,
        evidence_snippets=evidence_snippets,
        screening_reasons=screening_reasons,
        candidate_id=candidate_id,
        job_id=job_id,
    )
    return index_documents(
        documents,
        store_path=store_path,
        reset=False,
        embedding_config=embedding_config,
        collection=collection,
    )
