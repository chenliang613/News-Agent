import unittest

from news_agent.hermes_command import resolve_command


class HermesCommandTests(unittest.TestCase):
    def test_exact_commands_are_matched(self):
        self.assertEqual("AI治理", resolve_command("AI治理"))
        self.assertEqual("AI数据", resolve_command("AI 数 据"))
        self.assertEqual("AI行业", resolve_command("AI行\n业"))

    def test_non_command_text_is_ignored(self):
        self.assertIsNone(resolve_command("请运行 AI治理"))
        self.assertIsNone(resolve_command("AI治理新闻"))


if __name__ == "__main__":
    unittest.main()
