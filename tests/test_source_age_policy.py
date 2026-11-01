import unittest
from datetime import datetime, timezone
from news_agent.sources import Article, filter_by_age


class AgePolicyTests(unittest.TestCase):
    def test_require_date_applies_without_time_window(self):
        dated = Article('dated', 'https://example.com/a', '', 'test', datetime.now(timezone.utc))
        undated = Article('undated', 'https://example.com/b', '', 'test')
        for window in (None, 0, -1):
            self.assertEqual([dated], filter_by_age([dated, undated], window, drop_undated=True))
            self.assertEqual([dated, undated], filter_by_age([dated, undated], window))
