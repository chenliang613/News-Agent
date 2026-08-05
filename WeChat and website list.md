# 微信公众号 + 官网订阅列表

本文件保留供未来重新启用公众号/官网板块时使用，**既包括微信公众号，也包括官网网站**两类；当前它不在自动排期中。
两类各用不同的时间窗口：

- **「## 微信公众号」小节**（微信公众号来源）→ 抓取近 **48 小时**内发布的文章（`config.yaml` 的 `wechat.max_age_hours`）
- **「## 官网」小节**（企业/机构官网来源）→ 抓取近 **24 小时**内发布的文章（`config.yaml` 的 `wechat.website_max_age_hours`）

抓到后用 DeepSeek 写中文摘要、两类合并按发布时间取最新若干条，经 PushPlus 推送。**不做相关性打分**。

## 怎么填

两个小节下都是「一行一个、`-` 开头」。每行可写 `名称` 或 `名称 | 地址`，地址按下面规则处理：

1. **RSS 地址**（如 `https://36kr.com/feed`）→ 真 feed，抓多篇、带发布时间，窗口过滤准确。**最推荐**。
2. **官网首页 / 文章链接**（普通网页）→ 当成**单页文章**抓（取网页标题/摘要）。
   ⚠️ 这类网页通常**没有发布时间**；系统默认严格模式（`wechat.require_published: true`）会
   **丢弃无发布时间的条目**，所以纯首页链接在严格模式下抓不到内容——要拿内容请尽量用 RSS。
   ⚠️ **微信自家链接（mp.weixin.qq.com/...）抓不到**：微信对服务器请求返回验证页。
3. **只写名称**（不带地址）→ 通过 OPML 映射表（`wechat.resolver.opml_urls`）尝试自动解析；
   默认 OPML 只含约 326 个安全类号，其它多半解析不到、会跳过。要对任意号生效需自建 wechat2rss 并配置其 OPML。

地址以 `/` 开头会被当作 RSSHub 路由（拼到 `config.yaml` 的 `rsshub.instance` 后）。
**想改某条的时间窗口，把它在两个小节之间移动即可。** 只有这两个小节下、`-` 开头的行会被识别；说明文字会被忽略。

## 微信公众号

- 宇十一 | https://mp.weixin.qq.com/mp/homepage?__biz=MzY4NzAzODM2NA==&hid=3&sn=e318ed625dfe741b5dd05dd7f14a53fc&scene=18#wechat_redirect
- 36氪 | https://36kr.com/feed
- AI前线 | https://www.infoq.cn/feed/ai
- InfoQ | https://www.infoq.cn/feed
- 量子位 | https://www.qbitai.com/feed
- 新智元 | https://link.baai.ac.cn/@AI_era
- DeepTech深科技 | https://www.mittrchina.com
- 虎嗅APP | https://rss.huxiu.com/
- 51CTO | https://www.51cto.com/

## 官网

- 世界互联网大会 | https://www.wicinternet.org
- 中国信通院CAICT | https://www.caict.ac.cn/
- 中国人工智能产业发展联盟 | https://aiiaorg.cn/
- 网易科技 | https://tech.163.com
- 国家数据局 | https://www.nda.gov.cn
- 全国数标委 | https://www.tc609.org.cn/
- 清华大学人工智能国际治理研究 | https://aiig.tsinghua.edu.cn/
- 亚马逊云科技 | https://aws.amazon.com/cn/blogs/china/feed/
