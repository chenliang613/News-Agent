import json
import unittest
from pathlib import Path


class QualityFixtureTests(unittest.TestCase):
    def test_quality_cases_are_valid_and_cover_active_categories(self):
        path = Path(__file__).parent / "fixtures" / "quality_cases.json"
        cases = json.loads(path.read_text("utf-8"))
        self.assertGreaterEqual(len(cases), 3)
        self.assertEqual({"governance", "data", "industry"}, {case["category"] for case in cases})
        for case in cases:
            self.assertIn(case["expected"], {"valuable", "irrelevant"})
            self.assertTrue(case["title"] and case["why"])
