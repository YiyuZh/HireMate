"""JD 解析模块（规则增强版）。

目标：
- 输入 AI 产品经理实习生岗位 JD 文本。
- 输出结构化 dict，供后续 scorer.py 使用。

设计原则：
- 规则优先，不依赖复杂模型。
- 字段缺失时返回空字符串或空列表，不抛错。
- required_skills / bonus_skills 采用“区块优先、全文兜底”。
"""

from __future__ import annotations

import re
from typing import Pattern


def _clean_text(text: str) -> str:
    """轻量清洗：保留换行，便于区块抽取。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t ]+", " ", ln).strip() for ln in raw.split("\n")]
    return "\n".join(lines).strip()


def _extract_first(text: str, patterns: list[Pattern[str] | str]) -> str:
    """返回第一个命中值；若无命中返回空字符串。"""
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue

        value = m.group(1) if m.lastindex else m.group(0)
        return value.strip(" ：:;；。,.，\n")

    return ""


def _extract_keywords(text: str, candidates: list[str]) -> list[str]:
    """按候选关键词抽取，去重并保持顺序。"""
    found: list[str] = []
    for word in candidates:
        if re.search(re.escape(word), text, flags=re.IGNORECASE):
            found.append(word)
    return list(dict.fromkeys(found))


def _extract_section_block(text: str, header_keywords: list[str]) -> str:
    """提取指定区块内容：从标题行开始，到下一个常见标题前结束。"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""

    common_headers = [
        "岗位职责",
        "职责描述",
        "任职要求",
        "岗位要求",
        "职位要求",
        "加分项",
        "优先条件",
        "学历要求",
        "专业要求",
        "实习要求",
    ]

    start_idx = -1
    for i, ln in enumerate(lines):
        if any(k in ln for k in header_keywords):
            start_idx = i
            break
    if start_idx == -1:
        return ""

    block_lines: list[str] = []

    # 支持“标题与内容同一行”的写法，例如：任职要求：熟悉 SQL、Python
    header_line = lines[start_idx]
    inline = re.split(r"[：:]", header_line, maxsplit=1)
    if len(inline) == 2 and inline[1].strip():
        block_lines.append(inline[1].strip())

    for j in range(start_idx + 1, len(lines)):
        ln = lines[j]
        if any(h in ln for h in common_headers) and not any(k in ln for k in header_keywords):
            break
        block_lines.append(ln)

    return "\n".join(block_lines).strip()


def parse_jd(jd_text: str) -> dict[str, str | list[str]]:
    """解析 JD 文本并返回结构化 dict（稳定输出 key）。"""
    text = _clean_text(jd_text)

    # 1) 岗位名称：优先解析“职位名称/岗位名称”，否则匹配常见岗位词
    job_title = _extract_first(
        text,
        [
            r"(?:职位名称|岗位名称|招聘岗位|岗位)\s*[：:]\s*(.+?)(?=(?:学历要求|学历|岗位职责|任职要求|加分项|实习要求)\s*[：:]|$)",
            r"(AI\s*产品经理\s*实习生)",
            r"(产品经理\s*实习生)",
        ],
    )

    # 2) 学历要求：优先“学历要求”区块，否则抓取常见学历门槛词
    degree_requirement = _extract_first(
        text,
        [
            r"(?:学历要求|学历)\s*[：:]\s*([^。；;\n]+)",
            r"(本科及以上|硕士及以上|本科以上|硕士以上|本科|硕士)",
        ],
    )

    # 3) 专业偏好：优先“专业要求”区块，否则抓取“XX相关专业”
    major_preference = _extract_first(
        text,
        [
            r"(?:专业要求|专业优先|专业背景)\s*[：:]\s*([^。；;\n]+)",
            r"((?:计算机|软件工程|人工智能|数据科学|统计学|信息管理(?:与信息系统)?)[^。；;\n]*相关专业)",
        ],
    )

    required_skill_candidates = [
        "SQL",
        "Python",
        "Excel",
        "数据分析",
        "指标分析",
        "A/B 测试",
        "A/B测试",
        "PRD",
        "Axure",
        "Figma",
        "用户研究",
        "需求分析",
        "需求拆解",
        "LLM",
        "大模型",
        "Prompt",
        "跨团队协作",
        "沟通协作",
    ]

    bonus_skill_candidates = [
        "AI 产品经验",
        "大模型项目经验",
        "NLP",
        "机器学习",
        "竞赛获奖",
        "英文文档",
        "英语",
        "创业经历",
    ]

    # 4) 必备技能 required_skills：优先“任职要求/岗位要求/职位要求”区块
    required_block = _extract_section_block(text, ["任职要求", "岗位要求", "职位要求"])
    required_skills = _extract_keywords(required_block, required_skill_candidates) if required_block else []

    # 5) 加分技能 bonus_skills：优先“加分项/优先条件”区块
    bonus_block = _extract_section_block(text, ["加分项", "优先条件"])
    bonus_skills = _extract_keywords(bonus_block, bonus_skill_candidates) if bonus_block else []

    # 兜底逻辑：仅区块不存在时，才回退到全文关键词抽取
    if not required_block:
        required_skills = _extract_keywords(text, required_skill_candidates)
    if not bonus_block:
        bonus_skills = _extract_keywords(text, bonus_skill_candidates)

    # 为减少混入：若区块存在，做一次交叉去重（加分项优先保留在 bonus）
    if required_block and bonus_block:
        required_skills = [s for s in required_skills if s not in bonus_skills]

    # 6) 实习要求：优先“实习要求”字段，否则抓取“每周X天/连续X个月”
    internship_requirement = _extract_first(
        text,
        [
            r"(?:实习要求|实习时长|到岗要求|每周到岗|到岗天数)\s*[：:]\s*([^。；;\n]+)",
            r"(每周\s*\d+\s*天[^。；;\n]*)",
            r"(连续\s*\d+\s*(?:个月|月)[^。；;\n]*)",
        ],
    )

    # 7) 胜任力要求：从全文抽取通用软技能关键词
    competency_candidates = [
        "产品思维",
        "结构化思维",
        "逻辑能力",
        "沟通能力",
        "学习能力",
        "执行力",
        "自驱力",
        "责任心",
        "团队协作",
        "抗压能力",
    ]
    competency_requirements = _extract_keywords(text, competency_candidates)

    return {
        "job_title": job_title,
        "degree_requirement": degree_requirement,
        "major_preference": major_preference,
        "required_skills": required_skills,
        "bonus_skills": bonus_skills,
        "internship_requirement": internship_requirement,
        "competency_requirements": competency_requirements,
    }


if __name__ == "__main__":
    # 本地测试示例：
    # cd ai_recruiting_screener
    # python src/jd_parser.py
    sample_jd = """
    职位名称：AI 产品经理实习生
    学历要求：本科及以上，计算机/数据科学等相关专业优先。
    岗位职责：参与需求分析、PRD 撰写，和研发/设计协作推动功能上线。
    任职要求：熟悉 SQL、Python、数据分析，具备产品思维和结构化思维。
    加分项：有大模型项目经验，能阅读英文文档。
    实习要求：每周到岗 4 天，连续 3 个月。
    """

    from pprint import pprint

    pprint(parse_jd(sample_jd))
