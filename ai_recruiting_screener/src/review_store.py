"""审核结果本地存储（JSON）。

能力：
- append_review(record): 追加一条审核记录（自动审核/初始化记录）
- upsert_manual_review(review_id, ...): 写入人工决策与备注留痕
- list_reviews(limit=None): 读取历史审核记录（最新优先）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "review_history.json"


def _ensure_store_file() -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        STORE_PATH.write_text("[]", encoding="utf-8")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_review(record: dict) -> dict:
    """兼容旧记录字段，统一为可留痕结构。"""
    timestamp = str(record.get("timestamp") or _now_str())
    auto_screening_result = str(
        record.get("auto_screening_result")
        or record.get("screening_result")
        or ""
    )
    auto_risk_level = str(
        record.get("auto_risk_level")
        or record.get("risk_level")
        or "unknown"
    )

    normalized = {
        "review_id": str(record.get("review_id") or "").strip(),
        "timestamp": timestamp,
        "updated_at": str(record.get("updated_at") or timestamp),
        "jd_title": str(record.get("jd_title") or ""),
        "resume_name": str(record.get("resume_name") or ""),
        "resume_file": str(record.get("resume_file") or ""),
        "scores": record.get("scores") or {},
        "auto_screening_result": auto_screening_result,
        "auto_risk_level": auto_risk_level,
        "manual_decision": str(record.get("manual_decision") or ""),
        "manual_note": str(record.get("manual_note") or ""),
        # 兼容旧 UI 字段
        "screening_result": auto_screening_result,
        "risk_level": auto_risk_level,
        "screening_reasons": record.get("screening_reasons") or [],
        "risk_points": record.get("risk_points") or [],
        "interview_summary": str(record.get("interview_summary") or ""),
        "evidence_snippets": record.get("evidence_snippets") or [],
    }
    return normalized


def _read_reviews() -> list[dict]:
    _ensure_store_file()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8") or "[]")
        if isinstance(data, list):
            return [_normalize_review(item) for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _write_reviews(reviews: list[dict]) -> None:
    _ensure_store_file()
    STORE_PATH.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")


def append_review(record: dict) -> None:
    """追加一条审核记录。"""
    reviews = _read_reviews()
    normalized = _normalize_review(record)
    normalized["updated_at"] = _now_str()
    reviews.append(normalized)
    _write_reviews(reviews)


def upsert_manual_review(
    review_id: str,
    manual_decision: str | None = None,
    manual_note: str | None = None,
) -> bool:
    """按 review_id 写入人工决策/备注，成功返回 True。"""
    key = (review_id or "").strip()
    if not key:
        return False

    reviews = _read_reviews()
    updated = False
    for idx, item in enumerate(reviews):
        if (item.get("review_id") or "").strip() != key:
            continue
        if manual_decision is not None:
            item["manual_decision"] = str(manual_decision)
        if manual_note is not None:
            item["manual_note"] = str(manual_note)
        item["updated_at"] = _now_str()
        reviews[idx] = _normalize_review(item)
        updated = True
        break

    if updated:
        _write_reviews(reviews)
    return updated


def list_reviews(limit: int | None = None) -> list[dict]:
    """返回历史记录（按时间倒序）。"""
    reviews = list(reversed(_read_reviews()))
    if limit is not None:
        return reviews[:limit]
    return reviews


if __name__ == "__main__":
    append_review(
        {
            "review_id": "demo-review-id",
            "timestamp": "2026-01-01 10:00:00",
            "jd_title": "AI 产品经理实习生",
            "resume_name": "张三",
            "scores": {"教育背景匹配度": 4},
            "auto_risk_level": "low",
            "auto_screening_result": "推荐进入下一轮",
        }
    )
    upsert_manual_review("demo-review-id", manual_decision="待复核", manual_note="需核验项目真实性")
    print(list_reviews(limit=3))
