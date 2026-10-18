"""推送状态：记录已推过的文章 uid 和时间，避免重复。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class SentState:
    """文件存储的去重表。{uid: ISO 时间戳}。"""

    def __init__(self, path: Path, retention_days: int = 30) -> None:
        self.path = path
        self.retention_days = retention_days
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.info("state file %s not found, starting fresh", self.path)
            return
        try:
            self._data = json.loads(self.path.read_text("utf-8"))
        except json.JSONDecodeError as e:
            # 坏 state 绝不能静默清零后继续跑：那会把旧新闻当新文章全量重推
            # （2026-10-17 合并冲突标记入库后就发生过一次）。修复/恢复后再运行。
            raise RuntimeError(
                f"state file {self.path} is corrupted ({e}); "
                "修复或恢复该文件后再运行，避免带着空去重历史全量重推"
            ) from e

    def contains(self, uid: str) -> bool:
        return uid in self._data

    def mark(self, uid: str) -> None:
        self._data[uid] = datetime.now(timezone.utc).isoformat()

    def prune(self) -> int:
        """删除超过 retention_days 的记录,返回删除条数。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        before = len(self._data)
        self._data = {
            uid: ts for uid, ts in self._data.items()
            if datetime.fromisoformat(ts) > cutoff
        }
        return before - len(self._data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")
        log.info("saved state: %d entries", len(self._data))


class EventHistory:
    """已推送事件的历史：标题 + event_key，用于跨次运行的“同一事件”去重。

    SentState 只按 URL 判重；同一事件下周换一家媒体、换一个标题再报道时
    URL 不同，会被当成新闻重新推送。这里额外记下推过的标题和事件键：
    - 新文章标题与历史标题高度相似 → 打分前直接丢弃(省 token)；
    - 终评后 event_key 与历史相同 → 不再推送。
    存储格式：[{"title": ..., "event_key": ..., "ts": ISO 时间}, ...]
    """

    def __init__(self, path: Path, retention_days: int = 21, title_threshold: float = 0.8) -> None:
        self.path = path
        self.retention_days = retention_days
        self.title_threshold = title_threshold
        self._entries: list[dict[str, str]] = []
        if path.exists():
            try:
                data = json.loads(path.read_text("utf-8"))
                self._entries = [e for e in data if isinstance(e, dict) and e.get("ts")]
            except json.JSONDecodeError as e:
                # 同 SentState：坏历史静默清零会导致已推事件被重复推送。
                raise RuntimeError(
                    f"event history {self.path} is corrupted ({e}); "
                    "修复或恢复该文件后再运行，避免重复推送已推事件"
                ) from e

    def prune(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        before = len(self._entries)
        self._entries = [e for e in self._entries if datetime.fromisoformat(e["ts"]) > cutoff]
        return before - len(self._entries)

    def event_keys(self) -> list[str]:
        """近期已推送的 event_key，按时间从新到旧、去重。"""
        keys = [e["event_key"] for e in reversed(self._entries) if e.get("event_key")]
        return list(dict.fromkeys(keys))

    def has_event_key(self, key: str) -> bool:
        return bool(key) and any(e.get("event_key") == key for e in self._entries)

    def matching_title(self, title: str) -> str | None:
        """返回与 title 高度相似的历史标题；没有则返回 None。"""
        from .sources import _is_similar, _normalize_title

        norm = _normalize_title(title)
        for entry in self._entries:
            old = entry.get("title") or ""
            if old and _is_similar(norm, _normalize_title(old), title, old, self.title_threshold):
                return old
        return None

    def add(self, title: str, event_key: str = "", ts: datetime | None = None) -> None:
        self._entries.append({
            "title": title,
            "event_key": event_key,
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
        })

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._entries, ensure_ascii=False, indent=2), "utf-8")
        log.info("saved event history: %d entries", len(self._entries))
