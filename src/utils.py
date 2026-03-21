"""通用工具模块。

模块职责：
- 管理本地 JSON 读写，便于保存输入与结果。
- 放置环境变量加载等通用方法。
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from dotenv import load_dotenv as _load_dotenv
except ModuleNotFoundError:
    _load_dotenv = None


def load_env() -> None:
    """尽力加载 .env；缺少 python-dotenv 时静默跳过。"""
    if _load_dotenv is None:
        return
    try:
        _load_dotenv()
    except Exception:
        return


def save_json(path: str | Path, payload: dict) -> None:
    """保存 JSON 数据到本地。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
