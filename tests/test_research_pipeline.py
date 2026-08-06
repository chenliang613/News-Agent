import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from news_agent.filter import ScoredArticle, assess_research_value, group_by_event
from news_agent.sources import Article, _extract_page_text, dedupe_by_content


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))],
        )


class _FailingClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        raise RuntimeError("simulated API outage")


class ResearchPipelineTests(unittest.TestCase):
    def test_coarse_scoring_failure_is_not_treated_as_relevant(self):
        article = Article("AI 新闻", "https://example.com/a", "preview", "Media")
        result = __import__("news_agent.filter", fromlist=["score_articles"]).score_articles(
            _FailingClient(), [article], "focus", "model", active_category="governance",
        )
        self.assertEqual(0.0, result[0].score)
        self.assertEqual([], result[0].categories)

    def test_discovery_without_body_cannot_receive_high_evidence(self):
        article = Article("Policy", "https://example.com/a", "preview", "Google", source_tier="discovery")
        scored = [ScoredArticle(article=article, score=5, reason="coarse", categories=["governance"])]
        payload = [{
            "id": 0, "relevance": 8, "novelty": 7, "evidence": 10,
            "actionability": 6, "event_key": "policy-2026", "reason": "有事实",
        }]
        result = assess_research_value(_FakeClient(payload), scored, "focus", "model", "governance")
        self.assertEqual(5.0, result[0].evidence)

    def test_extract_page_text_excludes_navigation_and_script(self):
        html = """<html><body><nav>Menu</nav><article><h1>Title</h1>
        <p>First verified fact.</p><p>Second verified fact.</p></article>
        <script>secret()</script></body></html>"""
        text = _extract_page_text(html, 1000)
        self.assertIn("First verified fact.", text)
        self.assertNotIn("Menu", text)
        self.assertNotIn("secret", text)

    def test_assessment_recomputes_weighted_score_and_normalizes_event_key(self):
        article = Article("Policy", "https://example.com/a", "preview", "Official", datetime.now(timezone.utc))
        article.content = "A sufficiently detailed body " * 30
        scored = [ScoredArticle(article=article, score=5, reason="coarse", categories=["governance"])]
        payload = [{
            "id": 0, "relevance": 8, "novelty": 7, "evidence": 9,
            "actionability": 6, "score": 0, "event_key": "EU AI Act / Code 2026!", "reason": "有原始规则",
        }]
        result = assess_research_value(_FakeClient(payload), scored, "focus", "model", "governance")
        self.assertEqual(7.8, result[0].score)
        self.assertEqual("eu-ai-act-code-2026", result[0].event_key)

    def test_group_by_event_keeps_highest_value_and_references_others(self):
        a = Article("A", "https://example.com/a", "", "Primary")
        b = Article("B", "https://example.com/b", "", "Media")
        first = ScoredArticle(a, 7, "", event_key="same-event", evidence=7)
        second = ScoredArticle(b, 8, "", event_key="same-event", evidence=6)
        cards = group_by_event([first, second])
        self.assertEqual(1, len(cards))
        self.assertIs(cards[0], second)
        self.assertEqual([a], cards[0].related_articles)

    def test_full_body_dedup_removes_identical_content_with_different_titles(self):
        body = "同一篇完整报道的正文内容，包含多个可验证事实和具体数字。" * 20
        first = Article("机构发布人工智能政策", "https://example.com/a", "短摘要 A", "官方")
        second = Article("政策解读：AI 规则正式出台", "https://example.com/b", "短摘要 B", "转载媒体")
        first.content = body
        second.content = body
        kept = dedupe_by_content([first, second], threshold=1.1, body_threshold=0.90)
        self.assertEqual(1, len(kept))
        self.assertEqual(first.url, kept[0].url)


if __name__ == "__main__":
    unittest.main()
