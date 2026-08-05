import unittest
from pathlib import Path

import yaml

from news_agent.main import load_config, resolve_category


class ConfigurationAlignmentTests(unittest.TestCase):
    def test_active_categories_align_across_config_and_workflow(self):
        config = load_config()
        scheduled = {resolve_category(config, day) for day in range(7)} - {None}
        self.assertEqual({"governance", "data", "industry"}, scheduled)
        self.assertEqual(scheduled, set(config["category_labels"]))
        self.assertEqual(scheduled, set(config["google_news_queries"]))

        workflow = yaml.load(
            Path(".github/workflows/daily.yml").read_text("utf-8"), Loader=yaml.BaseLoader,
        )
        options = set(workflow["on"]["workflow_dispatch"]["inputs"]["category"]["options"]) - {""}
        self.assertEqual(scheduled, options)
        self.assertEqual("0 0 * * 1,3,5", workflow["on"]["schedule"][0]["cron"])
