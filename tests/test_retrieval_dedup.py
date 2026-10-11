import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from news_agent.filter import ScoredArticle, assess_research_value
from news_agent.main import seed_event_history
from news_agent.sources import (
    Article,
    _google_news_locale,
    _is_similar,
    _normalize_title,
    _split_google_news_title,
)
from news_agent.state import EventHistory


class _RecordingClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))],
        )


def _similar(a: str, b: str, threshold: float = 0.65) -> bool:
    return _is_similar(_normalize_title(a), _normalize_title(b), a, b, threshold)


class GoogleNewsQueryTests(unittest.TestCase):
    def test_locale_follows_query_script(self):
        self.assertEqual("en-US", _google_news_locale('"AI PC" shipments')[0])
        self.assertEqual("zh-CN", _google_news_locale("AI眼镜 出货量")[0])
        self.assertEqual("ja", _google_news_locale("日本 データ利活用 AI政策")[0])
        self.assertEqual("ko", _google_news_locale("韩国 데이터 산업 진흥 정책")[0])

    def test_publisher_suffix_is_split_from_title(self):
        self.assertEqual(
            ("华为余承东：计划将鸿蒙推向全球市场", "财联社电报"),
            _split_google_news_title("华为余承东：计划将鸿蒙推向全球市场 - 财联社电报"),
        )
        self.assertEqual(("No publisher here", ""), _split_google_news_title("No publisher here"))


class TitleSimilarityTests(unittest.TestCase):
    def test_generic_terms_alone_do_not_merge_different_events(self):
        self.assertFalse(_similar("这届 AI PC，太烧心了", "全球 AI PC 出货量同比大跌"))

    def test_shared_product_and_version_still_merge(self):
        self.assertTrue(_similar("谷歌发布Gemini 3.0", "Google releases Gemini 3.0"))


class EventHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_repost_of_pushed_event_is_detected_across_runs(self):
        history = EventHistory(self.path)
        history.add("华为余承东：智能手机芯片基本摆脱外部依赖", "huawei-chip-2026")
        history.save()

        reloaded = EventHistory(self.path)
        self.assertIsNotNone(reloaded.matching_title("华为余承东：智能手机芯片已基本摆脱外部依赖"))
        self.assertIsNone(reloaded.matching_title("苹果发布会定档 10 月 13 日"))
        self.assertTrue(reloaded.has_event_key("huawei-chip-2026"))
        self.assertFalse(reloaded.has_event_key(""))
        self.assertEqual(["huawei-chip-2026"], reloaded.event_keys())

    def test_prune_drops_expired_events(self):
        history = EventHistory(self.path, retention_days=7)
        history.add("old", "old-key", ts=datetime.now(timezone.utc) - timedelta(days=8))
        history.add("new", "new-key")
        self.assertEqual(1, history.prune())
        self.assertEqual(["new-key"], history.event_keys())

    def test_seed_from_recent_output_files_only(self):
        out = Path(self.tmp.name) / "output"
        out.mkdir()
        recent = datetime.now() - timedelta(days=2)
        stale = datetime.now() - timedelta(days=60)
        (out / f"AI终端+{recent:%Y-%m-%d_%H%M%S}.md").write_text(
            "# t\n\n### 1. 阶跃首款智能体手机定档 - TGBUS\n\n### 2. 华为 Mate 90 体验\n", "utf-8",
        )
        (out / f"AI治理+{stale:%Y-%m-%d_%H%M%S}.md").write_text("### 1. 很久以前的新闻\n", "utf-8")

        history = EventHistory(self.path, retention_days=21)
        seed_event_history(history, out)
        self.assertIsNotNone(history.matching_title("阶跃首款智能体手机定档"))
        self.assertIsNone(history.matching_title("很久以前的新闻"))


class KnownEventKeyPromptTests(unittest.TestCase):
    def test_recent_event_keys_are_given_to_assessor(self):
        article = Article("Policy", "https://example.com/a", "preview", "Official")
        scored = [ScoredArticle(article=article, score=5, reason="coarse", categories=["terminal"])]
        client = _RecordingClient([{
            "id": 0, "relevance": 8, "novelty": 2, "evidence": 5,
            "actionability": 5, "event_key": "apple-event-2026", "reason": "旧事件",
        }])
        assess_research_value(
            client, scored, "focus", "model", "terminal", known_event_keys=["apple-event-2026"],
        )
        system_msg = client.calls[0]["messages"][0]["content"]
        self.assertIn("近期已推送事件", system_msg)
        self.assertIn("apple-event-2026", system_msg)


if __name__ == "__main__":
    unittest.main()
