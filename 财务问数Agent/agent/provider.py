"""直接调用DeepSeek；只用于难以解析的输入，0自动重试，不传财务结果或账户字段。"""
import json
import httpx


class Provider:
    def __init__(self,settings):self.settings=settings

    @property
    def enabled(self):return self.settings.key_configured()

    def parse(self,question,prompt):
        key=self.settings.key()
        if not key:return {}
        try:
            with httpx.Client(timeout=45,transport=httpx.HTTPTransport(retries=0),follow_redirects=False) as c:
                response=c.post(self.settings.base_url.rstrip('/')+'/chat/completions',
                    headers={'Authorization':'Bearer '+key},json={
                        'model':self.settings.model,'temperature':0,'max_tokens':1200,
                        'response_format':{'type':'json_object'},'stream':False,
                        'messages':[{'role':'system','content':prompt},{'role':'user','content':question}]})
                if response.status_code!=200:raise ValueError(f'模型接口返回状态{response.status_code}；没有自动重试。')
                body=response.json();choice=body['choices'][0]
                if choice.get('finish_reason') not in ('stop',None):raise ValueError('模型解析未完整结束，请明确对象后重试。')
                result=json.loads(choice['message']['content'])
                if not isinstance(result,dict):raise ValueError('模型返回格式不正确。')
                result['_usage']=body.get('usage')
                return result
        except ValueError:raise
        except Exception:raise ValueError('模型解析未完成，请直接填写项目/楼栋、合同ID或单据号；没有自动重试。') from None
