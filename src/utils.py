"""通用工具模块。

模块职责：
- 管理本地 JSON 读写，便于保存输入与结果。
- 放置环境变量加载等通用方法。
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv


def load_env() -> None:
    """加载 .env 环境变量。"""
    load_dotenv()


def save_json(path: str | Path, payload: dict) -> None:
    """保存 JSON 数据到本地。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
