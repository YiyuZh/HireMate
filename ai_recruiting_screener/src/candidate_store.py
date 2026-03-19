"""岗位级候选池存储（JSON，本地版）。

能力：
- save_candidate_batch(...): 保存一次批量初筛结果
- list_jd_titles(): 返回已有候选池的岗位标题
- list_batches_by_jd(jd_title): 返回某岗位的批次摘要（新到旧）
- load_batch(batch_id): 加载指定批次
- load_latest_batch_by_jd(jd_title): 加载岗位最新批次
- delete_batch(batch_id): 删除指定批次
- delete_batches_by_jd(jd_title): 删除岗位下全部批次
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_pool_store.json"


def _ensure_store_file() -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        STORE_PATH.write_text("[]", encoding="utf-8")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_batch(item: dict) -> dict:
    batch_id = str(item.get("batch_id") or f"batch-{uuid4().hex}")
    jd_title = str(item.get("jd_title") or "未命名岗位")
    created_at = str(item.get("created_at") or _now_str())

    candidates = item.get("candidates") or []
    if not isinstance(candidates, list):
        candidates = []

    normalized_candidates: list[dict] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        candidate_id = str(cand.get("candidate_id") or f"cand-{uuid4().hex[:8]}")
        row = cand.get("row") or {}
        detail = cand.get("detail") or {}
        normalized_candidates.append(
            {
                "candidate_id": candidate_id,
                "screening_result": cand.get("screening_result") or row.get("初筛结论", ""),
                "risk_level": cand.get("risk_level") or row.get("风险等级", "unknown"),
                "scores": cand.get("scores") or detail.get("score_details") or {},
                "review_summary": cand.get("review_summary") or row.get("审核摘要", ""),
                "extract_info": cand.get("extract_info") or detail.get("extract_info") or {},
                "manual_decision": str(cand.get("manual_decision") or row.get("人工最终结论") or ""),
                "manual_note": str(cand.get("manual_note") or ""),
                "manual_priority": str(cand.get("manual_priority") or row.get("处理优先级") or detail.get("manual_priority") or "普通"),
                "updated_at": str(cand.get("updated_at") or item.get("created_at") or _now_str()),
                "row": row,
                "detail": detail,
            }
        )

    total_resumes = int(item.get("total_resumes") or len(normalized_candidates))
    pass_count = int(item.get("pass_count") or sum(1 for c in normalized_candidates if c.get("screening_result") == "推荐进入下一轮"))
    review_count = int(item.get("review_count") or sum(1 for c in normalized_candidates if c.get("screening_result") == "建议人工复核"))
    reject_count = int(item.get("reject_count") or sum(1 for c in normalized_candidates if c.get("screening_result") == "暂不推荐"))

    return {
        "batch_id": batch_id,
        "jd_title": jd_title,
        "created_at": created_at,
        "total_resumes": total_resumes,
        "pass_count": pass_count,
        "review_count": review_count,
        "reject_count": reject_count,
        "candidates": normalized_candidates,
    }


def _read_store() -> list[dict]:
    _ensure_store_file()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8") or "[]")
        if isinstance(data, list):
            return [_normalize_batch(item) for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _write_store(data: list[dict]) -> None:
    _ensure_store_file()
    STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_candidate_batch(jd_title: str, rows: list[dict], details: dict[str, dict]) -> str:
    """保存一次批量初筛结果，返回 batch_id。"""
    batch_id = f"batch-{uuid4().hex}"
    clean_jd_title = (jd_title or "").strip() or "未命名岗位"

    candidates: list[dict] = []
    for row in rows or []:
        cand_id = row.get("candidate_id")
        detail = (details or {}).get(cand_id, {})
        candidates.append(
            {
                "candidate_id": cand_id,
                "screening_result": row.get("初筛结论", ""),
                "risk_level": row.get("风险等级", "unknown"),
                "scores": detail.get("score_details") or {},
                "review_summary": row.get("审核摘要", ""),
                "extract_info": detail.get("extract_info") or {},
                "manual_priority": str(row.get("处理优先级") or detail.get("manual_priority") or "普通"),
                "row": row,
                "detail": detail,
            }
        )

    payload = {
        "batch_id": batch_id,
        "jd_title": clean_jd_title,
        "created_at": _now_str(),
        "total_resumes": len(rows or []),
        "pass_count": sum(1 for r in rows or [] if r.get("初筛结论") == "推荐进入下一轮"),
        "review_count": sum(1 for r in rows or [] if r.get("初筛结论") == "建议人工复核"),
        "reject_count": sum(1 for r in rows or [] if r.get("初筛结论") == "暂不推荐"),
        "candidates": candidates,
    }

    all_batches = _read_store()
    all_batches.append(_normalize_batch(payload))
    _write_store(all_batches)
    return batch_id


def list_jd_titles() -> list[str]:
    titles = {str(item.get("jd_title") or "未命名岗位") for item in _read_store()}
    return sorted(titles)


def list_batches_by_jd(jd_title: str) -> list[dict]:
    clean_title = (jd_title or "").strip()
    result = []
    for item in _read_store():
        if str(item.get("jd_title") or "") != clean_title:
            continue
        result.append(
            {
                "batch_id": item.get("batch_id", ""),
                "jd_title": item.get("jd_title", ""),
                "created_at": item.get("created_at", ""),
                "candidate_count": len(item.get("candidates") or []),
                "total_resumes": int(item.get("total_resumes") or len(item.get("candidates") or [])),
                "pass_count": int(item.get("pass_count") or 0),
                "review_count": int(item.get("review_count") or 0),
                "reject_count": int(item.get("reject_count") or 0),
            }
        )
    return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)


def load_batch(batch_id: str) -> dict | None:
    key = (batch_id or "").strip()
    if not key:
        return None

    for item in _read_store():
        if str(item.get("batch_id") or "") != key:
            continue
        rows: list[dict] = []
        details: dict[str, dict] = {}
        for cand in item.get("candidates") or []:
            cand_id = cand.get("candidate_id")
            row = cand.get("row") or {}
            detail = cand.get("detail") or {}
            if cand_id:
                manual_decision = str(cand.get("manual_decision") or "")
                if manual_decision:
                    row["人工最终结论"] = manual_decision
                if cand.get("manual_note"):
                    row["人工备注"] = cand.get("manual_note")
                manual_priority = str(cand.get("manual_priority") or row.get("处理优先级") or detail.get("manual_priority") or "普通")
                row["处理优先级"] = manual_priority
                rows.append(row)
                detail["manual_decision"] = manual_decision
                detail["manual_note"] = cand.get("manual_note") or ""
                detail["manual_priority"] = manual_priority
                detail["updated_at"] = cand.get("updated_at") or item.get("created_at") or ""
                details[cand_id] = detail
        return {
            "batch_id": item.get("batch_id", ""),
            "jd_title": item.get("jd_title", ""),
            "created_at": item.get("created_at", ""),
            "total_resumes": int(item.get("total_resumes") or len(item.get("candidates") or [])),
            "pass_count": int(item.get("pass_count") or 0),
            "review_count": int(item.get("review_count") or 0),
            "reject_count": int(item.get("reject_count") or 0),
            "candidates": item.get("candidates") or [],
            "rows": rows,
            "details": details,
        }
    return None


def load_latest_batch_by_jd(jd_title: str) -> dict | None:
    batches = list_batches_by_jd(jd_title)
    if not batches:
        return None
    return load_batch(batches[0].get("batch_id", ""))


def delete_batch(batch_id: str) -> bool:
    """删除指定批次，成功返回 True。"""
    bid = (batch_id or "").strip()
    if not bid:
        return False

    data = _read_store()
    new_data = [item for item in data if str(item.get("batch_id") or "") != bid]
    if len(new_data) == len(data):
        return False
    _write_store(new_data)
    return True


def delete_batches_by_jd(jd_title: str) -> int:
    """删除岗位下全部批次，返回删除数量。"""
    target = (jd_title or "").strip()
    if not target:
        return 0

    data = _read_store()
    new_data = [item for item in data if str(item.get("jd_title") or "").strip() != target]
    deleted = len(data) - len(new_data)
    if deleted > 0:
        _write_store(new_data)
    return deleted


def _recount_batch_pools(batch: dict) -> None:
    """按候选人的当前候选池重算批次分流统计。"""
    pass_count = 0
    review_count = 0
    reject_count = 0
    for cand in batch.get("candidates") or []:
        row = cand.get("row") or {}
        manual_decision = str(cand.get("manual_decision") or row.get("人工最终结论") or "").strip()
        if manual_decision == "通过":
            pass_count += 1
        elif manual_decision == "待复核":
            review_count += 1
        elif manual_decision == "淘汰":
            reject_count += 1
        else:
            pool = str(row.get("候选池") or "")
            if pool == "通过候选人":
                pass_count += 1
            elif pool == "待复核候选人":
                review_count += 1
            elif pool == "淘汰候选人":
                reject_count += 1

    batch["pass_count"] = pass_count
    batch["review_count"] = review_count
    batch["reject_count"] = reject_count


def upsert_candidate_manual_review(
    batch_id: str,
    candidate_id: str,
    manual_decision: str | None = None,
    manual_note: str | None = None,
    manual_priority: str | None = None,
) -> bool:
    """更新候选人在指定批次内的人工决策/备注。"""
    bid = (batch_id or "").strip()
    cid = (candidate_id or "").strip()
    if not bid or not cid:
        return False

    data = _read_store()
    updated = False
    for batch in data:
        if str(batch.get("batch_id") or "") != bid:
            continue
        for cand in batch.get("candidates") or []:
            if str(cand.get("candidate_id") or "") != cid:
                continue
            row = cand.get("row") or {}
            detail = cand.get("detail") or {}
            if manual_decision is not None:
                decision = str(manual_decision)
                cand["manual_decision"] = decision
                row["人工最终结论"] = decision
                detail["manual_decision"] = decision
            if manual_note is not None:
                note = str(manual_note)
                cand["manual_note"] = note
                row["人工备注"] = note
                detail["manual_note"] = note
            if manual_priority is not None:
                priority = str(manual_priority)
                cand["manual_priority"] = priority
                row["处理优先级"] = priority
                detail["manual_priority"] = priority
            cand["row"] = row
            cand["detail"] = detail
            cand["updated_at"] = _now_str()
            updated = True
            break
        if updated:
            _recount_batch_pools(batch)
            break

    if updated:
        _write_store(data)
    return updated


if __name__ == "__main__":
    demo_rows = [{"candidate_id": "cand_1", "姓名": "张三", "初筛结论": "建议人工复核", "风险等级": "medium", "审核摘要": "需核验"}]
    demo_details = {"cand_1": {"score_details": {"综合推荐度": {"score": 3}}, "extract_info": {"method": "text", "quality": "ok", "message": "demo"}}}
    new_batch_id = save_candidate_batch("AI 产品经理实习生", demo_rows, demo_details)
    print("saved:", new_batch_id)
    print("titles:", list_jd_titles())
    print("batches:", list_batches_by_jd("AI 产品经理实习生")[:1])
    print("latest:", load_latest_batch_by_jd("AI 产品经理实习生"))
