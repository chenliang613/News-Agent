import tempfile
import unittest
from pathlib import Path

from news_agent.feedback import feedback_links, feedback_profile, record_feedback


class FeedbackTests(unittest.TestCase):
    def test_links_include_all_scores_when_callback_is_configured(self):
        links = feedback_links(
            "https://news.example.com", url="https://article.example/a", source="Official",
            category="governance", title="Policy",
        )
        self.assertIn("score=1", links)
        self.assertIn("score=5", links)
        self.assertIn("article.example%2Fa", links)

    def test_repeated_feedback_creates_source_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.json"
            self.assertTrue(record_feedback(path, 5, "https://a.example/1", source="Official", category="data"))
            self.assertTrue(record_feedback(path, 4, "https://a.example/2", source="Official", category="data"))
            profile = feedback_profile(path, "data")
            self.assertIn("Official：4.5/5（2 次）", profile)

    def test_invalid_score_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(record_feedback(Path(tmp) / "feedback.json", 6, "https://a.example"))
