# GitHub代码交付（2026-10-29）

本目录同步当前本地运行的财务查询Agent，含多轮结构化会话记忆、需求确认、受控参数化SQL、只读数据查询、SSE和前端。当前尚非大模型自主规划的完整进阶版，模型仅辅助解析。

代码来自现有项目原样同步；业务SQLite、原始Excel、密钥、config.local.json、runtime会话/审计/备份以及虚拟环境不在交付范围。测试与文档保留现有业务口径和示例，不视作当前财务验收结果。

在新机器使用前须配置本地项目路径和已验收数据库，并核对正式版本入口及knowledge/database_binding.json。仓库不包含私有数据库，不能只克隆仓库就查询财务数据。Key由用户自行保存在本地secrets/deepseek_key.txt；不得提交。真实API测试仍须遵循AGENTS.md逐请求确认。

源码入口app.py；会话协调agent/conversation.py；记忆agent/memory.py；按需查询agent/query_plan.py、query_service.py。结构与使用见docs/13_初级财务查询Agent与会话记忆.md。部分历史测试和文档含旧版本断言，不能替代当前绑定及独立金标准。
