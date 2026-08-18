# News Agent

面向 AI 治理、AI 数据和 AI 行业落地的个人情报 Agent。它从 RSS、Google News 与 RSSHub 发现候选新闻，抓取正文后完成研究价值终评、正文去重和事件聚合，再通过 PushPlus 推送到微信。

## 工作流

```text
RSS / Google News / RSSHub
  → 时间过滤、URL/标题初步去重、历史去重
  → DeepSeek 标题与摘要粗筛
  → 候选文章正文抓取（失败重试）+ 正文指纹去重
  → DeepSeek 研究价值终评 + event_key 事件聚合
  → 中文摘要 + 本周观察/持续跟踪
  → Markdown 落盘 + PushPlus 推送 + 已处理状态记录
```

终评按以下权重计算：相关性 35%、新颖性 30%、证据强度 25%、可行动性 10%。正文高度相似的转载即使标题不同也只保留一篇。

## 自动排期

| 北京时间 | 板块 | 配置 key |
|---|---|---|
| 周一 07:00 | AI 治理 | `governance` |
| 周三 07:00 | AI 数据 | `data` |
| 周五 07:00 | AI 行业落地 | `industry` |

Python weekday 使用 `0=周一` 至 `6=周日`；GitHub Actions 使用 UTC，当前 cron 为 `0 23 * * 0,2,4`，即上表的北京时间 07:00。

当前默认配置、GitHub Actions 和 webhook 仅面向上述三个板块；历史的公众号/官网配置保留但未启用。

## 本地运行

```bash
pip install -r requirements.txt

export DEEPSEEK_API_KEY=sk-...
export PUSHPLUS_TOKEN=...

# 按当天排期运行；非排期日自动退出
python -m news_agent.main

# 干跑：仍会调用 DeepSeek，但不推送、不更新 sent 状态
python -m news_agent.main --dry-run --category governance

# 模拟某一天（0=周一..6=周日）
python -m news_agent.main --weekday 0 --dry-run

# 只测试 PushPlus
python -m news_agent.main --test-push
```

正常推送包含事件卡片、研究价值维度、同事件参考来源、本周观察、持续跟踪和本次运行摘要。若没有新文章、没有候选通过粗筛或没有内容达到终评阈值，正常运行会发送一条状态通知。

## GitHub Actions

在仓库 Settings → Secrets and variables → Actions 中设置：

- `DEEPSEEK_API_KEY`
- `PUSHPLUS_TOKEN`

工作流会在每次运行后提交 `state/` 与 `output/`，使 URL 去重跨运行生效。可在 Actions 页面手动触发，并指定板块或仅测试推送。

## 配置指南

### `keywords.md`

定义“什么值得推送”。每个板块建议包含研究目标、高优先级事件、重点机构/标准/场景、降权或排除项，以及高价值判断标准。它影响模型筛选，不直接决定 Google News 搜索范围。

### `config.yaml`

- `schedule.weekday_category`：自动排期。
- `google_news_queries`：每个板块的发现查询词；使用“主体 + 事件/动作”而非宽泛词。
- `push.max_articles` / `push.min_score`：单次上限与终评阈值。
- `research_filter`：粗筛阈值、正文候选上限、抓取并发、正文长度和正文去重阈值。
- `insights.enabled`：是否生成本周观察与持续跟踪。
- `sources.max_age_hours`：普通新闻时间窗口，默认 168 小时。
- `state.retention_days`：历史去重记录保留时间。

RSS 源可声明来源等级：

```yaml
rss_feeds:
  - name: "官方机构"
    url: "https://example.com/rss.xml"
    tier: primary   # primary | trusted
```

`primary` 用于官方机构、标准组织、公司原始发布；`trusted` 用于可信媒体和研究机构；Google News 与 RSSHub 自动标为 `discovery`。终评会结合来源等级判断证据强度，但一手来源也必须有正文事实支持。

### `WeChat and website list.md`

此文件及 `wechat` 配置保留为将来扩展用，当前不参与自动排期、GitHub Actions 手动选项或 webhook 关键词映射。

## 微信 webhook

```bash
python -m news_agent.webhook
python -m news_agent.webhook --port 9000
```

程序的 webhook 可用于自建调用方按板块触发新闻任务。支持的展示名以 `category_labels` 为准。

## 安全的关键词反馈闭环

在 GitHub 的 **Issues → New issue → 新闻筛选反馈** 提交文章评分和建议词。每周六北京时间 07:20，工作流只汇总带 `news-feedback` 标签、且提交者为仓库 `OWNER`、`MEMBER` 或 `COLLABORATOR` 的反馈，调用 DeepSeek 生成受限的候选规则，并创建独立 PR。

工作流只会替换 `keywords.md` 中带内部标记的“反馈驱动的调优规则”区，不能改写既有研究目标或全局过滤规则；它不会直接合并 PR。合并候选 PR 后，对应反馈 Issue 会自动关闭。私有仓库可天然限制提交者；公开仓库中，外部用户的反馈会被忽略。

## 质量回归与调优

```bash
python -m unittest discover -s tests -v
```

`tests/fixtures/quality_cases.json` 是人工标注的质量回归集。修改关键词、模型提示词、来源或阈值后，应补充相应样本并运行测试。

常用调优方向：

- 推送太少：适度降低 `push.min_score` 或 `research_filter.coarse_min_score`。
- 推送偏题：在 `keywords.md` 增加明确的排除项和高价值样例。
- 转载过多：提高 `research_filter.body_dedupe_threshold`（例如 `0.95`）。
- 主题相近但不该合并：降低正文去重阈值的激进程度，即提高该值。

## 注意事项

- Google News 与 RSSHub 是发现渠道，可能受限或暂时失败；RSS 抓取会重试 3 次，单个来源失败不会中断本次运行。
- 正文抓取受站点反爬、付费墙和页面结构影响；抓取失败时系统退回 RSS 摘要，并在终评中限制证据分。
- PushPlus 免费版存在每日配额，请按实际套餐使用。
