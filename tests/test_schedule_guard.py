"""--skip-if-ran-today 兜底守卫：当日已有产物静默跳过，无产物继续原流程。"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from news_agent import main as main_mod
from news_agent.main import already_pushed_today

TODAY = datetime(2026, 10, 12, 16, 30, 0)
BRIEFING_NAME = "AI治理+2026-10-12_141240.md"


class AlreadyPushedTodayTests(unittest.TestCase):
    def test_detects_today_briefing_for_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / BRIEFING_NAME).write_text("# x", encoding="utf-8")
            self.assertTrue(already_pushed_today("governance", output_dir=out, now=TODAY))

    def test_ignores_other_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / BRIEFING_NAME).write_text("# x", encoding="utf-8")
            self.assertFalse(already_pushed_today("data", output_dir=out, now=TODAY))

    def test_ignores_other_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / BRIEFING_NAME).write_text("# x", encoding="utf-8")
            self.assertFalse(
                already_pushed_today(
                    "governance", output_dir=out, now=datetime(2026, 10, 13, 8, 0, 0),
                )
            )


class SkipIfRanTodayTests(unittest.TestCase):
    """守卫在环境检查(DeepSeek/PushPlus)之前生效；无 DEEPSEEK_API_KEY 时原流程返回 1。

    run() 内部用真实 datetime.now() 判定"当日"，所以落盘文件名动态生成。
    """

    def run_guarded(self, output_dir: Path, **kwargs):
        with mock.patch.object(main_mod, "OUTPUT_DIR", output_dir), mock.patch.dict(
            main_mod.os.environ, {}, clear=True
        ):
            return main_mod.run(skip_if_ran_today=True, **kwargs)

    @staticmethod
    def briefing_name() -> str:
        # override_weekday=0 → governance → AI治理
        return f"AI治理+{datetime.now():%Y-%m-%d}_000000.md"

    def test_skips_silently_when_today_briefing_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / self.briefing_name()).write_text("# x", encoding="utf-8")
            self.assertEqual(0, self.run_guarded(out, override_weekday=0))

    def test_proceeds_when_no_briefing_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(1, self.run_guarded(Path(tmp), override_weekday=0))

    def test_category_override_bypasses_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / self.briefing_name()).write_text("# x", encoding="utf-8")
            self.assertEqual(1, self.run_guarded(out, override_category="governance"))


if __name__ == "__main__":
    unittest.main()
