
## 一、AI 治理（governance）   --  每周一获得AI治理相关新闻和动态

**主题**：AI 监管法规、安全对齐、企业治理、地缘政治、治理标准的演进与落地。

**关注角度**：
- **各国 AI 监管法案 / 政策**：欧盟 AI Act 实施细则、中国《生成式人工智能服务管理暂行办法》及配套标准、美国 AI 行政令与 AISI、英国 AI Safety Institute、日韩 AI 法案
- **AI 安全 / 对齐 / 红队**：前沿模型安全评测（preparedness/RSP）、对齐研究进展、Anthropic / OpenAI / DeepMind 等发布的安全或负责任扩展报告、第三方红队结果
- **企业 AI 治理实践**：大企业内部 AI 使用规范、模型审计、AI 伦理委员会、AI 风险管理框架（NIST AI RMF、ISO 42001 落地案例）
- **AI 出口管制 / 地缘政治**：美中半导体管制、模型权重出口、技术封锁、对应反制
- **AI 治理标准**：ISO/IEC JTC1 SC42 进展、欧洲 CEN-CENELEC JTC21、中国 TC260（信安标委）相关 AI 标准、IEEE / NIST 标准

**关键词**：AI Act、AI 监管、AI governance、AI safety、AI alignment、red team、responsible scaling、AI 出口管制、export control、芯片管制、AI 标准、SC42、JTC21、TC260、NIST AI RMF、ISO 42001、生成式人工智能管理办法、AI 伦理

## 二、AI 数据（data）  -- 每周二获得AI数据的相关新闻和动态

**主题**：AI 的"燃料"和"载体"——训练数据、算力基础设施、数据要素市场、检索/记忆栈。

**关注角度**：
- **训练数据 / 数据集 / 数据合规**：训练语料来源争议、版权诉讼（NYT vs OpenAI 一类）、合成数据、新发布的开源/商业数据集、数据隐私合规
- **数据中心 / 算力基础设施**：超大规模数据中心建设、电力供应与选址、芯片采购与 capex（Nvidia/AMD/华为昇腾/寒武纪）、AI infra 公司动态
- **数据要素市场 / 数据资产化**（主要为中国语境）：数据要素 X 行动、数据交易所、企业数据资产入表、公共数据授权运营
- **向量数据库 / RAG / 知识库技术**：Pinecone / Weaviate / Milvus / Qdrant 等存储与检索栈、企业级 RAG 架构演进
- **Agent 数据 / RAG 数据 / 智能终端数据**：Agent 的上下文工程与记忆系统、端侧数据（手机/汽车/IoT）的采集与利用、跨 Agent 数据流转

**关键词**：AI capex、AI infra、data center、算力中心、芯片采购、训练数据、training data、synthetic data、数据版权、数据合规、数据要素、数据交易、数据资产入表、vector database、RAG、knowledge base、Pinecone、Milvus、Weaviate、Agent memory、context engineering

## 三、AI 行业落地（industry） -- 每周三获得AI行业的相关新闻和动态

**主题**：AI 在各行业的真实部署、产业格局、Agent 与编程智能的产业化。

**关注角度**：
- **行业智能化案例**：制造、医疗、金融、零售、教育、能源、政务、法律等行业的真实落地（不是 PoC、不是 demo），强调具体公司、ROI、失败教训
- **行业大模型 / 垂直 AI**：行业大模型、垂直 Agent、行业知识库、行业 RAG 的企业级部署
- **AI 产业格局与商业化**：硬件 / 模型 / 应用三层的价值流动；OpenAI、Anthropic、Google、Meta、DeepSeek、Qwen、智谱、月之暗面、Mistral 的商业化与收入数据；Cursor、Perplexity、Manus、Devin 等应用公司的 ARR / 增长
- **Agent 与编程智能**：Claude Code、Cursor、Windsurf、Devin、Codex 等编程 Agent 进展；多 Agent 系统与框架（LangGraph、AutoGen、CrewAI）；Agent 在企业的落地案例
- **AI 项目方法论**：行业 AI 项目成败的方法论、关键路径、组织变革

**关键词**：行业大模型、行业智能化、AI 落地、产业 AI、企业 AI、AI 转型、智能制造、AI+医疗、AI+金融、vertical AI、enterprise AI、AI Agent、coding agent、agentic AI、multi-agent、MCP、computer use、AI ARR、AI valuation、AI capex（如果聚焦在投资回报/产业格局而非数据中心本身）

## 四、过滤规则（请勿推送，scorer 给 0-3 分）

- 纯模型跑分、benchmark 对比，除非有产业意义
- 公司内部八卦、人事变动，除非是关键人物（CEO / CTO / 首席科学家 / 监管负责人）
- AI 绘画 / AI 视频 的纯娱乐应用与同质化产品发布
- 加密货币、Web3、元宇宙相关
- 标题党、营销软文、纯翻译稿、二手转述
- 已经超过 48 小时的旧闻
- 通用财经/宏观新闻里 AI 只是被一笔带过的内容

## 五、scorer 输出格式约定

对每条新闻必须返回：
```json
{
  "id": <输入序号>,
  "score": <0-10 数字,可一位小数>,
  "categories": ["governance" | "data" | "industry", ...],
  "reason": "<不超过30字的中文打分理由>"
}
```

规则：
- `score < 4` 时 `categories` 可以为空数组
- `score >= 4` 必须至少一个 category
- 跨板块新闻给多个 categories（如出口管制类常常是 `["governance", "data"]`）
- 三板块都不沾边但又确实有价值的（罕见），仍可空 categories，会被丢弃不进推送
