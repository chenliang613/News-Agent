"""用户质量反馈的轻量持久化；用于后续调整关键词、来源和阈值。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

VALID_RATINGS = {"有价值", "一般", "无关"}


def record_feedback(path: Path, rating: str, url: str) -> bool:
    if rating not in VALID_RATINGS or not url.startswith(("http://", "https://")):
        return False
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else []
        if not isinstance(data, list):
            data = []
    except (OSError, json.JSONDecodeError):
        data = []
    data.append({"rating": rating, "url": url, "at": datetime.now(timezone.utc).isoformat()})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data[-1000:], ensure_ascii=False, indent=2), "utf-8")
    return True
