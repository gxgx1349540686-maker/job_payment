import json
import os

# 财务问题、项目楼栋及SQL不发送到追踪平台。
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"


class DeepSeek:
    def __init__(self, settings):
        self.settings = settings

    @property
    def enabled(self):
        return bool(self.settings.key())

    def json(self, system, data):
        from langchain_openai import ChatOpenAI
        model = ChatOpenAI(api_key=self.settings.key(), base_url=self.settings.base_url,
                           model=self.settings.model, temperature=0, timeout=45,
                           max_retries=0, max_tokens=2500)
        response = model.bind(response_format={"type":"json_object"}).invoke([
            ("system",system+"\n只输出json对象，不要Markdown。"),
            ("human",json.dumps(data,ensure_ascii=False))])
        content = response.content
        if not isinstance(content,str) or not content.strip():
            raise ValueError("模型返回空内容，请重试")
        return json.loads(content)
