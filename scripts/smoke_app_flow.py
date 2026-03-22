from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.candidate_store import delete_batch, list_batches_by_jd, list_jd_titles, load_batch, save_candidate_batch
from src.db import init_db
from src.jd_store import delete_jd, list_jds, save_jd
from src.utils import load_env


def _build_smoke_test_jd_text() -> str:
    return (
        "岗位名称：Smoke Test 产品经理实习生\n"
        "岗位职责：\n"
        "1. 协助整理岗位需求与候选人评估标准。\n"
        "2. 支持基础数据整理、跨团队沟通和文档输出。\n"
        "3. 参与候选人信息校验与工作台流转。\n"
        "任职要求：\n"
        "1. 本科及以上学历。\n"
        "2. 具备基础数据分析、文档整理与沟通能力。\n"
        "3. 熟悉 Python、SQL 或产品文档者优先。\n"
    )


def _build_smoke_candidate_payload(candidate_id: str) -> tuple[dict[str, object], dict[str, object]]:
    score_details = {
        "教育背景匹配度": {"score": 4, "reason": "smoke", "evidence": ["学历信息完整"]},
        "相关经历匹配度": {"score": 4, "reason": "smoke", "evidence": ["有基础项目经历"]},
        "技能匹配度": {"score": 4, "reason": "smoke", "evidence": ["具备基础技能"]},
        "表达完整度": {"score": 4, "reason": "smoke", "evidence": ["简历结构完整"]},
        "综合推荐度": {"score": 4, "reason": "smoke", "evidence": ["用于 smoke 测试"]},
    }
    row = {
        "candidate_id": candidate_id,
        "姓名": "Smoke 候选人",
        "文件名": "smoke_resume.txt",
        "解析状态": "正常识别",
        "初筛结论": "建议人工复核",
        "风险等级": "low",
        "候选池": "待复核候选人",
        "人工最终结论": "",
        "人工备注": "",
        "处理优先级": "中",
        "审核摘要": "Smoke 主流程测试候选人",
    }
    detail = {
        "parsed_jd": {"job_title": "Smoke Test 产品经理实习生", "scoring_config": {}},
        "parsed_resume": {"name": "Smoke 候选人"},
        "score_details": score_details,
        "score_values": {
            "教育背景匹配度": 4,
            "相关经历匹配度": 4,
            "技能匹配度": 4,
            "表达完整度": 4,
            "综合推荐度": 4,
        },
        "risk_result": {"risk_level": "low", "risk_summary": "smoke", "risk_points": []},
        "screening_result": {
            "screening_result": "建议人工复核",
            "screening_reasons": ["Smoke 测试批次"],
            "gating_signals": {},
        },
        "interview_plan": {"interview_questions": [], "focus_points": [], "interview_summary": "smoke"},
        "evidence_snippets": [],
        "ai_review_suggestion": {},
        "ai_review_status": "not_generated",
        "extract_info": {
            "file_name": "smoke_resume.txt",
            "method": "text",
            "quality": "ok",
            "message": "smoke test",
            "parse_status": "正常识别",
            "can_evaluate": True,
            "should_skip": False,
        },
        "manual_priority": "中",
        "review_id": "",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return row, detail


def run_smoke(*, cleanup: bool = True) -> dict[str, object]:
    load_env()
    init_db()

    smoke_job_title = f"SMOKE_FLOW_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
    candidate_id = f"smoke_cand_{uuid4().hex[:8]}"
    batch_id = ""
    steps: list[dict[str, str]] = []
    cleanup_notes: list[str] = []

    def _step(name: str, status: str, message: str) -> None:
        steps.append({"name": name, "status": status, "message": message})

    try:
        save_jd(
            smoke_job_title,
            _build_smoke_test_jd_text(),
            openings=1,
            created_by_user_id="smoke_runner",
            created_by_name="Smoke Runner",
            created_by_email="smoke@local.invalid",
            updated_by_user_id="smoke_runner",
            updated_by_name="Smoke Runner",
            updated_by_email="smoke@local.invalid",
        )
        if smoke_job_title not in list_jds():
            raise RuntimeError("test JD not visible in jobs list")
        _step("新建测试 JD", "pass", f"已创建测试岗位：{smoke_job_title}")

        row, detail = _build_smoke_candidate_payload(candidate_id)
        batch_id = save_candidate_batch(
            jd_title=smoke_job_title,
            rows=[row],
            details={candidate_id: detail},
            created_by_user_id="smoke_runner",
            created_by_name="Smoke Runner",
            created_by_email="smoke@local.invalid",
        )
        if not batch_id:
            raise RuntimeError("save_candidate_batch returned empty batch id")
        batches = list_batches_by_jd(smoke_job_title)
        if not any(str(item.get("batch_id") or "") == batch_id for item in batches):
            raise RuntimeError("test batch not visible in batch history")
        _step("创建最小批次", "pass", f"已创建测试批次：{batch_id[:12]}…")

        payload = load_batch(batch_id)
        if not payload:
            raise RuntimeError("workspace payload is empty")
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        if len(rows) != 1 or candidate_id not in details:
            raise RuntimeError("workspace payload missing expected candidate row/detail")
        if smoke_job_title not in list_jd_titles():
            raise RuntimeError("workspace JD title index missing test job")
        _step("读取候选人工作台", "pass", "候选人工作台数据读取正常。")
    except Exception as exc:  # noqa: BLE001
        _step("主流程 smoke", "fail", str(exc))
    finally:
        if cleanup:
            if batch_id:
                if delete_batch(batch_id):
                    cleanup_notes.append("已清理测试批次。")
                else:
                    cleanup_notes.append("测试批次清理失败或已不存在。")
            try:
                delete_jd(smoke_job_title)
                cleanup_notes.append("已清理测试岗位。")
            except Exception:  # noqa: BLE001
                cleanup_notes.append("测试岗位清理失败或已不存在。")

    success = all(step.get("status") == "pass" for step in steps) and bool(steps)
    return {
        "success": success,
        "steps": steps,
        "cleanup_notes": cleanup_notes,
        "artifacts": {"job_title": smoke_job_title, "batch_id": batch_id},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run minimal HireMate app flow smoke test.")
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep the temporary JD and batch for debugging.",
    )
    args = parser.parse_args()

    result = run_smoke(cleanup=not bool(args.keep_artifacts))
    for step in result.get("steps", []):
        status = str(step.get("status") or "fail").upper()
        print(f"[{status}] {step.get('name')}: {step.get('message')}")

    for note in result.get("cleanup_notes", []):
        print(f"[INFO] {note}")

    artifacts = result.get("artifacts", {}) if isinstance(result.get("artifacts"), dict) else {}
    print(
        "Summary:",
        "PASS" if result.get("success") else "FAIL",
        f"| job_title={artifacts.get('job_title') or '-'}",
        f"| batch_id={artifacts.get('batch_id') or '-'}",
    )
    return 0 if bool(result.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
