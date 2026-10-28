# 楼栋财务问数Agent：GitHub调研报告

调研日期：2026年10月17日。范围是自然语言问数、业务规则检索、SQLite只读查询及网页交互。以下结论来自实际打开的项目README和官方文档；未克隆、执行或复制这些项目的业务代码，未采用星标数作为选型标准。

## 相关项目及适配判断

| 项目 | 官方资料所示能力 | 对本项目有价值的做法 | 本次选择 |
|---|---|---|---|
| [LangChain](https://github.com/langchain-ai/langchain) / [LangGraph SQL Agent教程](https://docs.langchain.com/oss/python/langgraph/sql-agent) | 工具调用及可自定义的SQL工作流；教程强调数据库权限收窄，并把查表、生成、检查、执行做成节点 | 通过固定节点强制澄清和校验，避免完全依赖提示词 | 使用LangGraph工作流和LangChain模型适配；不直接把全部物理表交给通用SQL Agent |
| [Vanna](https://github.com/vanna-ai/vanna) | 当前README为2.0；提供用户感知的工具权限、FastAPI接入、流式表格和网页组件 | 问数结果不只有一句话，应能展示明细、SQL和来源；后续多人使用需鉴权与审计 | 借鉴交互与权限分层，自行编写本地网页；不引入整个Vanna平台 |
| [WrenAI](https://github.com/Canner/WrenAI) | 强调业务定义、度量、关联关系及示例构成可版本化的语义与上下文层 | “应收”定义、合同有效期、同一金额的重复展示，必须在数据模型之外明确约束 | 编写项目专用规则库、指标登记表及审核SQL；不引入其完整分析部署体系 |
| [SQLGlot](https://github.com/tobymao/sqlglot) | SQL解析及抽象语法树处理 | 不能仅通过开头是否为SELECT判断SQL可否执行 | 用AST检查单语句、表访问范围，并与指标审核模板比对 |

补充参考：[Vanna旧版README](https://github.com/vanna-ai/vanna/blob/main/README_LEGACY.md)将DDL、业务说明、SQL示例作为取数上下文。这是旧版方法说明，不应把旧版API直接当作2.0接口使用。

## 本项目与通用演示项目的区别

本库有228张物理表，其中包括历史、原始资料、辅助来源和核对档案。它们不能一律作为可叠加的金额事实表。T01有展示拆行，直接关联后汇总可能放大金额；付款计划也不能替代成功实付。数据库能够执行一条SQL，并不能证明其业务含义正确。

因此，本次选型判断是：以LangGraph编排固定步骤，以项目规则与指标目录约束取数；模型负责理解及SQL生成，确定性代码负责权限、楼栋选择、账期、金额精度和输出依据。这是针对本项目的工程判断，并非上述框架保证财务正确。

## DeepSeek接入

[DeepSeek官方接入文档](https://api-docs.deepseek.com/)提供OpenAI兼容接口，base_url为`https://api.deepseek.com`。本次读取的文档使用`deepseek-flash`作为示例模型，因此配置采用该名称，保留可修改项，不沿用旧教程的模型名称。

[JSON Output文档](https://api-docs.deepseek.com/guides/json_mode/)要求设置`response_format={"type":"json_object"}`，在提示词中说明JSON并给出结构示例，同时提醒空响应和长度截断仍需处理。实现中会解析并检查响应；空内容、错误意图或不合规SQL不会直接执行。

Key只由后端从本地TXT或环境变量读取，不出现在浏览器、日志或调研文档中。发送给DeepSeek的内容限定为用户问题、提取的项目楼栋文本、规则和审核查询结构；查询明细、银行卡字段及完整SQLite文件不作为模型输入。规则中的表名和业务口径会作为必要上下文传输。

## 轻量实现与后续扩展

第一版：本地FastAPI、原生HTML/JS聊天页、LangGraph、LangChain模型适配、SQLGlot、SQLite业务库、独立SQLite规则向量库。依赖锁定到本次安装并验证的版本。

向量库采用本地字符TF-IDF稀疏向量和余弦检索，适合中文字段名、表名及项目规则的精确词项检索；它不具备通用语义Embedding模型的同义理解能力。关键规则及所选指标规则强制加入上下文，避免仅靠相似度漏检。后续可用中文Embedding及Chroma/Qdrant替换检索层，但仍须保留强制规则。

后续建设网站可以保留`/api/chat`接口，但需要补充身份认证、项目权限、部署密钥、访问审计、并发查询隔离和数据库版本更新流程。本次交付仅本机使用，未部署网站，未连接任何远程仓库写入。
