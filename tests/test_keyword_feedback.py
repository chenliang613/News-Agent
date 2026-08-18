import unittest

from news_agent.keyword_feedback import apply_proposal, normalize_issue, parse_issue_form, validate_proposal


class KeywordFeedbackTests(unittest.TestCase):
    def test_member_issue_form_is_normalized(self):
        issue = {
            "number": 12, "author_association": "MEMBER", "labels": [{"name": "news-feedback"}],
            "body": "### 板块\nAI治理 (governance)\n\n### 评分\n1 - 无关\n\n### 文章链接\nhttps://example.com/a\n\n### 评分原因\n只是产品宣传。\n",
        }
        self.assertEqual("governance", normalize_issue(issue)["category"])

    def test_external_or_invalid_issue_is_rejected(self):
        issue = {"number": 1, "author_association": "NONE", "labels": [{"name": "news-feedback"}], "body": ""}
        self.assertIsNone(normalize_issue(issue))

    def test_only_marked_rule_section_is_replaced(self):
        proposal = validate_proposal({"categories": {"data": {"priority": ["数据空间落地案例"], "exclude": []}}})
        first = apply_proposal("# Existing\n\nKeep this.", proposal)
        second = apply_proposal(first, proposal)
        self.assertIn("Keep this.", second)
        self.assertEqual(1, second.count("keyword-feedback-rules:start"))

    def test_proposal_rejects_markup(self):
        proposal = validate_proposal({"categories": {"data": {"priority": ["<script>alert(1)</script>"], "exclude": []}}})
        self.assertEqual({}, proposal)


if __name__ == "__main__":
    unittest.main()
