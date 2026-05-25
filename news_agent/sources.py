"""新闻源采集：统一从 RSS / Google News / RSSHub 拉取，输出统一格式的 Article 列表。"""
from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import quote_plus, urlparse, urlunparse

import feedparser
from dateutil import parser as date_parser
from googlenewsdecoder import new_decoderv1

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; NewsAgent/1.0)"


def _url_for_dedup(url: str) -> str:
    """去掉 query 参数和 fragment，只用 scheme+host+path 做去重。"""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


@dataclass
class Article:
    title: str
    url: str
    summary: str
    source: str
    published_at: datetime | None = None
    uid: str = field(init=False)

    def __post_init__(self) -> None:
        self.uid = hashlib.sha1(_url_for_dedup(self.url).encode("utf-8")).hexdigest()[:16]


def _parse_date(entry) -> datetime | None:
    for key in ("published", "updated", "created"):
        val = entry.get(key)
        if not val:
            continue
        try:
            dt = date_parser.parse(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue
    return None


def _clean_summary(raw: str, limit: int = 600) -> str:
    if not raw:
        return ""
    # feedparser 已经 strip 了 HTML 大部分标签，这里再清一遍
    import re
    text = re.sub(r"<[^>]+>", "", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _resolve_google_news_url(gnews_url: str) -> str:
    """将 Google News 中转链接解析为原始文章 URL，失败时返回原链接。"""
    if "news.google.com" not in gnews_url:
        return gnews_url
    try:
        result = new_decoderv1(gnews_url, interval=0)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception as e:
        log.debug("gnews decode failed for %s: %s", gnews_url[:80], e)
    return gnews_url


def _fetch_feed(url: str, source_name: str) -> list[Article]:
    """拉一个 RSS feed,容错返回空列表。"""
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        log.warning("fetch %s failed: %s", source_name, e)
        return []

    if parsed.bozo and not parsed.entries:
        log.warning("bad feed %s: %s", source_name, getattr(parsed, "bozo_exception", ""))
        return []

    out: list[Article] = []
    for entry in parsed.entries:
        url_ = entry.get("link")
        title = entry.get("title")
        if not url_ or not title:
            continue
        out.append(Article(
            title=title.strip(),
            url=url_.strip(),
            summary=_clean_summary(entry.get("summary", "") or entry.get("description", "")),
            source=source_name,
            published_at=_parse_date(entry),
        ))
    return out


def fetch_rss_sources(feeds: list[dict]) -> list[Article]:
    """从配置的 RSS 源批量拉取。"""
    all_articles: list[Article] = []
    for feed in feeds:
        name = feed["name"]
        articles = _fetch_feed(feed["url"], name)
        log.info("RSS [%s] -> %d articles", name, len(articles))
        all_articles.extend(articles)
        time.sleep(0.3)  # 礼貌延时
    return all_articles


def fetch_google_news(queries: list[str]) -> list[Article]:
    """Google News RSS 搜索（免费、稳定、不需 key）。

    URL 模板：https://news.google.com/rss/search?q=KEYWORD&hl=zh-CN&gl=CN&ceid=CN:zh
    when:7d 限制 7 天窗口（配合一周推送一次的节奏）。
    """
    all_articles: list[Article] = []
    for query in queries:
        encoded = quote_plus(f"{query} when:7d")
        url = f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh"
        articles = _fetch_feed(url, f"Google News: {query}")
        log.info("Google News [%s] -> %d articles", query, len(articles))
        all_articles.extend(articles)
        time.sleep(0.5)

    # 并发解析所有 Google News 中转链接
    gnews_articles = [a for a in all_articles if "news.google.com" in a.url]
    if gnews_articles:
        log.info("resolving %d Google News URLs (concurrent)...", len(gnews_articles))
        resolved = 0
        with ThreadPoolExecutor(max_workers=20) as pool:
            future_to_article = {
                pool.submit(_resolve_google_news_url, a.url): a
                for a in gnews_articles
            }
            for future in as_completed(future_to_article):
                article = future_to_article[future]
                new_url = future.result()
                if new_url != article.url:
                    article.url = new_url
                    article.uid = hashlib.sha1(_url_for_dedup(new_url).encode("utf-8")).hexdigest()[:16]
                    resolved += 1
        log.info("resolved %d / %d Google News URLs", resolved, len(gnews_articles))

    return all_articles


def fetch_rsshub(instance: str, routes: list[dict]) -> list[Article]:
    """RSSHub 路由（用于 X / 微信公众号等需要二次包装的源）。"""
    if not routes:
        return []
    all_articles: list[Article] = []
    instance = instance.rstrip("/")
    for route in routes:
        url = f"{instance}{route['path']}"
        articles = _fetch_feed(url, f"RSSHub: {route['name']}")
        log.info("RSSHub [%s] -> %d articles", route["name"], len(articles))
        all_articles.extend(articles)
        time.sleep(0.5)
    return all_articles


def filter_by_age(articles: Iterable[Article], max_age_hours: int | None) -> list[Article]:
    """丢弃发布时间早于 max_age_hours 的文章。无 published_at 的保留(避免误杀)。

    None 或 <=0 时不过滤。OpenAI/HF/DeepMind 等 feed 不分页会返回全量历史,
    必须用这个把窗口收住。
    """
    if not max_age_hours or max_age_hours <= 0:
        return list(articles)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    kept: list[Article] = []
    dropped_by_source: dict[str, int] = {}
    for a in articles:
        if a.published_at is not None and a.published_at < cutoff:
            dropped_by_source[a.source] = dropped_by_source.get(a.source, 0) + 1
            continue
        kept.append(a)
    for src, n in sorted(dropped_by_source.items(), key=lambda x: -x[1])[:5]:
        log.info("filter_by_age: dropped %d stale articles from %s", n, src)
    return kept


def dedupe(articles: Iterable[Article]) -> list[Article]:
    """按 uid 去重，保留第一条。"""
    seen: set[str] = set()
    out: list[Article] = []
    for a in articles:
        if a.uid in seen:
            continue
        seen.add(a.uid)
        out.append(a)
    return out


def _normalize_title(title: str) -> str:
    import re
    t = re.sub(r"[\s　]+", "", title)
    return t.lower()


def _extract_key_terms(raw_title: str) -> set[str]:
    """从原始标题中提取关键术语（产品名+版本号、英文单词），用于跨语言去重。

    在中英文字符边界处插入空格后提取带连字符/点号的完整标识符，
    这样"GPT-4o" → {"gpt-4o"}, "Claude 4.5" → {"claude", "4.5"}。
    """
    import re
    s = re.sub(r"([一-鿿])([a-zA-Z\d])", r"\1 \2", raw_title)
    s = re.sub(r"([a-zA-Z\d])([一-鿿])", r"\1 \2", s)
    s = s.lower()
    return {t for t in re.findall(r"[a-z\d]+(?:[.\-][a-z\d]+)*", s) if len(t) >= 2}


def _is_similar(norm_a: str, norm_b: str, raw_a: str, raw_b: str, threshold: float) -> bool:
    from difflib import SequenceMatcher

    if SequenceMatcher(None, norm_a, norm_b).ratio() >= threshold:
        return True

    # 关键术语重叠（处理中英混排标题，如"谷歌发布Gemini 3.0" vs "Google releases Gemini 3.0"）
    terms_a = _extract_key_terms(raw_a)
    terms_b = _extract_key_terms(raw_b)
    if len(terms_a) >= 2 and len(terms_b) >= 2:
        overlap = terms_a & terms_b
        smaller = min(len(terms_a), len(terms_b))
        if len(overlap) / smaller >= 0.7:
            return True

    return False


def dedupe_by_content(articles: list[Article], threshold: float = 0.65) -> list[Article]:
    """按标题相似度去重跨源重复报道。同一事件保留摘要最长的一条。"""
    if not articles:
        return []

    kept: list[Article] = []
    normed: list[str] = []

    for a in articles:
        na = _normalize_title(a.title)
        dup_idx: int | None = None
        for i, nk in enumerate(normed):
            if _is_similar(na, nk, a.title, kept[i].title, threshold):
                dup_idx = i
                break
        if dup_idx is not None:
            if len(a.summary) > len(kept[dup_idx].summary):
                log.debug("content dedup: replace [%s] with [%s]", kept[dup_idx].title, a.title)
                kept[dup_idx] = a
                normed[dup_idx] = na
            else:
                log.debug("content dedup: drop [%s] (dup of [%s])", a.title, kept[dup_idx].title)
        else:
            kept.append(a)
            normed.append(na)

    dropped = len(articles) - len(kept)
    if dropped:
        log.info("dedupe_by_content: removed %d cross-source duplicates", dropped)
    return kept
