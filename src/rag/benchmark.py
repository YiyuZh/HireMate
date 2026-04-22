from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from typing import Any

from .metadata import extract_skill_tags_from_text, resolve_role_family, safe_identifier
from .retriever import (
    retrieve_for_ai_reviewer,
    retrieve_for_counter_evidence,
    retrieve_for_evidence_grounding,
    retrieve_for_historical_grounding,
    retrieve_for_jd_alignment,
    retrieve_for_missing_evidence,
    retrieve_for_risk_grounding,
    retrieve_for_semantic_anchors,
)


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+/.#-]*|[\u4e00-\u9fff]{2,}")
_GENERIC_TERMS = {
    "岗位",
    "简历",
    "候选人",
    "相关",
    "能力",
    "经验",
    "经历",
    "项目",
    "实习",
    "证据",
    "说明",
    "结论",
    "建议",
    "分析",
    "工作",
    "内容",
    "负责",
    "参与",
    "协助",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_text(value)
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean)
    return deduped


def _clip(text: str, limit: int = 140) -> str:
    clean = _clean_text(text).replace("\n", " ")
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _tokenize(text: str) -> list[str]:
    return _dedupe(_TOKEN_RE.findall(_clean_text(text)))


def _build_query(parts: list[str], *, limit: int = 420) -> str:
    query = " ".join(_clean_text(part) for part in parts if _clean_text(part)).strip()
    return query[:limit].strip()


def _pick_expected_terms(
    texts: list[str],
    *,
    preferred_terms: list[str] | None = None,
    limit: int = 3,
) -> list[str]:
    selected: list[str] = []
    candidates = _dedupe(preferred_terms or [])
    for text in texts:
        candidates.extend(extract_skill_tags_from_text(text, extra_candidates=preferred_terms or []))
        candidates.extend(_tokenize(text))

    for candidate in candidates:
        clean = _clean_text(candidate)
        if not clean:
            continue
        if clean.lower() in _GENERIC_TERMS:
            continue
        if clean.isdigit():
            continue
        if len(clean) <= 1:
            continue
        selected.append(clean)
        if len(_dedupe(selected)) >= limit:
            break
    return _dedupe(selected)[:limit]


def _pick_counter_terms(texts: list[str]) -> list[str]:
    negatives = []
    for text in texts:
        clean = _clean_text(text)
        if not clean:
            continue
        if any(token in clean for token in ["缺少", "不足", "未覆盖", "未体现"]):
            negatives.append(clean)
    return _pick_expected_terms(negatives, preferred_terms=_tokenize(" ".join(negatives)), limit=3)


def _case_id(prefix: str, seed: str) -> str:
    return safe_identifier(seed, prefix).replace(prefix + "-", prefix + "_")


def _build_case(
    *,
    case_id: str,
    task: str,
    query: str,
    expected_source_types: list[str] | None = None,
    expected_substrings: list[str] | None = None,
    expected_chunk_labels: list[str] | None = None,
    role_family: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
    top_k: int = 4,
    created_from: str = "",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "task": task,
        "query": _clean_text(query),
        "expected_source_types": _dedupe([str(item) for item in (expected_source_types or [])]),
        "expected_substrings": _dedupe([str(item) for item in (expected_substrings or [])]),
        "expected_chunk_labels": _dedupe([str(item) for item in (expected_chunk_labels or [])]),
        "role_family": _clean_text(role_family),
        "job_id_safe": _clean_text(job_id_safe),
        "candidate_id_safe": _clean_text(candidate_id_safe),
        "top_k": max(1, int(top_k or 4)),
        "created_from": _clean_text(created_from),
    }


def build_cases_from_review_record(review_record: dict[str, Any]) -> list[dict[str, Any]]:
    record = review_record if isinstance(review_record, dict) else {}
    jd_title = _clean_text(record.get("jd_title"))
    resume_name = _clean_text(record.get("resume_name") or record.get("resume_file") or record.get("review_id"))
    evidence_snippets = record.get("evidence_snippets") if isinstance(record.get("evidence_snippets"), list) else []
    screening_reasons = record.get("screening_reasons") if isinstance(record.get("screening_reasons"), list) else []

    evidence_texts = [
        _clean_text(item.get("text") if isinstance(item, dict) else item)
        for item in evidence_snippets[:3]
        if _clean_text(item.get("text") if isinstance(item, dict) else item)
    ]
    reason_texts = [_clean_text(item) for item in screening_reasons[:3] if _clean_text(item)]

    job_id_safe = safe_identifier(jd_title, "job")
    candidate_id_safe = safe_identifier(resume_name, "cand")
    cases: list[dict[str, Any]] = []

    if jd_title:
        jd_terms = _pick_expected_terms([jd_title], preferred_terms=_tokenize(jd_title), limit=2)
        cases.append(
            _build_case(
                case_id=_case_id("review_jd", f"{record.get('review_id')}|{jd_title}"),
                task="jd_alignment",
                query=_build_query([jd_title, *reason_texts[:1]]),
                expected_source_types=["jd", "role_profile"],
                expected_substrings=jd_terms,
                job_id_safe=job_id_safe,
                top_k=3,
                created_from=f"review:{_clean_text(record.get('review_id'))}",
            )
        )

        cases.append(
            _build_case(
                case_id=_case_id("review_anchor", f"{record.get('review_id')}|{jd_title}|anchor"),
                task="semantic_anchors",
                query=_build_query([jd_title, *reason_texts[:1]]),
                expected_source_types=["jd", "role_profile", "rubric"],
                expected_substrings=jd_terms,
                job_id_safe=job_id_safe,
                top_k=3,
                created_from=f"review:{_clean_text(record.get('review_id'))}",
            )
        )

    if evidence_texts:
        cases.append(
            _build_case(
                case_id=_case_id("review_evidence", f"{record.get('review_id')}|{resume_name}|evidence"),
                task="evidence_grounding",
                query=_build_query([jd_title, *reason_texts[:2], *evidence_texts[:2]]),
                expected_source_types=["evidence", "rubric"],
                expected_substrings=_pick_expected_terms(
                    evidence_texts + reason_texts,
                    preferred_terms=_tokenize(" ".join(evidence_texts)),
                ),
                job_id_safe=job_id_safe,
                candidate_id_safe=candidate_id_safe,
                top_k=4,
                created_from=f"review:{_clean_text(record.get('review_id'))}",
            )
        )

        counter_terms = _pick_counter_terms(reason_texts)
        if counter_terms:
            cases.append(
                _build_case(
                    case_id=_case_id("review_counter", f"{record.get('review_id')}|{resume_name}|counter"),
                    task="counter_evidence",
                    query=_build_query([jd_title, *counter_terms, *evidence_texts[:1]]),
                    expected_source_types=["evidence", "rubric", "resume_fragment"],
                    expected_substrings=_pick_expected_terms(counter_terms),
                    job_id_safe=job_id_safe,
                    candidate_id_safe=candidate_id_safe,
                    top_k=4,
                    created_from=f"review:{_clean_text(record.get('review_id'))}",
                )
            )

        cases.append(
            _build_case(
                case_id=_case_id("review_missing", f"{record.get('review_id')}|{resume_name}|missing"),
                task="missing_evidence",
                query=_build_query([jd_title, "缺少 未覆盖", *reason_texts[:2]]),
                expected_source_types=["jd", "role_profile", "rubric"],
                expected_substrings=_pick_expected_terms(reason_texts),
                job_id_safe=job_id_safe,
                top_k=3,
                created_from=f"review:{_clean_text(record.get('review_id'))}",
            )
        )

    if evidence_texts or reason_texts:
        cases.append(
            _build_case(
                case_id=_case_id("review_ai", f"{record.get('review_id')}|{resume_name}|ai"),
                task="ai_reviewer",
                query=_build_query([jd_title, *reason_texts[:2], *evidence_texts[:2]]),
                expected_source_types=["evidence", "rubric", "role_profile"],
                expected_substrings=_pick_expected_terms(reason_texts + evidence_texts),
                job_id_safe=job_id_safe,
                candidate_id_safe=candidate_id_safe,
                top_k=4,
                created_from=f"review:{_clean_text(record.get('review_id'))}",
            )
        )

        cases.append(
            _build_case(
                case_id=_case_id("review_history", f"{record.get('review_id')}|{resume_name}|history"),
                task="historical_grounding",
                query=_build_query([jd_title, *evidence_texts[:2], *reason_texts[:1]]),
                expected_source_types=["evidence", "resume_fragment", "rubric"],
                expected_substrings=_pick_expected_terms(evidence_texts),
                job_id_safe=job_id_safe,
                top_k=4,
                created_from=f"review:{_clean_text(record.get('review_id'))}",
            )
        )

        risk_terms = [text for text in reason_texts if "风险" in text]
        if risk_terms:
            cases.append(
                _build_case(
                    case_id=_case_id("review_risk", f"{record.get('review_id')}|{resume_name}|risk"),
                    task="risk_grounding",
                    query=_build_query([jd_title, *risk_terms[:2]]),
                    expected_source_types=["evidence", "rubric"],
                    expected_substrings=_pick_expected_terms(risk_terms),
                    job_id_safe=job_id_safe,
                    candidate_id_safe=candidate_id_safe,
                    top_k=4,
                    created_from=f"review:{_clean_text(record.get('review_id'))}",
                )
            )

    return [case for case in cases if case.get("query")]


def build_cases_from_batch_candidate(
    *,
    jd_title: str,
    candidate_row: dict[str, Any] | None = None,
    detail_payload: dict[str, Any] | None = None,
    batch_id: str = "",
) -> list[dict[str, Any]]:
    row = candidate_row if isinstance(candidate_row, dict) else {}
    detail = detail_payload if isinstance(detail_payload, dict) else {}
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    parsed_resume = detail.get("parsed_resume") if isinstance(detail.get("parsed_resume"), dict) else {}
    score_details = detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    screening_result = detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {}
    evidence_snippets = detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else []
    screening_reasons = (
        screening_result.get("screening_reasons")
        if isinstance(screening_result.get("screening_reasons"), list)
        else []
    )

    job_anchor = _clean_text(parsed_jd.get("job_title") or jd_title or batch_id)
    candidate_anchor = _clean_text(
        row.get("candidate_id")
        or detail.get("candidate_id")
        or parsed_resume.get("name")
        or row.get("candidate_name")
        or row.get("姓名")
    )
    role_family = resolve_role_family(parsed_jd)
    job_id_safe = safe_identifier(job_anchor, "job")
    candidate_id_safe = safe_identifier(candidate_anchor, "cand")
    required_skills = [str(item) for item in (parsed_jd.get("expanded_required_skills") or parsed_jd.get("required_skills") or [])]
    bonus_skills = [str(item) for item in (parsed_jd.get("expanded_bonus_skills") or parsed_jd.get("bonus_skills") or [])]
    resume_skills = [str(item) for item in (parsed_resume.get("skills") or [])]
    evidence_texts = [
        _clean_text(item.get("text") if isinstance(item, dict) else item)
        for item in evidence_snippets[:3]
        if _clean_text(item.get("text") if isinstance(item, dict) else item)
    ]
    reason_texts = [_clean_text(item) for item in screening_reasons[:3] if _clean_text(item)]
    representative_texts = []
    for detail_name, detail_payload_item in score_details.items():
        if not isinstance(detail_payload_item, dict):
            continue
        representative = (
            detail_payload_item.get("representative_evidence")
            if isinstance(detail_payload_item.get("representative_evidence"), dict)
            else {}
        )
        rep_text = _clean_text(
            representative.get("display_text") or representative.get("text") or representative.get("raw_text")
        )
        if rep_text:
            representative_texts.append(rep_text)
        if len(representative_texts) >= 3:
            break

    base_seed = f"{batch_id}|{job_anchor}|{candidate_anchor}"
    cases: list[dict[str, Any]] = []
    if job_anchor:
        cases.append(
            _build_case(
                case_id=_case_id("batch_jd", base_seed + "|jd"),
                task="jd_alignment",
                query=_build_query([job_anchor, " ".join(required_skills[:4]), " ".join(bonus_skills[:3])]),
                expected_source_types=["jd", "role_profile", "rubric"],
                expected_substrings=_pick_expected_terms(
                    required_skills + bonus_skills + [job_anchor],
                    preferred_terms=required_skills + bonus_skills,
                ),
                role_family=role_family,
                job_id_safe=job_id_safe,
                top_k=4,
                created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
            )
        )

        cases.append(
            _build_case(
                case_id=_case_id("batch_anchor", base_seed + "|anchor"),
                task="semantic_anchors",
                query=_build_query([job_anchor, " ".join(required_skills[:4])]),
                expected_source_types=["jd", "role_profile", "rubric"],
                expected_substrings=_pick_expected_terms(
                    required_skills + bonus_skills + [job_anchor],
                    preferred_terms=required_skills + bonus_skills,
                ),
                role_family=role_family,
                job_id_safe=job_id_safe,
                top_k=4,
                created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
            )
        )

    evidence_expected_terms = _pick_expected_terms(
        evidence_texts + representative_texts + resume_skills,
        preferred_terms=resume_skills + required_skills,
    )
    evidence_query = _build_query(
        [
            job_anchor,
            " ".join(required_skills[:4]),
            " ".join(resume_skills[:4]),
            *evidence_texts[:2],
            *representative_texts[:1],
        ]
    )
    if (resume_skills or evidence_texts or representative_texts) and evidence_expected_terms and evidence_query and evidence_query != job_anchor:
        cases.append(
            _build_case(
                case_id=_case_id("batch_evidence", base_seed + "|evidence"),
                task="evidence_grounding",
                query=evidence_query,
                expected_source_types=["resume_fragment", "evidence", "rubric"],
                expected_substrings=evidence_expected_terms,
                role_family=role_family,
                job_id_safe=job_id_safe,
                candidate_id_safe=candidate_id_safe,
                top_k=4,
                created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
            )
        )

        counter_terms = _pick_counter_terms(reason_texts)
        if counter_terms:
            cases.append(
                _build_case(
                    case_id=_case_id("batch_counter", base_seed + "|counter"),
                    task="counter_evidence",
                    query=_build_query([job_anchor, *counter_terms, *evidence_texts[:1]]),
                    expected_source_types=["resume_fragment", "evidence", "rubric"],
                    expected_substrings=_pick_expected_terms(counter_terms),
                    role_family=role_family,
                    job_id_safe=job_id_safe,
                    candidate_id_safe=candidate_id_safe,
                    top_k=4,
                    created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
                )
            )

        cases.append(
            _build_case(
                case_id=_case_id("batch_missing", base_seed + "|missing"),
                task="missing_evidence",
                query=_build_query([job_anchor, "缺少 未覆盖", *reason_texts[:2]]),
                expected_source_types=["jd", "role_profile", "rubric"],
                expected_substrings=_pick_expected_terms(reason_texts),
                role_family=role_family,
                job_id_safe=job_id_safe,
                top_k=3,
                created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
            )
        )

    ai_expected_terms = _pick_expected_terms(
        reason_texts + evidence_texts + representative_texts + required_skills,
        preferred_terms=required_skills + resume_skills,
    )
    ai_query = _build_query(
        [
            job_anchor,
            " ".join(required_skills[:4]),
            " ".join(resume_skills[:4]),
            *reason_texts[:2],
            *evidence_texts[:2],
        ]
    )
    if (reason_texts or evidence_texts or representative_texts or resume_skills) and ai_expected_terms and ai_query and ai_query != job_anchor:
        cases.append(
            _build_case(
                case_id=_case_id("batch_ai", base_seed + "|ai"),
                task="ai_reviewer",
                query=ai_query,
                expected_source_types=["role_profile", "resume_fragment", "evidence", "rubric"],
                expected_substrings=ai_expected_terms,
                role_family=role_family,
                job_id_safe=job_id_safe,
                candidate_id_safe=candidate_id_safe,
                top_k=4,
                created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
            )
        )

        cases.append(
            _build_case(
                case_id=_case_id("batch_history", base_seed + "|history"),
                task="historical_grounding",
                query=_build_query([job_anchor, *evidence_texts[:2], *reason_texts[:1]]),
                expected_source_types=["evidence", "resume_fragment", "rubric"],
                expected_substrings=_pick_expected_terms(evidence_texts),
                role_family=role_family,
                job_id_safe=job_id_safe,
                top_k=4,
                created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
            )
        )

        risk_terms = [text for text in reason_texts if "风险" in text]
        if risk_terms:
            cases.append(
                _build_case(
                    case_id=_case_id("batch_risk", base_seed + "|risk"),
                    task="risk_grounding",
                    query=_build_query([job_anchor, *risk_terms[:2]]),
                    expected_source_types=["evidence", "rubric"],
                    expected_substrings=_pick_expected_terms(risk_terms),
                    role_family=role_family,
                    job_id_safe=job_id_safe,
                    candidate_id_safe=candidate_id_safe,
                    top_k=4,
                    created_from=f"batch:{batch_id}|candidate:{candidate_anchor}",
                )
            )

    return [case for case in cases if case.get("query")]


def load_benchmark_cases(path: str | Path) -> list[dict[str, Any]]:
    case_path = Path(path).resolve()
    if not case_path.exists():
        return []
    cases: list[dict[str, Any]] = []
    for line in case_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        payload = json.loads(raw)
        if isinstance(payload, dict):
            cases.append(payload)
    return cases


def save_benchmark_cases(path: str | Path, cases: list[dict[str, Any]]) -> None:
    case_path = Path(path).resolve()
    case_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(case, ensure_ascii=False) for case in cases)
    case_path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def _reciprocal_rank(results: list[dict[str, Any]], predicate) -> float:
    for index, item in enumerate(results, start=1):
        if predicate(item):
            return 1.0 / index
    return 0.0


def _mean_metric(results: list[dict[str, Any]], key: str) -> float:
    values = [float(item.get(key) or 0.0) for item in results if item.get(key) is not None]
    if not values:
        return 0.0
    return round(sum(values) / max(1, len(values)), 6)


def run_benchmark_case(
    case: dict[str, Any],
    *,
    store_path: str,
    runtime_config: dict[str, Any],
    default_collection: str = "default",
) -> dict[str, Any]:
    task = _clean_text(case.get("task"))
    query = _clean_text(case.get("query"))
    top_k = max(1, int(case.get("top_k") or 3))
    role_family = _clean_text(case.get("role_family"))
    job_id_safe = _clean_text(case.get("job_id_safe"))
    candidate_id_safe = _clean_text(case.get("candidate_id_safe"))
    collection = _clean_text(case.get("collection")) or default_collection

    if task == "jd_alignment":
        results = retrieve_for_jd_alignment(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    elif task == "semantic_anchors":
        results = retrieve_for_semantic_anchors(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    elif task == "evidence_grounding":
        results = retrieve_for_evidence_grounding(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            candidate_id_safe=candidate_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    elif task == "counter_evidence":
        results = retrieve_for_counter_evidence(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            candidate_id_safe=candidate_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    elif task == "missing_evidence":
        results = retrieve_for_missing_evidence(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    elif task == "ai_reviewer":
        results = retrieve_for_ai_reviewer(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            candidate_id_safe=candidate_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    elif task == "historical_grounding":
        results = retrieve_for_historical_grounding(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    elif task == "risk_grounding":
        results = retrieve_for_risk_grounding(
            query,
            top_k=top_k,
            role_family=role_family,
            job_id_safe=job_id_safe,
            candidate_id_safe=candidate_id_safe,
            store_path=store_path,
            collection=collection,
            runtime_config=runtime_config,
        )
    else:
        raise ValueError(f"unsupported benchmark task: {task}")

    expected_source_types = {str(item) for item in (case.get("expected_source_types") or []) if _clean_text(item)}
    expected_substrings = [str(item) for item in (case.get("expected_substrings") or []) if _clean_text(item)]
    expected_chunk_labels = [str(item) for item in (case.get("expected_chunk_labels") or []) if _clean_text(item)]

    source_rank = _reciprocal_rank(
        results,
        lambda item: str((item.get("metadata") or {}).get("source_type") or "") in expected_source_types,
    ) if expected_source_types else 1.0
    substring_rank = _reciprocal_rank(
        results,
        lambda item: any(expected.lower() in _clean_text(item.get("text")).lower() for expected in expected_substrings),
    ) if expected_substrings else 1.0
    label_rank = _reciprocal_rank(
        results,
        lambda item: any(
            expected.lower() in str((item.get("metadata") or {}).get("chunk_label") or "").lower()
            for expected in expected_chunk_labels
        ),
    ) if expected_chunk_labels else 1.0

    source_hit = source_rank > 0 if expected_source_types else True
    substring_hit = substring_rank > 0 if expected_substrings else True
    label_hit = label_rank > 0 if expected_chunk_labels else True
    success = bool(results) and source_hit and substring_hit and label_hit
    combined_score = (
        (1.0 if success else 0.0)
        + source_rank * 0.25
        + substring_rank * 0.2
        + label_rank * 0.1
    )
    expected_blob = " ".join(expected_substrings).lower()
    hit_blob = " ".join(_clean_text(item.get("text") or "") for item in results[:top_k]).lower()
    expected_terms = [item for item in expected_substrings if item]
    explanation_consistency = 0.0
    if expected_terms:
        hits = sum(1 for term in expected_terms if term.lower() in hit_blob)
        explanation_consistency = hits / max(1, len(expected_terms))

    top_hits = results[:top_k]

    def _source_type(item: dict[str, Any]) -> str:
        return str((item.get("metadata") or {}).get("source_type") or "")

    def _label(item: dict[str, Any]) -> str:
        return str((item.get("metadata") or {}).get("chunk_label") or "").lower()

    def _matches_expected(item: dict[str, Any]) -> bool:
        source_type = _source_type(item)
        text = _clean_text(item.get("text") or "").lower()
        label = _label(item)
        source_match = bool(expected_source_types) and source_type in expected_source_types
        substring_match = bool(expected_substrings) and any(expected.lower() in text for expected in expected_substrings)
        label_match = bool(expected_chunk_labels) and any(expected.lower() in label for expected in expected_chunk_labels)
        if not expected_source_types and not expected_substrings and not expected_chunk_labels:
            return bool(item)
        return source_match or substring_match or label_match

    matched_top_hits = sum(1 for item in top_hits if _matches_expected(item))
    unsupported_claim_rate = 0.0 if not top_hits else max(0.0, 1.0 - (matched_top_hits / len(top_hits)))
    evidence_coverage_rate = explanation_consistency

    expects_counter_evidence = bool(case.get("expects_counter_evidence")) or task in {"counter_evidence", "risk_grounding"}
    expects_missing_evidence = bool(case.get("expects_missing_evidence")) or task == "missing_evidence"
    expects_manual_first = bool(case.get("expects_manual_first")) or expects_missing_evidence

    contradiction_handling_accuracy = None
    if expects_counter_evidence:
        counter_checks: list[float] = []
        if expected_source_types:
            counter_checks.append(1.0 if source_hit else 0.0)
        if expected_substrings:
            counter_checks.append(1.0 if substring_hit else 0.0)
        if expected_chunk_labels:
            counter_checks.append(1.0 if label_hit else 0.0)
        if not counter_checks:
            counter_checks.append(1.0 if results else 0.0)
        contradiction_handling_accuracy = sum(counter_checks) / max(1, len(counter_checks))

    abstention_quality = None
    if expects_missing_evidence or expects_manual_first:
        safe_source_types = expected_source_types or {"jd", "role_profile", "rubric"}
        safe_hits = sum(1 for item in top_hits if _source_type(item) in safe_source_types)
        abstention_quality = 1.0 if not top_hits else safe_hits / max(1, len(top_hits))

    manual_first_trigger_precision = None
    if expects_manual_first:
        abstention_score = float(abstention_quality or 0.0)
        manual_first_trigger_precision = 1.0 if abstention_score >= 0.5 and unsupported_claim_rate <= 0.5 else 0.0

    return {
        "case_id": _clean_text(case.get("case_id") or task),
        "task": task,
        "query": query,
        "success": success,
        "result_count": len(results),
        "source_hit": source_hit,
        "substring_hit": substring_hit,
        "label_hit": label_hit,
        "source_rank": round(source_rank, 6),
        "substring_rank": round(substring_rank, 6),
        "label_rank": round(label_rank, 6),
        "combined_score": round(combined_score, 6),
        "grounding_recall": 1.0 if substring_hit else 0.0,
        "counter_quality": round((substring_rank + source_rank) / 2.0, 6) if task in {"counter_evidence", "missing_evidence"} else 0.0,
        "explanation_consistency": round(explanation_consistency, 6),
        "unsupported_claim_rate": round(unsupported_claim_rate, 6),
        "evidence_coverage_rate": round(evidence_coverage_rate, 6),
        "contradiction_handling_accuracy": round(contradiction_handling_accuracy, 6) if contradiction_handling_accuracy is not None else None,
        "abstention_quality": round(abstention_quality, 6) if abstention_quality is not None else None,
        "manual_first_trigger_precision": round(manual_first_trigger_precision, 6) if manual_first_trigger_precision is not None else None,
        "created_from": _clean_text(case.get("created_from")),
        "top_hits": [
            {
                "score": float(item.get("fused_score") or item.get("score") or 0.0),
                "semantic_score": float(item.get("semantic_score") or item.get("score") or 0.0),
                "source_type": str((item.get("metadata") or {}).get("source_type") or ""),
                "chunk_label": str((item.get("metadata") or {}).get("chunk_label") or ""),
                "text": _clip(item.get("text") or ""),
            }
            for item in results[:top_k]
        ],
    }


def run_benchmark(
    cases: list[dict[str, Any]],
    *,
    store_path: str,
    runtime_config: dict[str, Any],
    default_collection: str = "default",
) -> dict[str, Any]:
    results = [
        run_benchmark_case(
            case,
            store_path=store_path,
            runtime_config=runtime_config,
            default_collection=default_collection,
        )
        for case in cases
    ]
    task_distribution = Counter(str(item.get("task") or "unknown") for item in results)
    task_metrics: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        grouped[str(item.get("task") or "unknown")].append(item)

    for task, task_results in grouped.items():
        task_metrics[task] = {
            "count": len(task_results),
            "passed": sum(1 for item in task_results if item.get("success")),
            "pass_rate": round(
                sum(1 for item in task_results if item.get("success")) / max(1, len(task_results)),
                6,
            ),
            "mean_combined_score": round(
                sum(float(item.get("combined_score") or 0.0) for item in task_results) / max(1, len(task_results)),
                6,
            ),
            "mean_grounding_recall": _mean_metric(task_results, "grounding_recall"),
            "mean_counter_quality": _mean_metric(task_results, "counter_quality"),
            "mean_explanation_consistency": _mean_metric(task_results, "explanation_consistency"),
            "mean_unsupported_claim_rate": _mean_metric(task_results, "unsupported_claim_rate"),
            "mean_evidence_coverage_rate": _mean_metric(task_results, "evidence_coverage_rate"),
            "mean_contradiction_handling_accuracy": _mean_metric(task_results, "contradiction_handling_accuracy"),
            "mean_abstention_quality": _mean_metric(task_results, "abstention_quality"),
            "mean_manual_first_trigger_precision": _mean_metric(task_results, "manual_first_trigger_precision"),
        }

    return {
        "cases": len(results),
        "passed": sum(1 for item in results if item.get("success")),
        "failed": sum(1 for item in results if not item.get("success")),
        "pass_rate": round(sum(1 for item in results if item.get("success")) / max(1, len(results)), 6),
        "mean_combined_score": round(
            sum(float(item.get("combined_score") or 0.0) for item in results) / max(1, len(results)),
            6,
        ),
        "mean_grounding_recall": round(
            sum(float(item.get("grounding_recall") or 0.0) for item in results) / max(1, len(results)),
            6,
        ),
        "mean_counter_quality": round(
            sum(float(item.get("counter_quality") or 0.0) for item in results) / max(1, len(results)),
            6,
        ),
        "mean_explanation_consistency": round(
            sum(float(item.get("explanation_consistency") or 0.0) for item in results) / max(1, len(results)),
            6,
        ),
        "mean_unsupported_claim_rate": _mean_metric(results, "unsupported_claim_rate"),
        "mean_evidence_coverage_rate": _mean_metric(results, "evidence_coverage_rate"),
        "mean_contradiction_handling_accuracy": _mean_metric(results, "contradiction_handling_accuracy"),
        "mean_abstention_quality": _mean_metric(results, "abstention_quality"),
        "mean_manual_first_trigger_precision": _mean_metric(results, "manual_first_trigger_precision"),
        "mean_source_rank": round(
            sum(float(item.get("source_rank") or 0.0) for item in results) / max(1, len(results)),
            6,
        ),
        "mean_substring_rank": round(
            sum(float(item.get("substring_rank") or 0.0) for item in results) / max(1, len(results)),
            6,
        ),
        "mean_label_rank": round(
            sum(float(item.get("label_rank") or 0.0) for item in results) / max(1, len(results)),
            6,
        ),
        "task_distribution": dict(task_distribution),
        "task_metrics": task_metrics,
        "results": results,
    }
