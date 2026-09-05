import unittest
from pathlib import Path

from news_agent.filter import focus_keywords_md

KEYWORDS_PATH = Path(__file__).parent.parent / "keywords.md"

SAMPLE = """
## [AI治理]（AI Governance）

  ### 高优先级
  - 欧盟 AI 法案

## [AI数据]（AI Data）

  ### 高优先级
  - 国家数据局

## 过滤规则（下面的信息，请勿推送）

- 纯模型跑分

## scorer 输出格式约定

固定格式
"""


class FocusKeywordsTests(unittest.TestCase):
    def test_keeps_only_matching_category_and_shared_sections(self):
        focused = focus_keywords_md(SAMPLE, "AI治理")
        self.assertIn("欧盟 AI 法案", focused)
        self.assertIn("纯模型跑分", focused)  # 通用过滤规则始终保留
        self.assertIn("固定格式", focused)  # 通用输出格式约定始终保留
        self.assertNotIn("国家数据局", focused)  # 其他板块不应混入

    def test_falls_back_to_full_text_when_no_section_matches(self):
        focused = focus_keywords_md(SAMPLE, "wechat")
        self.assertEqual(SAMPLE, focused)

    def test_falls_back_to_full_text_when_document_has_no_headings(self):
        plain = "没有二级标题的普通说明文字"
        self.assertEqual(plain, focus_keywords_md(plain, "AI治理"))

    def test_real_keywords_md_extracts_expected_category_only(self):
        keywords_md = KEYWORDS_PATH.read_text("utf-8")
        # (板块 label, 该板块独有的关键片段, 其余两个板块独有、不应混入的片段)
        cases = (
            ("AI治理", "AI治理能力", ["国家数据局", "数字员工"]),
            ("AI数据", "国家数据局", ["AI治理能力", "数字员工"]),
            ("AI行业", "数字员工", ["AI治理能力", "国家数据局"]),
        )
        for label, own_fragment, other_fragments in cases:
            focused = focus_keywords_md(keywords_md, label)
            self.assertIn(own_fragment, focused)
            # 通用规则必须保留
            self.assertIn("scorer 输出格式约定", focused)
            self.assertIn("过滤规则", focused)
            # 其余板块的高优先级线索不应混入,精简后的文档也应明显短于全文
            for fragment in other_fragments:
                self.assertNotIn(fragment, focused)
            self.assertLess(len(focused), len(keywords_md))


if __name__ == "__main__":
    unittest.main()
