"""News Agent 编排入口：按周排期 → 拉源 → 去重 → Claude 打分 → top N 摘要 → PushPlus。

每天只跑一个板块(周一治理 / 周二数据 / 周三行业落地),周四到周日跳过。
排期表写在 config.yaml 的 schedule.weekday_category。

用法:
    python -m news_agent.main                       # 按今天的 weekday 跑
    python -m news_agent.main --dry-run             # 不实际推送,只打印输出
    python -m news_agent.main --category governance # 手动指定板块(忽略 weekday)
    python -m news_agent.main --weekday 0           # 模拟周一(0=周一..6=周日)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import openai
import yaml

from . import filter as filter_mod
from . import push as push_mod
from . import sources
from .state import SentState

ROOT = Path(__file__).resolve().parent.parent
KEYWORDS_PATH = ROOT / "keywords.md"
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "state" / "sent.json"
OUTPUT_DIR = ROOT / "output"

# 落盘文件名用的中文板块名(按用户要求:industry → "AI产业",不是 "AI 行业落地")
FILE_CATEGORY_NAMES = {
    "governance": "AI治理",
    "data": "AI数据",
    "industry": "AI产业",
    "agent": "AIAgent",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("news_agent")


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text("utf-8"))


def resolve_category(config: dict, weekday: int) -> str | None:
    """根据 weekday(0=Mon..6=Sun) 从 config.schedule.weekday_category 取板块 key。

    YAML 里的 key 可能是 int 也可能是 str,两种都兼容。
    """
    table = (config.get("schedule") or {}).get("weekday_category") or {}
    if weekday in table:
        return table[weekday]
    if str(weekday) in table:
        return table[str(weekday)]
    return None


def render_push_content(
    items: list[filter_mod.ScoredArticle],
    category: str,
    category_label: str,
) -> str:
    """单板块渲染。items 按分数倒序排列。"""
    items_sorted = sorted(items, key=lambda s: s.score, reverse=True)

    lines: list[str] = []
    lines.append(f"# 今日 {category_label}：共 {len(items_sorted)} 条")
    lines.append("")

    for idx, item in enumerate(items_sorted, start=1):
        a = item.article
        lines.append(f"### {idx}. {a.title}")
        lines.append("")
        lines.append(f"**来源**: {a.source}  |  **相关度**: {item.score:.1f}/10")
        if item.summary:
            lines.append("")
            lines.append(item.summary)
        elif a.summary:
            lines.append("")
            lines.append(a.summary[:200] + ("…" if len(a.summary) > 200 else ""))
        lines.append("")
        lines.append(f"[阅读原文]({a.url})")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def run(
    dry_run: bool = False,
    override_category: str | None = None,
    override_weekday: int | None = None,
) -> int:
    config = load_config()
    keywords_md = KEYWORDS_PATH.read_text("utf-8")

    # 1. 决定今天跑哪个板块
    if override_category:
        category = override_category
        log.info("category overridden via CLI: %s", category)
    else:
        weekday = override_weekday if override_weekday is not None else datetime.now().weekday()
        category = resolve_category(config, weekday)
        if not category:
            log.info("weekday=%d not in schedule.weekday_category; skip today", weekday)
            return 0
        log.info("weekday=%d → category=%s", weekday, category)

    if category not in filter_mod.VALID_CATEGORIES:
        log.error("invalid category %r (must be one of %s)", category, sorted(filter_mod.VALID_CATEGORIES))
        return 1

    category_labels = config.get("category_labels") or {}
    category_label = category_labels.get(category, category)

    queries_by_cat = config.get("google_news_queries") or {}
    today_queries = queries_by_cat.get(category) or []
    if not today_queries:
        log.warning("no google_news_queries for category=%s; only RSS will be used", category)

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not deepseek_key:
        log.error("DEEPSEEK_API_KEY not set")
        return 1
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN")
    if not pushplus_token and not dry_run:
        log.error("PUSHPLUS_TOKEN not set (use --dry-run to skip push)")
        return 1

    client = openai.OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
    state = SentState(STATE_PATH, retention_days=config["state"]["retention_days"])
    pruned = state.prune()
    if pruned:
        log.info("pruned %d expired state entries", pruned)

    # 2. 采集(只抓当日板块的 Google News queries;RSS 维持全量,由 scorer 判定)
    log.info("=== 1. fetching sources for category=%s ===", category)
    raw: list[sources.Article] = []
    raw.extend(sources.fetch_rss_sources(config["rss_feeds"]))
    raw.extend(sources.fetch_google_news(today_queries))
    raw.extend(sources.fetch_rsshub(
        config["rsshub"]["instance"],
        config["rsshub"].get("routes", []),
    ))
    log.info("fetched %d raw articles", len(raw))

    # 3. 时间窗口过滤
    max_age = config.get("sources", {}).get("max_age_hours")
    fresh_in_window = sources.filter_by_age(raw, max_age)
    if max_age:
        log.info("after age filter (<=%dh): %d articles", max_age, len(fresh_in_window))

    # 4. 去重(源内 + 跨源 + 历史)
    deduped = sources.dedupe(fresh_in_window)
    fresh = [a for a in deduped if not state.contains(a.uid)]
    log.info("after dedup: %d unique, %d new (vs sent history)", len(deduped), len(fresh))

    if not fresh:
        log.info("no new articles; exiting")
        return 0

    # 5. Gemini 打分(聚焦当日板块,其他板块的稿子直接被压到 0-3 分)
    log.info("=== 2. scoring with %s (focus=%s) ===", config["deepseek"]["scorer_model"], category)
    scored = filter_mod.score_articles(
        client=client,
        articles=fresh,
        keywords_md=keywords_md,
        model=config["deepseek"]["scorer_model"],
        batch_size=config["deepseek"]["scorer_batch_size"],
        active_category=category,
    )

    # 6. 按阈值过滤 + top N(还要求文章命中当日板块,scorer 已经做了限制,这里再兜底)
    min_score = float(config["push"]["min_score"])
    max_n = int(config["push"]["max_articles"])
    qualified = [
        s for s in scored
        if s.score >= min_score and category in s.categories
    ]
    qualified.sort(key=lambda s: s.score, reverse=True)
    top = qualified[:max_n]
    log.info(
        "scored: %d total, %d >= %.1f & in [%s], top %d selected",
        len(scored), len(qualified), min_score, category, len(top),
    )

    if not top:
        log.info("nothing meets threshold; exiting without push")
        # 仍然把"已看过"的 uid 标记上,避免下次重打分浪费 token
        for s in scored:
            state.mark(s.article.uid)
        if not dry_run:
            state.save()
        return 0

    # 7. Gemini 写摘要
    log.info("=== 3. summarizing top %d with %s ===", len(top), config["deepseek"]["summarizer_model"])
    top = filter_mod.summarize_top(
        client=client,
        scored=top,
        keywords_md=keywords_md,
        model=config["deepseek"]["summarizer_model"],
    )

    # 8. 渲染 + 落盘 + 推送
    now = datetime.now()
    title = config["push"]["title_template"].format(
        date=now.strftime("%Y-%m-%d"),
        category_label=category_label,
        category=category,
    )
    content = render_push_content(top, category, category_label)

    # 无条件落盘:dry-run 也写,没有 PUSHPLUS_TOKEN 也写,推送失败也写
    file_name = FILE_CATEGORY_NAMES.get(category, category)
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    out_path = OUTPUT_DIR / f"{file_name}+{timestamp}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(f"# {title}\n\n{content}", encoding="utf-8")
    log.info("wrote markdown file: %s", out_path.relative_to(ROOT))

    if dry_run:
        log.info("=== DRY RUN: skip push ===")
        print(f"\nTITLE: {title}\n")
        print(content)
    else:
        log.info("=== 4. pushing to PushPlus ===")
        ok = push_mod.push(pushplus_token, title, content)
        if not ok:
            log.error("push failed; not marking state so we can retry next run")
            return 2

    # 9. 标记所有"打过分的"为已处理(不止 top,避免下次反复打同样低分)
    for s in scored:
        state.mark(s.article.uid)
    if not dry_run:
        state.save()
    log.info("done")
    return 0


def test_push() -> int:
    """绕过抓取/打分,直接发一条测试消息到 PushPlus,验证微信端是否能收到。"""
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        log.error("PUSHPLUS_TOKEN not set")
        return 1
    now = datetime.now()
    title = f"🧪 News Agent 测试 · {now.strftime('%H:%M:%S')}"
    content = (
        "# News Agent 推送测试\n\n"
        "如果你在微信上看到了这条消息,说明 PushPlus token 和公众号关注都正常。\n\n"
        f"- 时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- Token 末 4 位: ...{token[-4:]}\n"
        f"- 模板: markdown\n"
    )
    log.info("sending test push: %s", title)
    ok = push_mod.push(token, title, content)
    if ok:
        log.info("PushPlus API 返回成功。请检查微信公众号「推送加」是否收到消息。")
        log.info("如果 API 成功但微信没收到,通常是:1) 没关注「推送加」公众号 2) token 属于别人")
        return 0
    log.error("PushPlus API 调用失败,看上面日志")
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(description="News Agent runner")
    parser.add_argument("--dry-run", action="store_true", help="不实际推送,只打印结果")
    parser.add_argument(
        "--test-push",
        action="store_true",
        help="只发一条测试消息到 PushPlus(不抓新闻、不调 Claude),验证微信能否收到",
    )
    parser.add_argument(
        "--category",
        choices=sorted(filter_mod.VALID_CATEGORIES),
        help="手动指定板块(忽略 weekday 排期)",
    )
    parser.add_argument(
        "--weekday",
        type=int,
        choices=range(7),
        help="模拟某个 weekday(0=周一..6=周日),用于测试",
    )
    args = parser.parse_args()
    if args.test_push:
        sys.exit(test_push())
    sys.exit(run(
        dry_run=args.dry_run,
        override_category=args.category,
        override_weekday=args.weekday,
    ))


if __name__ == "__main__":
    main()
