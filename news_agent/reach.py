"""Bounded, read-only Agent Reach discovery using the category's existing queries."""
import json
import logging
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .sources import Article, _parse_date, dedupe

log = logging.getLogger(__name__)


def fetch(queries: list[str], config: dict) -> list[Article]:
    if not config.get("enabled", False):
        return []
    executable = shutil.which("agent-reach")
    if not executable:
        local = Path.home() / ".local/bin/agent-reach"
        executable = str(local) if local.is_file() else None
    if not executable:
        log.warning("Agent Reach unavailable; install it to enable extended discovery")
        return []
    timeout = min(60, max(1, int(config.get("timeout_seconds", 20))))
    limit = min(10, max(1, int(config.get("max_per_query", 5))))
    articles = []
    for query in list(dict.fromkeys(queries))[:max(0, int(config.get("max_queries", 8)))]:
        try:
            response = subprocess.run(
                [executable, "web", "search", query, "--limit", str(limit),
                 "--timeout", str(timeout)], capture_output=True, text=True,
                timeout=timeout + 5, check=False,
            )
            payload = json.loads(response.stdout)
            if not isinstance(payload, dict):
                raise ValueError("expected JSON object")
            if not payload.get("ok"):
                error = payload.get("error") or {}
                if not isinstance(error, dict):
                    error = {}
                log.warning("Agent Reach search stopped: %s", error.get("code", "unknown"))
                break  # Do not retry login, rate limits or platform blocks.
            if response.returncode != 0:
                log.warning("Agent Reach exited with status %s", response.returncode)
                break
            items = payload.get("items") or []
            if not isinstance(items, list):
                raise ValueError("expected items array")
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                url, title = item.get("url"), item.get("title")
                if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
                    continue
                if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
                    continue
                text = item.get("text") or ""
                if not isinstance(text, str):
                    text = ""
                articles.append(Article(
                    title=title, url=url, summary=text[:600],
                    source="Agent Reach / " + urlparse(url).netloc,
                    published_at=_parse_date({"published": item.get("published_at")}),
                    source_tier="discovery",
                ))
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            log.warning("Agent Reach search failed; keeping collected sources")
            break
    return dedupe(articles)
