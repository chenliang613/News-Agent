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
        """每条 cron（主梯队 12:00 / 补跑梯队 12:30-15:30 / 兜底 16:30 北京时间）都必须落在 weekday_category 的四天。"""
        config = load_config()
        workflow = yaml.load(
            Path(".github/workflows/daily.yml").read_text("utf-8"), Loader=yaml.BaseLoader,
        )
        entries = workflow["on"]["schedule"]
        # 主梯队 UTC 04:00（北京 12:00）+ 补跑梯队 UTC 04:30-07:30（北京 12:30-15:30）
        # + 兜底梯队 UTC 08:30（北京 16:30）
        self.assertEqual(
            {("0", "4"), ("30", "4,5,6,7"), ("30", "8")},
            {(e["cron"].split()[0], e["cron"].split()[1]) for e in entries},
        )
        scheduled_days = {day for day in range(7) if resolve_category(config, day)}
        for entry in entries:
            minute, hour, _, _, dow = entry["cron"].split()
            # cron 星期 d(0=周日) 的 UTC 时刻是北京时间同日；换算到 Python
            # weekday(0=周一) 需减 1。
            beijing_weekdays = {(int(d) - 1) % 7 for d in dow.split(",")}
            self.assertEqual(scheduled_days, beijing_weekdays)
