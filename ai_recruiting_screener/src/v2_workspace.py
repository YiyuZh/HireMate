"""V2 工作台辅助函数（批量简历审核）。"""

from __future__ import annotations

import csv
import io

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "unknown": 3}
DECISION_ORDER = {"推荐进入下一轮": 0, "建议人工复核": 1, "暂不推荐": 2, "": 3}
PRIORITY_ORDER = {"低": 0, "普通": 1, "中": 2, "高": 3, "": 1}


def decode_uploaded_txt(file_obj) -> str:
    """读取上传的 txt 文本（优先 utf-8，失败回退 gbk）。"""
    raw = file_obj.getvalue()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore")


def build_candidate_row(result: dict, source_name: str, index: int) -> dict:
    """从单份评估结果提取列表行数据。"""
    parsed_resume = result.get("parsed_resume", {})
    score_details = result.get("score_details", {})
    fallback_name = f"候选人{index + 1}"

    risk_result = result.get("risk_result", {})
    risk_points = risk_result.get("risk_points", []) or []
    risk_summary = (risk_result.get("risk_summary") or "").strip()
    if not risk_summary and risk_points:
        risk_summary = str(risk_points[0])

    return {
        "candidate_id": f"cand_{index + 1}",
        "姓名": parsed_resume.get("name") or fallback_name,
        "文件名": source_name,
        "教育背景匹配度": score_details.get("教育背景匹配度", {}).get("score", "-"),
        "相关经历匹配度": score_details.get("相关经历匹配度", {}).get("score", "-"),
        "技能匹配度": score_details.get("技能匹配度", {}).get("score", "-"),
        "风险等级": risk_result.get("risk_level", "unknown"),
        "风险摘要": risk_summary,
        "初筛结论": result.get("screening_result", {}).get("screening_result", ""),
    }


def filter_by_decision(rows: list[dict], decision_filter: str) -> list[dict]:
    """按初筛结论筛选候选人列表。"""
    if decision_filter == "全部":
        return rows
    return [row for row in rows if row.get("初筛结论") == decision_filter]


def filter_by_risk(rows: list[dict], risk_filter: str) -> list[dict]:
    """按风险等级筛选候选人列表。"""
    if risk_filter == "全部":
        return rows
    return [row for row in rows if (row.get("风险等级") or "unknown") == risk_filter]


def search_by_name(rows: list[dict], keyword: str) -> list[dict]:
    """按候选人姓名关键词搜索（大小写不敏感）。"""
    key = (keyword or "").strip().lower()
    if not key:
        return rows
    return [row for row in rows if key in str(row.get("姓名", "")).lower()]


def _score_to_number(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def sort_rows(rows: list[dict], sort_field: str, descending: bool = True) -> list[dict]:
    """按指定字段排序。"""
    if sort_field in {"教育背景匹配度", "相关经历匹配度", "技能匹配度"}:
        return sorted(rows, key=lambda r: _score_to_number(r.get(sort_field)), reverse=descending)

    if sort_field == "风险等级":
        return sorted(rows, key=lambda r: RISK_ORDER.get((r.get("风险等级") or "unknown"), 99), reverse=descending)

    if sort_field == "初筛结论":
        return sorted(rows, key=lambda r: DECISION_ORDER.get(r.get("初筛结论", ""), 99), reverse=descending)

    if sort_field in {"处理优先级", "处理优先级（高到低）", "处理优先级（低到高）"}:
        reverse = descending
        if sort_field == "处理优先级（低到高）":
            reverse = False
        elif sort_field == "处理优先级（高到低）":
            reverse = True
        return sorted(rows, key=lambda r: PRIORITY_ORDER.get(str(r.get("处理优先级") or "普通"), 1), reverse=reverse)

    return rows


def rows_to_csv_bytes(rows: list[dict]) -> bytes:
    """将当前候选人列表导出为 CSV（二进制）。"""
    output = io.StringIO()
    fieldnames = ["姓名", "文件名", "处理优先级", "提取方式", "提取质量", "提取说明", "教育背景匹配度", "相关经历匹配度", "技能匹配度", "风险等级", "风险摘要", "审核摘要", "初筛结论"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in fieldnames})
    return output.getvalue().encode("utf-8-sig")
