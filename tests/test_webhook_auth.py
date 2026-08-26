import os
import unittest

from news_agent.webhook import _is_authorized


class WebhookAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("NEWS_AGENT_WEBHOOK_SECRET")

    def tearDown(self):
        if self.original is None:
            os.environ.pop("NEWS_AGENT_WEBHOOK_SECRET", None)
        else:
            os.environ["NEWS_AGENT_WEBHOOK_SECRET"] = self.original

    def test_body_secret_authorizes_pushplus_custom_webhook(self):
        os.environ["NEWS_AGENT_WEBHOOK_SECRET"] = "test-secret"
        self.assertTrue(_is_authorized({}, {"content": "AI治理", "secret": "test-secret"}))
        self.assertFalse(_is_authorized({}, {"content": "AI治理", "secret": "wrong"}))

    def test_header_secret_authorizes_generic_webhook(self):
        os.environ["NEWS_AGENT_WEBHOOK_SECRET"] = "test-secret"
        self.assertTrue(_is_authorized({"X-News-Agent-Secret": "test-secret"}, {}))



if __name__ == "__main__":
    unittest.main()
