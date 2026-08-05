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
    trends: list[str] | None = None,
    watchlist: list[str] | None = None,
    run_stats: str = "",
) -> str:
    """单板块渲染。打分板块按分数倒序;公众号(show_score=False)保持传入顺序、不显示相关度。"""
    items_sorted = sorted(items, key=lambda s: s.score, reverse=True) if show_score else items

    lines: list[str] = []
    lines.append(f"# 今日 {category_label}：共 {len(items_sorted)} 条")
    lines.append("")
    if trends:
        lines.append("## 本周观察")
        lines.extend(f"- {trend}" for trend in trends)
        lines.append("")
    if watchlist:
        lines.append("## 持续跟踪")
        lines.extend(f"- {item}" for item in watchlist)
        lines.append("")

    for idx, item in enumerate(items_sorted, start=1):
        a = item.article
        lines.append(f"### {idx}. {a.title}")
        lines.append("")
        if show_score:
            lines.append(f"**来源**: {a.source}  |  **相关度**: {item.score:.1f}/10")
            lines.append(
                f"**研究价值**: 相关 {item.relevance:.1f} · 新颖 {item.novelty:.1f} "
                f"· 证据 {item.evidence:.1f} · 可行动 {item.actionability:.1f}"
            )
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
        if item.related_articles:
            refs = "、".join(
                f"[{related.source}]({related.url})" for related in item.related_articles[:3]
            )
            lines.append("")
            lines.append(f"同一事件参考：{refs}")
        lines.append("")
        lines.append("---")
        lines.append("")

    if run_stats:
        lines.extend(["## 运行摘要", run_stats, ""])
    return "\n".join(lines)


def notify_no_results(category_label: str, reason: str, token: str | None, dry_run: bool) -> None:
    """没有可推送事件时给出明确状态，避免采集失败被误认为“没有新闻”。"""
    title = f"{category_label}本次运行状态"
    content = f"# {category_label}本次未生成新闻简报\n\n原因：{reason}\n\n可在 Actions 日志查看各源抓取与筛选统计。"
    if dry_run:
        log.info("DRY RUN no-result notice: %s", reason)
    elif token:
        push_mod.push(token, title, content)


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

    # 4. 去重(URL 去重 + 标题相似度跨源去重 + 历史已推送去重)
    deduped = sources.dedupe(fresh_in_window)
    deduped = sources.dedupe_by_content(deduped)
    fresh = [a for a in deduped if not state.contains(a.uid)]
    log.info("after dedup: %d unique, %d new (vs sent history)", len(deduped), len(fresh))

    if not fresh:
        log.info("no new articles; exiting")
        notify_no_results(category_label, "采集结果均已在历史推送中处理", pushplus_token, dry_run)
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
        # 5a. 第一阶段：仅用标题/RSS 摘要粗筛，控制后续正文抓取和终评成本。
        log.info("=== 2. coarse scoring with %s (focus=%s) ===", config["deepseek"]["scorer_model"], category)
        scored = filter_mod.score_articles(
            client=client,
            articles=fresh,
            keywords_md=keywords_md,
            model=config["deepseek"]["scorer_model"],
            batch_size=config["deepseek"]["scorer_batch_size"],
            active_category=category,
        )

        research_conf = config.get("research_filter") or {}
        coarse_min_score = float(research_conf.get("coarse_min_score", 4.0))
        max_candidates = int(research_conf.get("max_body_candidates", 60))
        candidates = [
            s for s in scored
            if s.score >= coarse_min_score and category in s.categories
        ]
        candidates.sort(key=lambda s: s.score, reverse=True)
        candidates = candidates[:max_candidates]
        log.info(
            "coarse scored: %d total, %d candidates >= %.1f; fetching bodies for %d",
            len(scored), len(candidates), coarse_min_score, len(candidates),
        )
        if not candidates:
            log.info("nothing passed coarse filter; exiting without push")
            for s in scored:
                state.mark(s.article.uid)
            if not dry_run:
                state.save()
            notify_no_results(category_label, "没有新闻通过标题与摘要粗筛", pushplus_token, dry_run)
            return 0

        sources.fetch_article_bodies(
            [s.article for s in candidates],
            max_workers=int(research_conf.get("body_fetch_workers", 8)),
            max_chars=int(research_conf.get("body_max_chars", 6000)),
        )

        # 正文去重必须在补抓原文之后再做：不同标题的转载稿常有完全相同的正文。
        # 这里关闭标题判重，仅按正文 4-gram 指纹比较，避免把“标题相近但内容不同”的新闻误删。
        before_body_dedup = len(candidates)
        body_unique = sources.dedupe_by_content(
            [s.article for s in candidates],
            threshold=1.1,
            body_threshold=float(research_conf.get("body_dedupe_threshold", 0.90)),
        )
        by_uid = {s.article.uid: s for s in candidates}
        candidates = [by_uid[a.uid] for a in body_unique]
        if len(candidates) < before_body_dedup:
            log.info("full-body dedup: %d → %d candidates", before_body_dedup, len(candidates))

        # 5b. 第二阶段：结合正文，按研究价值四维终评并产生事件键。
        log.info("=== 3. research-value assessment with %s ===", config["deepseek"]["scorer_model"])
        assessed = filter_mod.assess_research_value(
            client=client,
            scored=candidates,
            keywords_md=keywords_md,
            model=config["deepseek"]["scorer_model"],
            active_category=category,
            batch_size=int(research_conf.get("assessment_batch_size", 10)),
        )

        # 5c. 按终评分过滤；相同事件合并成一张卡片，保留证据最强的主报道。
        min_score = float(config["push"]["min_score"])
        max_n = int(config["push"]["max_articles"])
        qualified = [
            s for s in assessed
            if s.score >= min_score
        ]
        event_cards = filter_mod.group_by_event(qualified)
        top = event_cards[:max_n]
        log.info(
            "final assessed: %d candidates, %d >= %.1f, %d event cards, top %d selected",
            len(assessed), len(qualified), min_score, len(event_cards), len(top),
        )

        if not top:
            log.info("nothing meets threshold; exiting without push")
            # 仍然把"已看过"的 uid 标记上,避免下次重打分浪费 token
            for s in scored:
                state.mark(s.article.uid)
            if not dry_run:
                state.save()
            notify_no_results(category_label, "候选新闻未达到研究价值阈值", pushplus_token, dry_run)
            return 0

        # 5c-2. 语义去重：为 event_key 不一致的同一事件再做一层兜底。
        #       不补位——去掉重复后名额不再用其他文章填补。
        before = len(top)
        top = filter_mod.dedupe_semantic(
            client=client,
            scored=top,
            model=config["deepseek"]["scorer_model"],
        )
        if len(top) < before:
            log.info("semantic dedup: %d → %d (no backfill)", before, len(top))

        # 5d. DeepSeek 写摘要
        log.info("=== 4. summarizing top %d with %s ===", len(top), config["deepseek"]["summarizer_model"])
        top = filter_mod.summarize_top(
            client=client,
            scored=top,
            keywords_md=keywords_md,
            model=config["deepseek"]["summarizer_model"],
        )
        # 标记所有打过分的,避免下次反复打同样低分
        to_mark = scored

    # 6. 生成事件级周度观察；失败不会影响新闻推送。
    trends: list[str] = []
    watchlist: list[str] = []
    if (config.get("insights") or {}).get("enabled", True):
        trends, watchlist = filter_mod.summarize_weekly_insights(
            client, top, config["deepseek"]["summarizer_model"],
        )

    run_stats = (
        f"- 采集 {len(raw)} 篇；时间窗口内 {len(fresh_in_window)} 篇；"
        f"URL/内容去重后 {len(deduped)} 篇；历史新增 {len(fresh)} 篇。\n"
        f"- 最终推送 {len(top)} 个事件。"
    )

    # 8. 渲染 + 落盘 + 推送
    now = datetime.now()
    title = config["push"]["title_template"].format(
        date=now.strftime("%Y-%m-%d"),
        category_label=category_label,
        category=category,
    )
    content = render_push_content(
        top, category, category_label, show_score=not is_wechat,
        trends=trends, watchlist=watchlist, run_stats=run_stats,
    )

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
