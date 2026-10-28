import pytest
from agent.engine import Agent,parse_intent
from agent.settings import Settings

@pytest.mark.parametrize('question',['生成石厦公社月度账单','帮我制作本月月度对账单','我需要一份每月账单'])
def test_monthly_generation_is_reserved_and_does_not_call_tools(monkeypatch,question):
    class Model:
        enabled=True
        def parse(self,*args):pytest.fail('尚未实现功能不能调用模型')
    a=Agent(Settings.load(),model=Model())
    monkeypatch.setattr(a.db,'run',lambda *a,**k:pytest.fail('不能以普通查询冒充生成'))
    monkeypatch.setattr(a.tail_workflow,'generate',lambda *a,**k:pytest.fail('不能调用尾期流程替代'))
    r=a.chat({'message':question})
    assert r['status']=='feature_unavailable' and 'artifact' not in r and 'data' not in r

def test_monthly_financial_query_remains_query():
    assert parse_intent('石厦公社每月实收应付实付是多少')[0]=='finance'
    assert parse_intent('为石厦公社西村167号生成尾期对账单')[0]=='generate_statement'
