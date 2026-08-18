"""供自建调用方触发新闻任务的 HTTP webhook。

使用方法:
    python -m news_agent.webhook                 # 默认监听 0.0.0.0:8088
    python -m news_agent.webhook --port 9000     # 自定义端口

支持的自定义 POST 内容:
    {"content": "AI治理"} → governance 板块
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from .main import load_config, run

log = logging.getLogger("news_agent.webhook")

_running: set[str] = set()
_lock = threading.Lock()


def _normalize(text: str) -> str:
    return text.replace(" ", "").lower()


def _build_keyword_map() -> dict[str, str]:
    """从 config.yaml 的 category_labels 反向构建: 展示名 → 板块key。"""
    config = load_config()
    labels = config.get("category_labels") or {}
    mapping: dict[str, str] = {}
    for cat_key, label in labels.items():
        mapping[_normalize(label)] = cat_key
    return mapping


def _match_category(content: str, keyword_map: dict[str, str]) -> str | None:
    normalized = _normalize(content)
    if normalized in keyword_map:
        return keyword_map[normalized]
    for kw, cat in keyword_map.items():
        if kw in normalized:
            return cat
    return None


def _run_task(category: str) -> None:
    try:
        run(override_category=category)
    except Exception:
        log.exception("task %s failed", category)
    finally:
        with _lock:
            _running.discard(category)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._reply(400, "invalid JSON")
            return

        content = (data.get("content") or "").strip()
        log.info("received webhook: %r", content)

        if _normalize(content) in ("帮助", "help", "?", "？"):
            config = load_config()
            labels = config.get("category_labels") or {}
            hint = "\n".join(f"  {label}" for label in labels.values())
            self._reply(200, "ok", f"支持的关键词：\n{hint}")
            return

        keyword_map = _build_keyword_map()
        category = _match_category(content, keyword_map)

        if not category:
            config = load_config()
            labels = config.get("category_labels") or {}
            supported = "、".join(labels.values())
            self._reply(200, "ok", f"未识别的关键词。\n支持：{supported}\n发送「帮助」查看详情")
            return

        with _lock:
            if category in _running:
                self._reply(200, "ok", f"该板块正在推送中，请稍候...")
                return
            _running.add(category)

        config = load_config()
        label = (config.get("category_labels") or {}).get(category, category)

        thread = threading.Thread(target=_run_task, args=(category,), daemon=True)
        thread.start()
        log.info("triggered task: %s (%s)", category, label)

        self._reply(200, "ok", f"已触发【{label}】新闻推送，预计 2-3 分钟后推送结果")

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("News Agent webhook is running\n".encode())

    def _reply(self, code: int, msg: str, content: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        resp: dict = {"code": code, "msg": msg}
        if content:
            resp["data"] = content
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())

    def log_message(self, fmt, *args) -> None:
        log.info(fmt, *args)


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="PushPlus webhook server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8088, help="监听端口")
    args = parser.parse_args()

    keyword_map = _build_keyword_map()
    config = load_config()
    labels = config.get("category_labels") or {}
    log.info("支持关键词: %s", ", ".join(labels.values()))

    server = HTTPServer((args.host, args.port), WebhookHandler)
    log.info("webhook server listening on %s:%d", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
