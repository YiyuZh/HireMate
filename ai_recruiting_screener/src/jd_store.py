"""轻量 JD 存储模块（JSON 本地版）。

提供基础能力：
- save_jd(title, jd_text)
- list_jds()
- load_jd(title)
- update_jd(title, jd_text)
- delete_jd(title)

扩展能力：
- list_jd_records()  # 返回岗位库展示所需元数据
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "jd_store.json"


def _ensure_store_file() -> None:
    """确保存储文件存在。"""
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        STORE_PATH.write_text("{}", encoding="utf-8")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _default_scoring_config() -> dict:
    return {
        "profile_name": "AI产品经理 / 大模型产品经理",
        "weights": {
            "教育背景匹配度": 0.18,
            "相关经历匹配度": 0.32,
            "技能匹配度": 0.30,
            "表达完整度": 0.20,
        },
        "thresholds": {
            "pass_line": 4,
            "review_line": 3,
            "min_experience": 2,
            "min_skill": 2,
            "min_expression": 2,
        },
        "hard_flags": {},
    }


def _normalize_record(raw) -> dict:
    """兼容旧格式（title -> text）与新格式（title -> {text, updated_at, openings}）。"""
    if isinstance(raw, dict):
        text = str(raw.get("text", "")).strip()
        updated_at = str(raw.get("updated_at", "")).strip() or "-"
        try:
            openings = max(0, int(raw.get("openings", 0) or 0))
        except (TypeError, ValueError):
            openings = 0
        scoring = raw.get("scoring_config")
        if not isinstance(scoring, dict):
            scoring = _default_scoring_config()
        return {"text": text, "updated_at": updated_at, "openings": openings, "scoring_config": scoring}

    # 旧版本：值可能是字符串
    return {"text": str(raw or "").strip(), "updated_at": "-", "openings": 0, "scoring_config": _default_scoring_config()}


def _read_store() -> dict[str, dict]:
    """读取 JSON 存储，异常时回退为空字典。"""
    _ensure_store_file()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8") or "{}")
        if isinstance(data, dict):
            normalized: dict[str, dict[str, str | int]] = {}
            for key, value in data.items():
                normalized[str(key)] = _normalize_record(value)
            return normalized
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _write_store(data: dict[str, dict]) -> None:
    """写入 JSON 存储。"""
    _ensure_store_file()
    STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_jd(title: str, jd_text: str, openings: int = 0) -> None:
    """保存或更新一条 JD。"""
    clean_title = (title or "").strip()
    clean_text = (jd_text or "").strip()

    if not clean_title:
        raise ValueError("JD 标题不能为空")
    if not clean_text:
        raise ValueError("JD 文本不能为空")

    data = _read_store()
    existing = _normalize_record(data.get(clean_title, {}))
    if openings is None:
        openings_num = int(existing.get("openings", 0) or 0)
    else:
        try:
            openings_num = max(0, int(openings or 0))
        except (TypeError, ValueError):
            openings_num = int(existing.get("openings", 0) or 0)

    data[clean_title] = {
        "text": clean_text,
        "updated_at": _now_str(),
        "openings": openings_num,
        "scoring_config": existing.get("scoring_config") or _default_scoring_config(),
    }
    _write_store(data)


def list_jds() -> list[str]:
    """返回所有已保存 JD 标题。"""
    return sorted(_read_store().keys())


def list_jd_records() -> list[dict[str, str | int]]:
    """返回岗位库展示所需记录列表。"""
    records = []
    for title in list_jds():
        item = _read_store().get(title, {"text": "", "updated_at": "-"})
        records.append(
            {
                "title": title,
                "text": item.get("text", ""),
                "updated_at": item.get("updated_at", "-") or "-",
                "openings": int(item.get("openings", 0) or 0),
                "scoring_config": item.get("scoring_config") or _default_scoring_config(),
            }
        )
    return records


def load_jd(title: str) -> str:
    """按标题加载 JD 文本，不存在时返回空字符串。"""
    item = _read_store().get((title or "").strip(), {})
    return str(item.get("text", ""))


def update_jd(title: str, jd_text: str, openings: int | None = None) -> None:
    """更新指定标题 JD（不存在时抛错）。"""
    clean_title = (title or "").strip()
    clean_text = (jd_text or "").strip()

    if not clean_title:
        raise ValueError("JD 标题不能为空")
    if not clean_text:
        raise ValueError("JD 文本不能为空")

    data = _read_store()
    if clean_title not in data:
        raise ValueError("JD 不存在，无法更新")

    existing = _normalize_record(data.get(clean_title, {}))
    if openings is None:
        openings_num = int(existing.get("openings", 0) or 0)
    else:
        try:
            openings_num = max(0, int(openings or 0))
        except (TypeError, ValueError):
            openings_num = int(existing.get("openings", 0) or 0)

    data[clean_title] = {
        "text": clean_text,
        "updated_at": _now_str(),
        "openings": openings_num,
        "scoring_config": existing.get("scoring_config") or _default_scoring_config(),
    }
    _write_store(data)


def upsert_jd_openings(title: str, openings: int) -> None:
    """仅更新岗位空缺人数（不存在时抛错）。"""
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空")

    data = _read_store()
    if clean_title not in data:
        raise ValueError("JD 不存在，无法更新空缺人数")

    item = _normalize_record(data[clean_title])
    try:
        openings_num = max(0, int(openings or 0))
    except (TypeError, ValueError):
        openings_num = int(item.get("openings", 0) or 0)

    data[clean_title] = {
        "text": str(item.get("text", "")),
        "updated_at": _now_str(),
        "openings": openings_num,
        "scoring_config": item.get("scoring_config") or _default_scoring_config(),
    }
    _write_store(data)


def load_jd_scoring_config(title: str) -> dict:
    item = _read_store().get((title or "").strip(), {})
    return item.get("scoring_config") or _default_scoring_config()


def upsert_jd_scoring_config(title: str, scoring_config: dict) -> None:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空")
    data = _read_store()
    if clean_title not in data:
        raise ValueError("JD 不存在，无法更新评分设置")
    item = _normalize_record(data[clean_title])
    item["scoring_config"] = scoring_config if isinstance(scoring_config, dict) else _default_scoring_config()
    item["updated_at"] = _now_str()
    data[clean_title] = item
    _write_store(data)


def delete_jd(title: str) -> None:
    """删除指定标题 JD（不存在时抛错）。"""
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("JD 标题不能为空")

    data = _read_store()
    if clean_title not in data:
        raise ValueError("JD 不存在，无法删除")

    del data[clean_title]
    _write_store(data)


if __name__ == "__main__":
    demo_title = "示例JD-产品经理实习生"
    demo_text = "岗位职责：参与需求分析、PRD撰写、跨团队协作。"

    save_jd(demo_title, demo_text)
    print("已保存标题：", list_jds())
    print("岗位记录：", list_jd_records())
    print("加载内容：", load_jd(demo_title))
    update_jd(demo_title, "岗位职责：更新后的JD内容")
    print("更新后内容：", load_jd(demo_title))
    delete_jd(demo_title)
    print("删除后标题：", list_jds())
