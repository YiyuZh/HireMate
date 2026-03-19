"""最小流程串联脚本。

用途：
- 从本地样例文件读取 JD 与简历
- 串联解析、评分、风险、结论、面试建议模块
- 逐步打印结果，便于调试与演示
"""

from __future__ import annotations

from pathlib import Path
from pprint import pprint
import sys
import types

# 本地环境若未安装 python-dotenv，这里提供最小占位，避免导入 screener 时报错。
if "dotenv" not in sys.modules:
    dot = types.ModuleType("dotenv")
    dot.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dot

from src.interviewer import build_interview_plan
from src.jd_parser import parse_jd
from src.resume_parser import parse_resume
from src.risk_analyzer import analyze_risk
from src.scorer import score_candidate, to_score_values
from src.screener import build_screening_decision


def _read_text_or_raise(path: Path, label: str) -> str:
    """读取文本文件，不存在时给出友好提示。"""
    if not path.exists():
        raise FileNotFoundError(
            f"未找到{label}文件：{path}\n"
            f"请先创建该文件后重试。"
        )
    return path.read_text(encoding="utf-8")


def main() -> None:
    base = Path(__file__).resolve().parent
    jd_path = base / "data" / "jd_samples" / "jd_01.txt"
    resume_path = base / "data" / "resume_samples" / "resume_01.txt"

    try:
        jd_text = _read_text_or_raise(jd_path, "JD样例")
        resume_text = _read_text_or_raise(resume_path, "简历样例")
    except FileNotFoundError as exc:
        print("\n❌ 读取样例文件失败")
        print(exc)
        print("\n建议目录结构：")
        print("- data/jd_samples/jd_01.txt")
        print("- data/resume_samples/resume_01.txt")
        return

    print("\n=== Step 1: parse_jd ===")
    parsed_jd = parse_jd(jd_text)
    pprint(parsed_jd)

    print("\n=== Step 2: parse_resume ===")
    parsed_resume = parse_resume(resume_text)
    pprint(parsed_resume)

    print("\n=== Step 3: score_candidate ===")
    score_details = score_candidate(parsed_jd, parsed_resume)
    pprint(score_details)

    print("\n=== Step 4: analyze_risk ===")
    risk_result = analyze_risk(
        resume_data=parsed_resume,
        scores_input=score_details,
        resume_text=resume_text,
    )
    pprint(risk_result)

    print("\n=== Step 5: build_screening_decision ===")
    decision_result = build_screening_decision(
        scores_input=score_details,
        risk_level=risk_result.get("risk_level"),
        risks=risk_result.get("risk_points", []),
    )
    pprint(decision_result)

    print("\n=== Step 6: build_interview_plan ===")
    interview_plan = build_interview_plan(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        scores_input=score_details,
        risk_result=risk_result,
        screening_result=decision_result["screening_result"],
    )
    pprint(interview_plan)

    print("\n✅ Pipeline 运行完成")
    print("score_values:")
    pprint(to_score_values(score_details))


if __name__ == "__main__":
    main()
