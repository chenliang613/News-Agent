import unittest
from pathlib import Path

import yaml

from news_agent.main import load_config, resolve_category


class ConfigurationAlignmentTests(unittest.TestCase):
    def test_active_categories_align_across_config_and_workflow(self):
        config = load_config()
        scheduled = {resolve_category(config, day) for day in range(7)} - {None}
        self.assertEqual({"governance", "data", "industry", "terminal"}, scheduled)
        self.assertEqual(scheduled, set(config["category_labels"]))
        self.assertEqual(scheduled, set(config["google_news_queries"]))

        workflow = yaml.load(
            Path(".github/workflows/daily.yml").read_text("utf-8"), Loader=yaml.BaseLoader,
        )
        options = set(workflow["on"]["workflow_dispatch"]["inputs"]["category"]["options"]) - {""}
        self.assertEqual(scheduled, options)

    def test_cron_fires_on_beijing_scheduled_weekdays(self):
        """cron 按 UTC 04:00 触发，对应北京时间同日 12:00；必须与 weekday_category 一致。"""
        config = load_config()
        workflow = yaml.load(
            Path(".github/workflows/daily.yml").read_text("utf-8"), Loader=yaml.BaseLoader,
        )
        (entry,) = workflow["on"]["schedule"]
        minute, hour, _, _, dow = entry["cron"].split()
        self.assertEqual("4", hour)
        # cron 星期 d(0=周日) 的 UTC 04:00 是北京时间同日 12:00；换算到 Python
        # weekday(0=周一) 需减 1。
        beijing_weekdays = {(int(d) - 1) % 7 for d in dow.split(",")}
        scheduled_days = {day for day in range(7) if resolve_category(config, day)}
        self.assertEqual(scheduled_days, beijing_weekdays)
