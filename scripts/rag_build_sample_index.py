from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.jd_parser import parse_jd
from src.rag import (
    DEFAULT_VECTOR_STORE_PATH,
    build_chunks_from_evidence,
    build_chunks_from_jd,
    build_chunks_from_resume,
    index_documents,
    resolve_vector_store_path,
)
from src.resume_parser import parse_resume
from src.role_profiles import build_default_scoring_config, detect_role_profile
from src.scorer import score_candidate, to_score_values
from src.screener import build_evidence_bridge, build_screening_decision, collect_evidence_snippets

def main() -> None:
    root = ROOT_DIR
    jd_path = root / "data" / "jd_samples" / "jd_01.txt"
    resume_path = root / "data" / "resume_samples" / "resume_01.txt"
    store_path = resolve_vector_store_path(DEFAULT_VECTOR_STORE_PATH)

    jd_text = jd_path.read_text(encoding="utf-8")
    resume_text = resume_path.read_text(encoding="utf-8")

    parsed_jd = parse_jd(jd_text)
    role_profile = detect_role_profile(parsed_jd)
    parsed_jd["scoring_config"] = build_default_scoring_config(role_profile.get("profile_name", "通用岗位模板"))

    parsed_resume = parse_resume(resume_text)
    score_details = score_candidate(parsed_jd, parsed_resume)
    evidence_snippets = collect_evidence_snippets(parsed_resume, parsed_jd=parsed_jd, role_profile=role_profile)
    evidence_bridge = build_evidence_bridge(score_details, evidence_snippets)
    screening = build_screening_decision(
        evidence_bridge.get("score_details") or score_details,
        scoring_config=parsed_jd.get("scoring_config"),
    )

    job_anchor = parsed_jd.get("job_title") or jd_path.stem
    candidate_anchor = resume_path.stem

    chunks = []
    chunks.extend(build_chunks_from_jd(jd_text, parsed_jd, job_id=str(job_anchor)))
    chunks.extend(
        build_chunks_from_resume(
            resume_text,
            parsed_resume,
            parsed_jd=parsed_jd,
            candidate_id=str(candidate_anchor),
            job_id=str(job_anchor),
        )
    )
    chunks.extend(
        build_chunks_from_evidence(
            evidence_snippets=evidence_bridge.get("summary_snippets") if isinstance(evidence_bridge.get("summary_snippets"), list) else evidence_snippets,
            score_details=evidence_bridge.get("score_details") if isinstance(evidence_bridge.get("score_details"), dict) else score_details,
            screening_reasons=screening.get("screening_reasons") if isinstance(screening.get("screening_reasons"), list) else [],
            parsed_jd=parsed_jd,
            candidate_id=str(candidate_anchor),
            job_id=str(job_anchor),
        )
    )

    summary = index_documents(chunks, store_path=str(store_path), reset=True)

    print("RAG sample index built")
    print(f"store_path: {store_path}")
    print(f"chunk_count: {summary.get('chunk_count')}")
    print(f"indexed_documents: {summary.get('indexed_documents')}")
    print(f"source_type_distribution: {summary.get('source_type_distribution')}")
    print(f"job_title: {parsed_jd.get('job_title') or jd_path.stem}")
    print(f"resume_name: {resume_path.stem}")
    print(f"overall_score: {to_score_values(score_details).get('综合推荐度')}")


if __name__ == "__main__":
    main()
