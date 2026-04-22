from __future__ import annotations

import unittest

from src.ai_reviewer import _validate_grounded_output, run_ai_reviewer


class AiReviewerGuardrailTests(unittest.TestCase):
    def test_manual_first_gate_returns_conservative_output(self) -> None:
        output = run_ai_reviewer(
            parsed_jd={"job_title": "数据分析师"},
            parsed_resume={"name": "候选人A"},
            role_profile={"profile_name": "数据分析"},
            scoring_config={
                "ai_reviewer": {
                    "enable_ai_reviewer": True,
                    "ai_reviewer_mode": "suggest_only",
                    "provider": "mock",
                    "model": "mock-reviewer",
                }
            },
            score_details={},
            risk_result={"risk_level": "medium"},
            screening_result={"screening_result": "建议人工复核"},
            evidence_snippets=[],
            analysis_payload={"analysis_mode": "manual_first", "abstain_reasons": ["weak_text"]},
        )

        self.assertEqual(output["recommended_action"], "manual_review")
        self.assertEqual(output["recommended_action_detail"]["support_status"], "missing_evidence")
        self.assertTrue(output["recommended_action_detail"]["needs_manual_check"])
        self.assertEqual(output["score_adjustments"], [])
        self.assertIn("manual_first", output["abstain_reasons"])

    def test_invalid_or_conflicted_grounding_is_downgraded(self) -> None:
        validated = _validate_grounded_output(
            {
                "enabled": True,
                "mode": "suggest_only",
                "review_summary": "建议推进，但需要看证据约束是否通过。",
                "evidence_updates": [],
                "timeline_updates": [],
                "score_adjustments": [
                    {
                        "dimension": "相关经历匹配度",
                        "suggested_delta": 1,
                        "max_delta": 1,
                        "reason": "想上调分数",
                        "current_score": 3,
                        "support_status": "supported",
                        "supporting_evidence_ids": ["ev_missing"],
                        "opposing_evidence_ids": [],
                        "grounded_confidence": 0.8,
                        "needs_manual_check": False,
                    }
                ],
                "risk_adjustment": {
                    "reason": "",
                    "support_status": "missing_evidence",
                    "supporting_evidence_ids": [],
                    "opposing_evidence_ids": [],
                    "grounded_confidence": 0.0,
                    "needs_manual_check": True,
                },
                "recommended_action": "proceed",
                "recommended_action_detail": {
                    "reason": "存在一条支持证据，但同时有一条反证。",
                    "support_status": "supported",
                    "supporting_evidence_ids": ["ev_for"],
                    "opposing_evidence_ids": ["ev_against"],
                    "grounded_confidence": 0.72,
                    "needs_manual_check": False,
                },
                "abstain_reasons": [],
                "meta": {},
            },
            analysis_payload={
                "analysis_mode": "normal",
                "evidence_trace": [
                    {"evidence_id": "ev_for", "text": "有方法和产出证据"},
                    {"evidence_id": "ev_against", "text": "存在反证，结果量化不足"},
                ],
            },
            evidence_snippets=[],
        )

        self.assertEqual(validated["recommended_action"], "manual_review")
        self.assertEqual(validated["recommended_action_detail"]["support_status"], "contradicted")
        self.assertEqual(validated["score_adjustments"][0]["suggested_delta"], 0)
        self.assertEqual(validated["score_adjustments"][0]["support_status"], "missing_evidence")
        self.assertIn("counter_evidence_present", validated["abstain_reasons"])


if __name__ == "__main__":
    unittest.main()
