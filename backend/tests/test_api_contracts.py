from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core.deps import get_current_user, verify_csrf
from backend.main import app


ADMIN_USER = {
    "user_id": "user_admin",
    "email": "admin@example.com",
    "name": "Admin",
    "is_active": True,
    "is_admin": True,
    "created_at": "",
    "updated_at": "",
    "last_login_at": "",
}

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
        app.dependency_overrides[verify_csrf] = lambda: None
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def assert_no_cjk_keys(self, payload) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.assertFalse(CJK_RE.search(str(key)), f"Unexpected legacy key: {key}")
                self.assert_no_cjk_keys(value)
        elif isinstance(payload, list):
            for item in payload:
                self.assert_no_cjk_keys(item)

    def test_auth_me_contract(self) -> None:
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("user_id", data)
        self.assertIn("email", data)
        self.assertIn("is_admin", data)
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.jobs.job_service.list_jobs")
    def test_jobs_contract(self, mock_list_jobs) -> None:
        mock_list_jobs.return_value = [
            {
                "title": "AI 产品经理",
                "jd_text": "负责 AI 产品设计与上线复盘。",
                "openings": 2,
                "updated_at": "2026-04-22 12:00:00",
                "created_by_name": "Admin",
                "created_by_email": "admin@example.com",
                "scoring_config": {
                    "ai_reviewer": {"enable_ai_reviewer": True, "provider": "openai", "model": "gpt-4o-mini"}
                },
                "latest_batch": {
                    "batch_id": "batch_1",
                    "jd_title": "AI 产品经理",
                    "created_at": "2026-04-22 12:00:00",
                    "total_resumes": 8,
                    "pass_count": 2,
                    "review_count": 4,
                    "reject_count": 2,
                },
            }
        ]
        response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertIn("title", data[0])
        self.assertIn("latest_batch", data[0])
        self.assertIn("ai_defaults", data[0])
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.jobs.job_service.get_job_detail")
    def test_job_detail_contract(self, mock_get_job_detail) -> None:
        mock_get_job_detail.return_value = {
            "title": "AI 产品经理",
            "jd_text": "完整 JD",
            "openings": 1,
            "scoring_config": {
                "weights": {
                    "教育背景匹配度": 0.2,
                    "相关经历匹配度": 0.4,
                    "技能匹配度": 0.25,
                    "表达完整度": 0.15,
                },
                "ai_reviewer": {"enable_ai_reviewer": True, "provider": "deepseek", "model": "deepseek-chat"},
            },
            "batches": [],
        }
        response = self.client.get("/api/jobs/AI%20%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("jd_text", data)
        self.assertIn("scoring_config", data)
        self.assertIn("ai_defaults", data)
        self.assert_no_cjk_keys({key: value for key, value in data.items() if key != "scoring_config"})

    @patch("backend.api.routes.screening.screening_service.preview_files")
    def test_precheck_contract(self, mock_preview_files) -> None:
        mock_preview_files.return_value = [
            {
                "文件名": "resume.pdf",
                "提取方式": "pdf_text",
                "提取质量": "正常",
                "提取说明": "文本提取成功",
                "解析状态": "正常识别",
                "是否可进入批量初筛": "是",
            }
        ]
        response = self.client.post(
            "/api/screening/precheck",
            files=[("files", ("resume.pdf", b"demo", "application/pdf"))],
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data[0]["file_name"], "resume.pdf")
        self.assertIn("can_enter_batch_screening", data[0])
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.screening.screening_service.create_batch")
    def test_create_batch_contract(self, mock_create_batch) -> None:
        mock_create_batch.return_value = {
            "batch_id": "batch_1",
            "summary": {
                "success_count": 1,
                "failed_files": [],
                "skipped_files": [],
                "weak_files": [],
                "ocr_missing_files": [],
            },
            "batch": {
                "batch_id": "batch_1",
                "jd_title": "AI 产品经理",
                "created_at": "2026-04-22 12:00:00",
                "total_resumes": 1,
                "pass_count": 0,
                "review_count": 1,
                "reject_count": 0,
            },
            "batch_ai_reviewer_runtime": {
                "enable_ai_reviewer": True,
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_base": "https://api.deepseek.com/v1",
                "api_key_mode": "direct_input",
                "api_key_env_name": "DEEPSEEK_API_KEY",
                "auto_generate_for_new_batch": False,
            },
        }
        response = self.client.post(
            "/api/batches",
            data={
                "jd_title": "AI 产品经理",
                "jd_text": "JD text",
                "runtime_config_json": "{}",
                "force_allow_weak": "false",
            },
            files=[("files", ("resume.pdf", b"demo", "application/pdf"))],
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("batch_id", data)
        self.assertIn("summary", data)
        self.assertIn("runtime_config", data)
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.screening.test_runtime_connection")
    def test_connection_contract(self, mock_test_runtime_connection) -> None:
        mock_test_runtime_connection.return_value = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_base": "https://api.deepseek.com/v1",
            "api_key_env_name": "DEEPSEEK_API_KEY",
            "api_key_mode": "direct_input",
            "api_key_mode_label": "直接输入 API Key",
            "api_key_present": True,
            "api_key_env_detected": False,
            "success": True,
            "reason": "connection ok",
            "message": "connection ok",
            "request_id": "req_1",
            "purpose": "batch_runtime",
            "phase": "network_probe",
            "category": "success",
            "source": "api",
            "validation_ms": 12,
            "network_ms": 320,
            "latency_ms": 332,
        }
        response = self.client.post(
            "/api/screening/ai/test-connection",
            json={
                "runtime_config": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "api_base": "https://api.deepseek.com/v1",
                    "api_key_mode": "direct_input",
                    "api_key_value": "sk-demo",
                },
                "purpose": "batch_runtime",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("phase", data)
        self.assertIn("category", data)
        self.assertIn("latency_ms", data)
        self.assertIn("validation_ms", data)
        self.assertIn("network_ms", data)
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.workbench.workbench_service.get_workbench")
    def test_workbench_contract(self, mock_get_workbench) -> None:
        mock_get_workbench.return_value = {
            "batch": {
                "batch_id": "batch_1",
                "jd_title": "AI 产品经理",
                "created_at": "2026-04-22 12:00:00",
                "total_resumes": 2,
                "pass_count": 0,
                "review_count": 2,
                "reject_count": 0,
            },
            "rows": [
                {
                    "candidate_id": "cand_1",
                    "姓名": "候选人1",
                    "文件名": "resume.pdf",
                    "候选池": "待复核候选人",
                    "初筛结论": "建议人工复核",
                    "人工最终结论": "未处理",
                    "处理优先级": "高",
                    "解析状态": "正常识别",
                    "审核摘要": "建议人工复核，需重点确认项目产出。",
                    "风险等级": "medium",
                }
            ],
            "details": {
                "cand_1": {
                    "extract_info": {"method": "pdf_text", "quality": "ok", "message": "提取成功"},
                    "manual_decision": "未处理",
                    "manual_priority": "高",
                    "is_locked_effective": False,
                }
            },
        }
        response = self.client.get("/api/workbench?batch_id=batch_1&pool=pending_review&quick_filter=all&risk=all&sort=priority_desc")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("batch_summary", data)
        self.assertIn("rows", data)
        self.assertIn("name", data["rows"][0])
        self.assertIn("lock_state", data["rows"][0])
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.workbench.workbench_service.get_candidate_detail")
    def test_candidate_detail_contract(self, mock_get_candidate_detail) -> None:
        mock_get_candidate_detail.return_value = {
            "row": {
                "candidate_id": "cand_1",
                "姓名": "候选人1",
                "文件名": "resume.pdf",
                "候选池": "待复核候选人",
                "初筛结论": "建议人工复核",
                "人工最终结论": "待复核",
                "处理优先级": "高",
                "解析状态": "正常识别",
                "审核摘要": "项目经历较强，但需要进一步核验结果真实性。",
                "风险等级": "medium",
            },
            "detail": {
                "parsed_resume": {"name": "候选人1"},
                "extract_info": {"file_name": "resume.pdf", "method": "pdf_text", "quality": "ok", "message": "提取成功"},
                "analysis_payload": {
                    "analysis_mode": "normal",
                    "ocr_confidence": 0.9,
                    "structure_confidence": 0.8,
                    "parse_confidence": 0.82,
                    "grounding_summary": {
                        "jd_semantic_anchors": [{"source": "jd", "text": "SQL / Python / 指标分析"}],
                        "positive_evidence": [{"source": "evidence", "text": "SQL 和 Python 分析经历"}],
                        "counter_evidence": [{"source": "evidence", "text": "缺少结果量化"}],
                        "missing_evidence": [{"source": "rubric", "text": "未提供完整的毕业时间"}],
                        "historical_case_grounding": [],
                        "risk_case_grounding": [],
                    },
                    "evidence_trace": [
                        {
                            "evidence_id": "ev_1",
                            "source": "project",
                            "text": "使用 SQL 和 Python 完成指标分析",
                            "support_status": "supported",
                            "grounded_confidence": 0.88,
                            "needs_manual_check": False,
                        }
                    ],
                    "claim_candidates": [
                        {
                            "claim": "具备数据分析方法设计能力",
                            "supporting_evidence_ids": ["ev_1"],
                        }
                    ],
                    "abstain_reasons": ["counter_evidence_present"],
                    "candidate_profile": {"education_summary": "本科，计算机相关专业", "skill_inventory": ["Python", "SQL"]},
                    "evidence_for": [{"source": "项目", "text": "使用 SQL 和 Python 完成指标分析"}],
                    "evidence_against": [{"source": "风险", "text": "缺少结果量化"}],
                    "missing_info_points": [{"source": "缺失", "text": "缺少毕业时间"}],
                    "timeline_risks": [{"source": "时间线", "text": "一段实习结束时间不明"}],
                },
                "risk_result": {
                    "risk_level": "medium",
                    "risk_summary": "结果量化证据不足",
                    "risk_points": ["项目结果量化不足"],
                },
                "screening_result": {"screening_reasons": ["相关经历较强，但结果证据不足"]},
                "evidence_snippets": [{"source": "项目", "text": "负责数据分析并输出结论"}],
                "manual_note": "需要重点问结果归因",
                "manual_priority": "高",
                "manual_decision": "待复核",
                "ai_review_status": "ready",
                "ai_source": "stub",
                "ai_model": "mock-reviewer",
                "ai_generated_at": "2026-04-22 12:30:00",
                "ai_applied_actions": ["evidence"],
                "ai_review_suggestion": {
                    "review_summary": "建议围绕结果归因补充面试。",
                    "score_adjustments": [
                        {
                            "dimension": "相关经历匹配度",
                            "suggested_delta": 1,
                            "max_delta": 1,
                            "reason": "方法和产出信号较强",
                            "current_score": 3,
                            "support_status": "weakly_supported",
                            "supporting_evidence_ids": ["ev_1"],
                            "opposing_evidence_ids": [],
                            "grounded_confidence": 0.64,
                            "needs_manual_check": True,
                        }
                    ],
                    "risk_adjustment": {
                        "suggested_risk_level": "medium",
                        "reason": "结果量化不足",
                        "support_status": "contradicted",
                        "supporting_evidence_ids": [],
                        "opposing_evidence_ids": ["ev_1"],
                        "grounded_confidence": 0.3,
                        "needs_manual_check": True,
                    },
                    "recommended_action": "manual_review",
                    "recommended_action_detail": {
                        "reason": "存在反证，建议人工优先复核",
                        "support_status": "contradicted",
                        "supporting_evidence_ids": [],
                        "opposing_evidence_ids": ["ev_1"],
                        "grounded_confidence": 0.32,
                        "needs_manual_check": True,
                    },
                    "abstain_reasons": ["counter_evidence_present", "no_grounded_change"],
                    "interview_plan": {"interview_questions": ["请说明你如何验证分析结论？"], "focus_points": ["结果归因"]},
                },
                "is_locked_effective": False,
            },
            "batch": {
                "batch_id": "batch_1",
                "jd_title": "AI 产品经理",
                "created_at": "2026-04-22 12:00:00",
                "total_resumes": 2,
                "pass_count": 0,
                "review_count": 2,
                "reject_count": 0,
            },
        }
        response = self.client.get("/api/candidates/cand_1?batch_id=batch_1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("identity", data)
        self.assertIn("analysis", data)
        self.assertIn("evidence", data)
        self.assertIn("ai_review", data)
        self.assertIn("grounding_summary", data["analysis"])
        self.assertIn("evidence_trace", data["analysis"])
        self.assertIn("abstain_reasons", data["analysis"])
        self.assertIn("recommended_action_detail", data["ai_review"])
        self.assertIn("abstain_reasons", data["ai_review"])
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.admin.admin_service.get_system_health")
    def test_admin_health_contract(self, mock_get_system_health) -> None:
        mock_get_system_health.return_value = {
            "database": {"backend": "sqlite", "ok": True, "users_count": 2, "jobs_count": 3, "batches_count": 4},
            "ocr": {"image_ocr_available": True, "pdf_ocr_fallback_available": True},
            "latest_ai_call": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_base": "https://api.openai.com/v1",
                "source": "api",
                "reason": "",
                "env_detected": True,
            },
        }
        response = self.client.get("/api/admin/system-health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("database", data)
        self.assertIn("ocr", data)
        self.assertIn("latest_ai_call", data)
        self.assert_no_cjk_keys(data)

    @patch("backend.api.routes.admin.admin_service.get_admin_users")
    def test_admin_users_contract(self, mock_get_admin_users) -> None:
        mock_get_admin_users.return_value = [
            {
                "user_id": "user_1",
                "email": "a@example.com",
                "name": "Alice",
                "is_admin": True,
                "is_active": True,
                "created_at": "2026-04-20 10:00:00",
                "last_login_at": "2026-04-22 09:00:00",
            }
        ]
        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("user_id", data[0])
        self.assertIn("is_admin", data[0])
        self.assertIn("is_active", data[0])
        self.assert_no_cjk_keys(data)


if __name__ == "__main__":
    unittest.main()
