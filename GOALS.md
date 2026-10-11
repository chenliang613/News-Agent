# Project goal {#intake-goal status=active}

运营面向 AI 治理、AI 数据、AI 行业落地与 AI 终端的个人情报 Agent：按周排期自动发现、筛选、终评并聚合新闻，把高质量中文摘要推送到微信，并用质量回归与关键词反馈闭环持续改进推送质量。

**成功标准**

- 四个板块（AI治理 / AI数据 / AI行业 / AI终端）按北京时间周一、周三、周五、周日 12:00 运行；也可经 GitHub Actions 手动触发、本机 webhook 或 Hermes 微信指令触发。
- 每次运行产出中文摘要（含研究价值维度、同事件参考来源、本周观察、持续跟踪）并推送到微信；无新内容或无可推送内容时发送状态通知。
- 正文去重与事件聚合生效：同一事件只保留一篇主稿，高度相似的转载不再重复推送。
- 关键词、提示词、来源或阈值变更后，`python -m unittest discover -s tests` 全绿，并按需补充 `tests/fixtures/quality_cases.json` 样本。

**来源与核对说明**

- 目标与成功标准来自仓库权威材料：`README.md`（项目定位、工作流、排期、配置指南）、`config.yaml`（schedule / push / research_filter / insights / category_labels）与 `news_agent/` 实现。
- 已有实现与未完成事项的来源引用和当前状态登记在 `.awr/intake/work-ledger.yaml`；未经证据验证的进度不视为完成。
- 历史的公众号/官网配置（`WeChat and website list.md`、`wechat` 配置）保留但未启用，不构成当前交付范围。
