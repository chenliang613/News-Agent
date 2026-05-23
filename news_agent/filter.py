"""DeepSeek API 过滤层：用 keywords.md 做相关性打分,top N 写中文摘要。

使用 DeepSeek API（OpenAI 兼容格式），价格极低。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import openai

from .sources import Article

log = logging.getLogger(__name__)

VALID_CATEGORIES = {"governance", "data", "industry", "agent"}

CATEGORY_FOCUS_LABELS = {
    "governance": "AI 治理(监管/安全对齐/企业治理/出口管制/治理标准)",
    "data": "AI 数据(训练数据合规/数据中心/算力/数据要素/RAG/Agent 数据)",
    "industry": "AI 行业落地(行业大模型/垂直 Agent/产业格局/编程 Agent)",
    "agent": "AI Agent 动态(Agent 产品发布/技术突破/新标准与范式)",
}


@dataclass
class ScoredArticle:
    article: Article
    score: float
    reason: str
    categories: list[str] = field(default_factory=list)
    summary: str = ""


SCORER_SYSTEM = """你是一个新闻相关性评分 + 板块分类助手。你将收到一份用户的关注主题说明(keywords.md),然后是一批待评分的新闻。

主题说明里定义了四个板块,scorer 必须用这四个英文 key:
- governance → AI 治理(监管/安全对齐/企业治理/出口管制/治理标准)
- data       → AI 数据(训练数据合规/数据中心/数据要素/RAG/Agent数据)
- industry   → AI 行业落地(行业大模型/垂直 Agent/产业格局/编程 Agent)
- agent      → AI Agent 动态(Agent 产品发布/技术突破/新标准与范式)

任务:
1. 按 keywords.md 描述的关注角度和过滤规则,给每条新闻打 0-10 分相关性:
   - 0-3: 完全无关或属于过滤规则要排除的内容
   - 4-5: 沾边但价值不高(如标题党、轻量产品发布、纯翻译稿)
   - 6-7: 相关且有信息量(如行业落地案例、监管细则、有数据支撑的产业分析)
   - 8-10: 高度相关、信息密度高、对用户研究目标(四板块之一)有直接价值
2. 给每条新闻标记 categories(0~4 个板块 key 组成的数组):
   - score >= 4 必须至少标一个板块
   - 跨板块新闻给多个(如"美国扩大对华芯片出口管制" → ["governance", "data"])
   - score < 4 可以给空数组 []

严格按用户的过滤规则执行——属于排除项的一律给 0-3 分。

返回 JSON 数组,每条对应一篇新闻,顺序与输入一致。每条格式:
{"id": <输入序号>, "score": <0-10 数字,可带一位小数>, "categories": ["governance"|"data"|"industry"|"agent", ...], "reason": "<不超过30字的中文打分理由>"}

只返回 JSON,不要 markdown 包装,不要任何其他文字。"""


SUMMARIZER_SYSTEM = """你是一个中文新闻摘要助手。你将收到一份用户关注主题说明,然后是若干已经被判定为高相关的新闻。

请为每条新闻写一段 80-150 字的中文摘要,要求:
- 突出与用户关注主题最相关的信息点(具体公司、数据、案例、决策)
- 不要堆砌形容词,不要复述标题
- 如果原文是英文,直接译写要点;不要保留英文术语除非是专有名词

返回 JSON 数组,每条格式:
{"id": <输入序号>, "summary": "<中文摘要>"}

只返回 JSON,不要 markdown 包装。"""


def _article_to_dict(idx: int, a: Article) -> dict:
    return {
        "id": idx,
        "title": a.title,
        "source": a.source,
        "url": a.url,
        "preview": a.summary[:400] if a.summary else "",
    }


def _extract_json_array(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def score_articles(
    client: openai.OpenAI,
    articles: list[Article],
    keywords_md: str,
    model: str,
    batch_size: int = 20,
    active_category: str | None = None,
) -> list[ScoredArticle]:
    if not articles:
        return []

    focus_directive = ""
    if active_category in VALID_CATEGORIES:
        focus_label = CATEGORY_FOCUS_LABELS[active_category]
        focus_directive = (
            f"\n\n【今日聚焦】今天只关心 **{active_category}** 板块 ({focus_label})。"
            f"严格要求:\n"
            f"- 不属于该板块的新闻一律 0-3 分,无论它本身多么相关。\n"
            f'- categories 字段只允许出现 ["{active_category}"] 或空数组 [],不要写其他板块。\n'
            f"- 跨板块的新闻只要主要价值不在 {active_category},也按上面规则给 0-3 分。"
        )

    system_msg = (
        SCORER_SYSTEM + focus_directive
        + f"\n\n以下是用户的关注主题说明:\n\n<keywords>\n{keywords_md}\n</keywords>"
    )

    scored: list[ScoredArticle] = []

    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start : batch_start + batch_size]
        articles_json = json.dumps(
            [_article_to_dict(i, a) for i, a in enumerate(batch)],
            ensure_ascii=False,
            indent=2,
        )

        user_msg = f"以下是本批 {len(batch)} 条新闻(JSON 数组):\n\n{articles_json}\n\n请输出 JSON 数组评分结果。"

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=4000,
                temperature=0.1,
            )
            text = response.choices[0].message.content or ""
        except Exception as e:
            log.error("scorer API error on batch starting %d: %s", batch_start, e)
            for a in batch:
                scored.append(ScoredArticle(article=a, score=5.0, reason="评分失败"))
            continue

        usage = response.usage
        log.info(
            "scorer batch %d: input=%d output=%d",
            batch_start // batch_size,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )

        try:
            result = _extract_json_array(text)
        except (json.JSONDecodeError, ValueError) as e:
            log.error("scorer returned non-JSON, raw=%r, err=%s", text[:200], e)
            for a in batch:
                scored.append(ScoredArticle(article=a, score=5.0, reason="解析失败"))
            continue

        by_id = {item.get("id"): item for item in result if isinstance(item, dict)}
        for idx, a in enumerate(batch):
            item = by_id.get(idx, {})
            try:
                score = float(item.get("score", 5.0))
            except (TypeError, ValueError):
                score = 5.0
            raw_cats = item.get("categories") or []
            if not isinstance(raw_cats, list):
                raw_cats = []
            seen: set[str] = set()
            cats: list[str] = []
            for c in raw_cats:
                if isinstance(c, str) and c in VALID_CATEGORIES and c not in seen:
                    seen.add(c)
                    cats.append(c)
            if active_category in VALID_CATEGORIES:
                cats = [c for c in cats if c == active_category]
            scored.append(ScoredArticle(
                article=a,
                score=max(0.0, min(10.0, score)),
                reason=str(item.get("reason", ""))[:50],
                categories=cats,
            ))

    return scored


def summarize_top(
    client: openai.OpenAI,
    scored: list[ScoredArticle],
    keywords_md: str,
    model: str,
) -> list[ScoredArticle]:
    if not scored:
        return scored

    articles_json = json.dumps(
        [_article_to_dict(i, s.article) for i, s in enumerate(scored)],
        ensure_ascii=False,
        indent=2,
    )

    system_msg = (
        SUMMARIZER_SYSTEM
        + f"\n\n用户关注主题:\n\n<keywords>\n{keywords_md}\n</keywords>"
    )
    user_msg = f"待摘要的 {len(scored)} 条新闻:\n\n{articles_json}\n\n请输出 JSON 摘要结果。"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=8000,
            temperature=0.2,
        )
        text = response.choices[0].message.content or ""
    except Exception as e:
        log.error("summarizer API error: %s", e)
        return scored

    usage = response.usage
    log.info(
        "summarizer: input=%d output=%d",
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )

    try:
        result = _extract_json_array(text)
    except (json.JSONDecodeError, ValueError) as e:
        log.error("summarizer returned non-JSON, raw=%r, err=%s", text[:200], e)
        return scored

    by_id = {item.get("id"): item.get("summary", "") for item in result if isinstance(item, dict)}
    for idx, s in enumerate(scored):
        s.summary = str(by_id.get(idx, ""))[:400]
    return scored
