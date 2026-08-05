"""逐篇新闻反馈的存储、链接生成与来源偏好摘要。"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

RATING_ALIASES = {"有价值": 5, "一般": 3, "无关": 1}


def record_feedback(
    path: Path, score: int, url: str, *, source: str = "", category: str = "", title: str = "",
) -> bool:
    if not 1 <= score <= 5 or not url.startswith(("http://", "https://")):
        return False
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else []
        if not isinstance(data, list):
            data = []
    except (OSError, json.JSONDecodeError):
        data = []
    data.append({
        "score": score, "url": url, "source": source[:120], "category": category,
        "title": title[:300], "at": datetime.now(timezone.utc).isoformat(),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data[-1000:], ensure_ascii=False, indent=2), "utf-8")
    return True


def feedback_links(base_url: str, *, url: str, source: str, category: str, title: str) -> str:
    """生成 PushPlus Markdown 反馈链接；未配置公网地址时给出可复制命令。"""
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return f"反馈：回复 `反馈 1-5 {url}`（1=无关，5=很有价值）"
    links = []
    for score in range(1, 6):
        query = urlencode({"score": score, "url": url, "source": source, "category": category, "title": title})
        links.append(f"[{score}分]({base_url}/?{query})")
    return "反馈：" + " · ".join(links) + "（1=无关，5=很有价值）"


def feedback_profile(path: Path, category: str, retention_days: int = 90) -> str:
    """将近期逐篇评分汇总为模型可执行的来源偏好，不对单次偶然评分过拟合。"""
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    grouped: dict[str, list[int]] = defaultdict(list)
    for item in data if isinstance(data, list) else []:
        if item.get("category") not in ("", category) or not item.get("source"):
            continue
        try:
            at = datetime.fromisoformat(item["at"])
            score = int(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if at >= cutoff and 1 <= score <= 5:
            grouped[str(item["source"])].append(score)
    preferences = []
    for source, scores in grouped.items():
        if len(scores) >= 2:
            average = sum(scores) / len(scores)
            if average >= 4.0 or average <= 2.0:
                preferences.append(f"{source}：{average:.1f}/5（{len(scores)} 次）")
    if not preferences:
        return ""
    return "用户近期来源偏好（仅作轻度加/降权，仍以正文事实为准）：" + "；".join(preferences[:10])
