"""Hermes 微信适配器的零 LLM 新闻指令路由器。

Hermes 收到文本消息后，可将原文作为第一个参数调用本模块：
    python -m news_agent.hermes_command 'AI治理'

本模块只匹配三个固定关键词并调用本机 webhook；不会调用任何模型。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

COMMANDS = {
    "ai治理": "AI治理",
    "ai数据": "AI数据",
    "ai行业": "AI行业",
}


def resolve_command(message: str) -> str | None:
    """仅接受完整关键词；允许空格或换行，避免普通对话误触发。"""
    return COMMANDS.get(re.sub(r"\s+", "", message or "").lower())


def trigger(message: str, webhook_url: str, secret: str) -> tuple[bool, str]:
    command = resolve_command(message)
    if not command:
        return False, ""
    payload = json.dumps({"content": command, "secret": secret}, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "X-News-Agent-Secret": secret},
    )
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310: configured local webhook URL
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return True, f"任务触发失败：{exc}"
    return True, str(data.get("data") or data.get("msg") or "任务已触发")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes fixed-command → News Agent webhook")
    parser.add_argument("message", help="Hermes 收到的原始文本")
    args = parser.parse_args()
    handled, reply = trigger(
        args.message,
        os.environ.get("NEWS_AGENT_WEBHOOK_URL", "http://127.0.0.1:8088"),
        os.environ.get("NEWS_AGENT_WEBHOOK_SECRET", ""),
    )
    if not handled:
        sys.exit(1)
    print(reply)


if __name__ == "__main__":
    main()
