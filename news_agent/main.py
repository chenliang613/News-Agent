"""News Agent 编排入口：按周排期 → 拉源 → 去重 → DeepSeek 打分 → top N 摘要 → PushPlus。

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
from datetime import datetime, timezone
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
WECHAT_LIST_PATH = ROOT / "WeChat and website list.md"
WECHAT_FEED_CACHE_PATH = ROOT / "state" / "wechat_feeds.json"

# 微信公众号板块:不在 filter 的四个打分板块里,走单独的"全部摘要"流程
WECHAT_CATEGORY = "wechat"

# 落盘文件名用的中文板块名(按用户要求:industry → "AI产业",不是 "AI 行业落地")
FILE_CATEGORY_NAMES = {
    "governance": "AI治理",
    "data": "AI数据",
    "industry": "AI产业",
    "agent": "AIAgent",
    "wechat": "微信公众号",
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
    show_score: bool = True,
) -> str:
    """单板块渲染。打分板块按分数倒序;公众号(show_score=False)保持传入顺序、不显示相关度。"""
    items_sorted = sorted(items, key=lambda s: s.score, reverse=True) if show_score else items

    lines: list[str] = []
    lines.append(f"# 今日 {category_label}：共 {len(items_sorted)} 条")
    lines.append("")

    for idx, item in enumerate(items_sorted, start=1):
        a = item.article
        lines.append(f"### {idx}. {a.title}")
        lines.append("")
        if show_score:
            lines.append(f"**来源**: {a.source}  |  **相关度**: {item.score:.1f}/10")
        else:
            meta = f"**公众号**: {a.source}"
            if a.published_at:
                meta += f"  |  **发布**: {a.published_at.strftime('%Y-%m-%d')}"
            lines.append(meta)
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

    is_wechat = category == WECHAT_CATEGORY
    if not is_wechat and category not in filter_mod.VALID_CATEGORIES:
        log.error(
            "invalid category %r (must be one of %s)",
            category, sorted(filter_mod.VALID_CATEGORIES) + [WECHAT_CATEGORY],
        )
        return 1

    category_labels = config.get("category_labels") or {}
    category_label = category_labels.get(category, category)

    today_queries: list[str] = []
    if not is_wechat:
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

    # 2. 采集 + 3. 时间窗口过滤
    log.info("=== 1. fetching sources for category=%s ===", category)
    raw: list[sources.Article] = []
    if is_wechat:
        # 读 "WeChat and website list.md":含两类源 → 微信公众号(48h) + 官网链接(24h)
        entries = sources.parse_account_list(WECHAT_LIST_PATH)
        if not entries:
            log.info("no entries in %s; nothing to do", WECHAT_LIST_PATH.name)
            return 0
        n_wechat = sum(1 for e in entries if e["kind"] == "wechat")
        n_site = sum(1 for e in entries if e["kind"] == "website")
        log.info("loaded %d entries (%d 公众号, %d 官网)", len(entries), n_wechat, n_site)

        wconf = config.get("wechat") or {}
        opml_urls = (wconf.get("resolver") or {}).get("opml_urls") or []
        resolved = sources.resolve_wechat_feeds(
            entries, opml_urls, config["rsshub"]["instance"], WECHAT_FEED_CACHE_PATH,
        )
        if not resolved:
            log.info("no feeds resolved; nothing to do")
            return 0

        # "只要近 N 小时内发布":默认丢弃无发布时间的条目(官网首页/微信链接等单页抓取)
        drop_undated = wconf.get("require_published", True)
        wechat_age = wconf.get("max_age_hours") or 48
        site_age = wconf.get("website_max_age_hours") or 24

        wechat_raw = sources.fetch_wechat_articles([r for r in resolved if r["kind"] == "wechat"])
        site_raw = sources.fetch_wechat_articles([r for r in resolved if r["kind"] == "website"])
        raw = wechat_raw + site_raw
        log.info("fetched %d raw articles (%d 公众号, %d 官网)", len(raw), len(wechat_raw), len(site_raw))

        fresh_in_window = (
            sources.filter_by_age(wechat_raw, wechat_age, drop_undated=drop_undated)
            + sources.filter_by_age(site_raw, site_age, drop_undated=drop_undated)
        )
        log.info("after age filter (公众号<=%dh, 官网<=%dh): %d articles", wechat_age, site_age, len(fresh_in_window))
    else:
        # 只抓当日板块的 Google News queries;RSS 维持全量,由 scorer 判定
        raw.extend(sources.fetch_rss_sources(config["rss_feeds"]))
        raw.extend(sources.fetch_google_news(today_queries))
        raw.extend(sources.fetch_rsshub(
            config["rsshub"]["instance"],
            config["rsshub"].get("routes", []),
        ))
        log.info("fetched %d raw articles", len(raw))
        max_age = config.get("sources", {}).get("max_age_hours")
        fresh_in_window = sources.filter_by_age(raw, max_age)
        if max_age:
            log.info("after age filter (<=%dh): %d articles", max_age, len(fresh_in_window))
    if max_age:
        log.info("after age filter (<=%dh): %d articles", max_age, len(fresh_in_window))

    # 4. 去重(URL 去重 + 标题相似度跨源去重 + 历史已推送去重)
    deduped = sources.dedupe(fresh_in_window)
    deduped = sources.dedupe_by_content(deduped)
    fresh = [a for a in deduped if not state.contains(a.uid)]
    log.info("after dedup: %d unique, %d new (vs sent history)", len(deduped), len(fresh))

    if not fresh:
        log.info("no new articles; exiting")
        return 0

    # 5. 选出要推送的文章 + 写摘要
    if is_wechat:
        # 公众号是手动精选的源,不做相关性打分:按发布时间取最新 max_n 条,全部写摘要
        max_n = int((config.get("wechat") or {}).get("max_articles") or config["push"]["max_articles"])
        fresh.sort(
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        top = [
            filter_mod.ScoredArticle(article=a, score=0.0, reason="", categories=[category])
            for a in fresh[:max_n]
        ]
        log.info(
            "=== 2. summarizing %d WeChat articles with %s ===",
            len(top), config["deepseek"]["summarizer_model"],
        )
        top = filter_mod.summarize_top(
            client=client,
            scored=top,
            keywords_md=keywords_md,
            model=config["deepseek"]["summarizer_model"],
        )
        # 公众号只标记真正推过的,溢出的(超过 max_n)下次还能再出
        to_mark = top
    else:
        # 5a. DeepSeek 打分(聚焦当日板块,其他板块的稿子直接被压到 0-3 分)
        log.info("=== 2. scoring with %s (focus=%s) ===", config["deepseek"]["scorer_model"], category)
        scored = filter_mod.score_articles(
            client=client,
            articles=fresh,
            keywords_md=keywords_md,
            model=config["deepseek"]["scorer_model"],
            batch_size=config["deepseek"]["scorer_batch_size"],
            active_category=category,
        )

        # 5b. 按阈值过滤 + top N(还要求文章命中当日板块,scorer 已经做了限制,这里再兜底)
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

        # 5c. DeepSeek 写摘要
        log.info("=== 3. summarizing top %d with %s ===", len(top), config["deepseek"]["summarizer_model"])
        top = filter_mod.summarize_top(
            client=client,
            scored=top,
            keywords_md=keywords_md,
            model=config["deepseek"]["summarizer_model"],
        )
        # 标记所有打过分的,避免下次反复打同样低分
        to_mark = scored

    # 8. 渲染 + 落盘 + 推送
    now = datetime.now()
    title = config["push"]["title_template"].format(
        date=now.strftime("%Y-%m-%d"),
        category_label=category_label,
        category=category,
    )
    content = render_push_content(top, category, category_label, show_score=not is_wechat)

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

    # 9. 标记已处理(打分板块标记全部打过分的;公众号只标记推过的)
    for s in to_mark:
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
        help="只发一条测试消息到 PushPlus(不抓新闻、不调 DeepSeek),验证微信能否收到",
    )
    parser.add_argument(
        "--category",
        choices=sorted(filter_mod.VALID_CATEGORIES) + [WECHAT_CATEGORY],
        help="手动指定板块(忽略 weekday 排期),含 wechat=微信公众号",
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
