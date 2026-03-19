"""岗位评分模板（当前含 AI 产品经理精细模板）。"""

from __future__ import annotations

from typing import Any


DEFAULT_WEIGHTS: dict[str, float] = {
    "教育背景匹配度": 0.25,
    "相关经历匹配度": 0.25,
    "技能匹配度": 0.25,
    "表达完整度": 0.25,
}

DEFAULT_SCREENING_THRESHOLDS: dict[str, int] = {
    "pass_line": 4,
    "review_line": 3,
    "min_experience": 2,
    "min_skill": 2,
    "min_expression": 2,
}


AI_PM_PROFILE: dict[str, Any] = {
    "profile_name": "AI产品经理 / 大模型产品经理",
    "signals": ["ai产品", "大模型", "llm", "aigc", "agent", "rag", "prompt", "知识库"],
    "skill_focus_keywords": [
        "大模型", "llm", "prompt", "rag", "agent", "知识库", "模型评估", "数据分析", "prd", "原型", "用户研究"
    ],
    "experience_ai_keywords": ["大模型", "llm", "aigc", "agent", "rag", "prompt", "知识库"],
    "experience_method_keywords": ["需求分析", "prd", "产品方案", "原型", "原型设计", "用户研究"],
    "experience_eval_keywords": ["模型评估", "评估", "数据分析", "迭代", "优化", "实验", "ab测试", "a/b测试"],
    "hard_cap_when_generic_only": 3,
}




GENERAL_PM_PROFILE: dict[str, Any] = {
    "profile_name": "通用产品经理 / 用户产品经理",
    "signals": ["产品经理", "用户产品", "prd", "原型", "竞品分析", "需求分析", "用户研究"],
    "skill_focus_keywords": [
        "需求分析", "用户需求", "prd", "需求文档", "原型", "axure", "figma", "用户研究", "竞品分析", "数据分析"
    ],
    "experience_ai_keywords": [],
    "experience_method_keywords": ["需求分析", "prd", "原型", "用户研究", "竞品", "产品方案", "需求拆解"],
    "experience_eval_keywords": ["数据分析", "指标", "转化", "留存", "复盘", "迭代", "优化", "ab测试", "a/b测试"],
    "hard_cap_when_generic_only": 3,
    "weights": {
        "教育背景匹配度": 0.18,
        "相关经历匹配度": 0.36,
        "技能匹配度": 0.30,
        "表达完整度": 0.16,
    },
    "hard_thresholds": {
        "require_pm_project_support": True,
        "require_prd_or_prototype": True,
    },
    "risk_focus": ["产品方法证据不足", "缺少可落地产品项目支撑", "数据意识与复盘能力偏弱"],
}
DATA_ANALYST_PROFILE: dict[str, Any] = {
    "profile_name": "数据分析师 / 数据分析实习生",
    "signals": ["数据分析师", "数据分析实习生", "数据分析", "指标体系", "a/b测试", "ab测试", "报表", "可视化"],
    "skill_focus_keywords": ["sql", "python", "excel", "指标体系", "a/b测试", "ab测试", "数据分析", "报表", "可视化"],
    "experience_ai_keywords": [],
    "experience_method_keywords": ["数据分析", "业务分析", "指标体系", "报表", "可视化", "ab测试", "a/b测试"],
    "experience_eval_keywords": ["分析", "结论", "洞察", "优化", "迭代", "指标", "实验"],
    "hard_cap_when_generic_only": 3,
    "weights": {
        "教育背景匹配度": 0.16,
        "相关经历匹配度": 0.34,
        "技能匹配度": 0.34,
        "表达完整度": 0.16,
    },
    "hard_thresholds": {
        "require_sql": True,
        "require_data_project_support": True,
    },
    "risk_focus": ["技能缺少项目支撑", "指标分析经验不足", "数据结论表达空泛"],
}


USER_RESEARCH_PROFILE: dict[str, Any] = {
    "profile_name": "用户研究分析师",
    "signals": ["用户研究", "研究分析师", "用户访谈", "问卷设计", "可用性测试", "研究洞察", "研究报告"],
    "skill_focus_keywords": [
        "定性研究", "定量研究", "用户访谈", "问卷设计", "可用性测试", "洞察提炼", "研究报告", "研究项目"
    ],
    "experience_ai_keywords": [],
    "experience_method_keywords": ["定性研究", "定量研究", "用户访谈", "问卷设计", "可用性测试", "可用性", "用户研究"],
    "experience_eval_keywords": ["洞察", "研究结论", "建议", "研究报告", "分析", "样本", "方法"],
    "hard_cap_when_generic_only": 3,
    "weights": {
        "教育背景匹配度": 0.14,
        "相关经历匹配度": 0.36,
        "技能匹配度": 0.30,
        "表达完整度": 0.20,
    },
    "hard_thresholds": {
        "require_research_project_support": True,
        "market_activity_not_equal_research": True,
    },
    "risk_focus": ["方法论证据不足", "研究经历过于空泛", "缺少可验证研究产出"],
}


DEFAULT_PROFILE: dict[str, Any] = {
    "profile_name": "通用岗位模板",
    "signals": [],
    "skill_focus_keywords": [],
    "experience_ai_keywords": [],
    "experience_method_keywords": ["需求分析", "prd", "原型", "用户研究"],
    "experience_eval_keywords": ["数据分析", "优化", "迭代", "ab测试", "a/b测试"],
    "hard_cap_when_generic_only": 3,
}

PROFILE_REGISTRY: dict[str, dict[str, Any]] = {
    AI_PM_PROFILE["profile_name"]: AI_PM_PROFILE,
    GENERAL_PM_PROFILE["profile_name"]: GENERAL_PM_PROFILE,
    DATA_ANALYST_PROFILE["profile_name"]: DATA_ANALYST_PROFILE,
    USER_RESEARCH_PROFILE["profile_name"]: USER_RESEARCH_PROFILE,
    DEFAULT_PROFILE["profile_name"]: DEFAULT_PROFILE,
}


def get_profile_options() -> list[str]:
    return [
        "AI产品经理 / 大模型产品经理",
        "通用产品经理 / 用户产品经理",
        "数据分析师 / 数据分析实习生",
        "用户研究分析师",
        "通用岗位模板",
    ]


def get_profile_by_name(profile_name: str) -> dict[str, Any]:
    return PROFILE_REGISTRY.get((profile_name or "").strip(), DEFAULT_PROFILE)


def build_default_scoring_config(profile_name: str) -> dict[str, Any]:
    profile = get_profile_by_name(profile_name)
    weights = profile.get("weights") or DEFAULT_WEIGHTS
    hard_defaults = profile.get("hard_thresholds") or {}
    return {
        "profile_name": profile.get("profile_name", "通用岗位模板"),
        "role_template": profile.get("profile_name", "通用岗位模板"),
        "weights": dict(weights),
        "thresholds": dict(DEFAULT_SCREENING_THRESHOLDS),
        "screening_thresholds": dict(DEFAULT_SCREENING_THRESHOLDS),
        "hard_flags": {k: bool(v) for k, v in hard_defaults.items()},
        "hard_thresholds": {k: bool(v) for k, v in hard_defaults.items()},
        "risk_focus": list(profile.get("risk_focus") or []),
    }


def merge_scoring_config(profile: dict[str, Any], scoring_config: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """合并岗位自定义配置：岗位配置 > 角色模板默认 > 通用默认。"""
    cfg = scoring_config or {}
    profile_defaults = build_default_scoring_config(profile.get("profile_name", "通用岗位模板"))

    custom_weights = cfg.get("weights") if isinstance(cfg.get("weights"), dict) else {}
    custom_thresholds = cfg.get("screening_thresholds") if isinstance(cfg.get("screening_thresholds"), dict) else {}
    if not custom_thresholds and isinstance(cfg.get("thresholds"), dict):
        custom_thresholds = cfg.get("thresholds")
    custom_hard = cfg.get("hard_thresholds") if isinstance(cfg.get("hard_thresholds"), dict) else {}
    if not custom_hard and isinstance(cfg.get("hard_flags"), dict):
        custom_hard = cfg.get("hard_flags")

    merged = {
        "role_template": cfg.get("role_template") or cfg.get("profile_name") or profile_defaults.get("role_template"),
        "weights": {**profile_defaults["weights"], **custom_weights},
        "screening_thresholds": {**profile_defaults["screening_thresholds"], **custom_thresholds},
        "hard_thresholds": {**profile_defaults.get("hard_thresholds", {}), **custom_hard},
        "risk_focus": cfg.get("risk_focus") if isinstance(cfg.get("risk_focus"), list) else profile_defaults.get("risk_focus", []),
    }
    merged["profile_name"] = merged["role_template"]

    custom_used = any([
        bool(custom_weights),
        bool(custom_thresholds),
        bool(custom_hard),
        bool(cfg.get("risk_focus")),
        bool(cfg.get("role_template") or cfg.get("profile_name")),
    ])
    return merged, custom_used


def detect_role_profile(parsed_jd: dict[str, Any]) -> dict[str, Any]:
    """根据 JD 识别评分模板；当前优先识别 AI 产品经理模板。"""
    blob = " ".join(
        [
            str(parsed_jd.get("job_title") or ""),
            str(parsed_jd.get("major_preference") or ""),
            " ".join(parsed_jd.get("required_skills") or []),
            " ".join(parsed_jd.get("bonus_skills") or []),
        ]
    ).lower().replace(" ", "")

    candidates = [AI_PM_PROFILE, GENERAL_PM_PROFILE, DATA_ANALYST_PROFILE, USER_RESEARCH_PROFILE]
    best = DEFAULT_PROFILE
    best_hit = 0
    for profile in candidates:
        hit = sum(1 for sig in profile.get("signals", []) if str(sig).lower().replace(" ", "") in blob)
        if hit > best_hit:
            best = profile
            best_hit = hit
    if best_hit > 0:
        return best
    return DEFAULT_PROFILE
