"""新闻源采集：统一从 RSS / Google News / RSSHub 拉取，输出统一格式的 Article 列表。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus, urlparse, urlunparse

import feedparser
import httpx
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


def _norm_name(s: str) -> str:
    """公众号名归一化:去掉所有空白(含全角)再小写,用于跨大小写/空格匹配。"""
    return re.sub(r"\s+", "", s).lower()


def parse_account_list(path) -> list[dict]:
    """解析 "WeChat and website list.md":含「微信公众号」和「官网」两类小节。

    按二级标题判断每条属于哪类(决定时间窗口):
      标题含「公众号」          → kind="wechat" (默认 48h)
      标题含「官网/网站/website」 → kind="website"(默认 24h)
    每行 `- 名称` 或 `- 名称 | 地址`(地址 / 开头=RSSHub 路由,http 开头=feed/网页)。
    H1 标题与「怎么填」等说明小节自动忽略。返回 [{"name","inline","kind"}, ...]。
    """
    path = Path(path)
    if not path.exists():
        log.warning("account list not found: %s", path)
        return []

    entries: list[dict] = []
    seen: set[str] = set()
    kind: str | None = None
    for line in path.read_text("utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            if s.startswith("##"):
                h = s.lstrip("#").strip().lower()
                if "公众号" in h:
                    kind = "wechat"
                elif "官网" in h or "网站" in h or "website" in h:
                    kind = "website"
                else:
                    kind = None
            else:
                kind = None  # H1 标题,重置
            continue
        if kind is None or not (s.startswith("- ") or s.startswith("* ")):
            continue
        item = s[2:].strip()
        inline: str | None = None
        if "|" in item:
            name, rest = item.split("|", 1)
            name, rest = name.strip(), rest.strip()
            if rest.startswith("/") or rest.startswith("http"):
                inline = rest
        else:
            name = item
        name = name.strip().strip("`").strip()
        key = _norm_name(name)
        if not name or key in seen:
            continue
        seen.add(key)
        entries.append({"name": name, "inline": inline, "kind": kind})
    return entries


def _load_opml_map(opml_urls: list[str]) -> dict[str, str]:
    """下载 OPML 源,构建 {归一化公众号名: RSS 地址}。先出现的优先。"""
    mapping: dict[str, str] = {}
    for url in opml_urls or []:
        try:
            r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True)
            r.raise_for_status()
            text = r.text
        except (httpx.HTTPError, ValueError) as e:
            log.warning("failed to fetch OPML %s: %s", url, e)
            continue
        n = 0
        for m in re.finditer(r'<outline\b[^>]*\btext="([^"]+)"[^>]*\bxmlUrl="([^"]+)"', text):
            mapping.setdefault(_norm_name(m.group(1)), m.group(2))
            n += 1
        log.info("OPML %s -> %d feeds", url, n)
    return mapping


def resolve_wechat_feeds(
    accounts: list[dict],
    opml_urls: list[str],
    rsshub_instance: str,
    cache_path,
) -> list[dict]:
    """把公众号名解析成可抓取的 RSS 地址。

    顺序:行内手填地址 > 缓存 > OPML 映射。OPML 仅在有未缓存名称时才下载(惰性)。
    新解析到的写回 cache_path,跨次运行复用。返回 [{"name":.., "url":..}, ...]。
    """
    instance = (rsshub_instance or "").rstrip("/")
    cache_path = Path(cache_path)
    cache: dict[str, str] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    opml_map: dict[str, str] | None = None  # 惰性加载
    resolved: list[dict] = []
    unresolved: list[str] = []
    dirty = False

    for acc in accounts:
        name, inline = acc["name"], acc.get("inline")
        kind = acc.get("kind", "wechat")
        if inline:
            url = inline if inline.startswith("http") else f"{instance}{inline}"
            resolved.append({"name": name, "url": url, "kind": kind})
            continue
        key = _norm_name(name)
        if key in cache:
            resolved.append({"name": name, "url": cache[key], "kind": kind})
            continue
        if opml_map is None:
            opml_map = _load_opml_map(opml_urls)
        if key in opml_map:
            cache[key] = opml_map[key]
            dirty = True
            resolved.append({"name": name, "url": opml_map[key], "kind": kind})
        else:
            unresolved.append(name)

    if dirty:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), "utf-8")

    if unresolved:
        log.warning(
            "无法自动解析 %d 个公众号(不在 OPML 源里): %s。"
            "请自建 wechat2rss/werss 并把其 OPML 加到 config 的 wechat.resolver.opml_urls,"
            "或在清单里用「名称 | 地址」手动指定。",
            len(unresolved), "、".join(unresolved),
        )
    return resolved


def _meta(html: str, key: str) -> str:
    """从 HTML 里取 <meta property/name=key content=...> 的 content(容忍属性顺序)。"""
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        tag = m.group(0)
        if re.search(rf'(?:property|name)=["\']{re.escape(key)}["\']', tag, re.I):
            cm = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
            if cm:
                return cm.group(1).strip()
    return ""


def _fetch_page_as_article(url: str, source_name: str) -> Article | None:
    """把一个普通网页(官网首页 / 公众号文章链接)抓成单篇 Article,用 og 标签提取标题/摘要。"""
    try:
        r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0, follow_redirects=True)
        r.raise_for_status()
        html = r.text
    except (httpx.HTTPError, ValueError) as e:
        log.warning("fetch page %s failed: %s", source_name, e)
        return None

    title = _meta(html, "og:title") or _meta(html, "twitter:title")
    if not title:
        tm = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        title = tm.group(1).strip() if tm else source_name
    desc = _meta(html, "og:description") or _meta(html, "description")

    published = None
    pub_raw = _meta(html, "article:published_time")
    if pub_raw:
        try:
            published = date_parser.parse(pub_raw)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            published = None

    return Article(
        title=title.strip() or source_name,
        url=url,
        summary=_clean_summary(desc),
        source=source_name,
        published_at=published,
    )


def fetch_wechat_articles(resolved: list[dict]) -> list[Article]:
    """抓取已解析出 url 的公众号。

    地址是 RSS feed → 抓多篇;不是有效 feed(官网首页 / 公众号文章链接)→ 当成单篇文章抓。
    """
    all_articles: list[Article] = []
    for acc in resolved:
        articles = _fetch_feed(acc["url"], acc["name"])
        if not articles:
            single = _fetch_page_as_article(acc["url"], acc["name"])
            if single:
                articles = [single]
                log.info("WeChat [%s] -> 非 feed,按单页文章抓取", acc["name"])
        log.info("WeChat [%s] -> %d articles", acc["name"], len(articles))
        all_articles.extend(articles)
        time.sleep(0.5)
    return all_articles


def filter_by_age(
    articles: Iterable[Article],
    max_age_hours: int | None,
    drop_undated: bool = False,
) -> list[Article]:
    """丢弃发布时间早于 max_age_hours 的文章。

    默认保留无 published_at 的文章(避免误杀普通 RSS)。drop_undated=True 时连无日期的
    一并丢弃——用于"只要近 N 小时内发布"的严格场景(如微信公众号流程)。
    None 或 <=0 时不过滤。
    """
    if not max_age_hours or max_age_hours <= 0:
        return list(articles)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    kept: list[Article] = []
    dropped_by_source: dict[str, int] = {}
    for a in articles:
        too_old = a.published_at is not None and a.published_at < cutoff
        undated = a.published_at is None and drop_undated
        if too_old or undated:
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


def _normalize_body(text: str) -> str:
    """正文归一化：去 HTML 标签、去空白与标点、转小写，便于做字符指纹比较。"""
    import re
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", "", text)          # 去掉 RSS 摘要里常见的 HTML 标签
    t = re.sub(r"[^\w一-鿿]+", "", t)          # 只保留字母数字下划线 + 中文
    return t.lower()


def _shingles(text: str, n: int = 4) -> set[str]:
    """字符 n-gram 指纹。中英文通用，无需分词。"""
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _body_similar(body_a: str, body_b: str, threshold: float) -> bool:
    """正文相似度：字符 4-gram 的 Jaccard 重叠。用于标题不同但内容雷同的跨源重复。"""
    na, nb = _normalize_body(body_a), _normalize_body(body_b)
    # 正文太短（如官网首页/纯链接条目）不参与正文判重，避免误杀
    if len(na) < 60 or len(nb) < 60:
        return False
    sa, sb = _shingles(na), _shingles(nb)
    if not sa or not sb:
        return False
    inter = len(sa & sb)
    union = len(sa | sb)
    return union > 0 and inter / union >= threshold


def dedupe_by_content(
    articles: list[Article],
    threshold: float = 0.65,
    body_threshold: float = 0.5,
) -> list[Article]:
    """跨源去重重复报道：先比标题相似度，再比正文指纹。同一事件保留摘要最长的一条。"""
    if not articles:
        return []

    kept: list[Article] = []
    normed: list[str] = []

    for a in articles:
        na = _normalize_title(a.title)
        dup_idx: int | None = None
        for i, nk in enumerate(normed):
            if _is_similar(na, nk, a.title, kept[i].title, threshold) or _body_similar(
                a.summary, kept[i].summary, body_threshold
            ):
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
