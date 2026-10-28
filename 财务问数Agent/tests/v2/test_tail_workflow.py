import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from agent.engine import Agent,parse_intent
from agent.settings import Settings
from agent.tail_workflow import TailWorkflow,statement_request

class Local:
    enabled=False

@pytest.mark.parametrize('question',[
 '为石厦公社西村167号生成尾期对账单',
 '导出退出结算Excel表格',
 '帮我制作一份尾期结算单','我需要一份尾期对账单',
])
def test_explicit_generation_intent(question):
    assert statement_request(question)
    assert parse_intent(question)[0]=='generate_statement'

@pytest.mark.parametrize('question',[
 '不需要一份尾期对账单','不要生成尾期对账单，只查费用','怎么生成尾期对账单','尾期对账流程是什么',
 '查询尾期对账单金额','这个楼栋应付多少','别生成尾期对账单',
])
def test_mentions_and_negations_do_not_generate(question):
    assert not statement_request(question)

def test_route_building_to_exact_workflow_once(monkeypatch):
    a=Agent(Settings.load(),model=Local());calls=[]
    def fake(dimension,**kw):
        calls.append(dimension)
        return {'status':'statement_ready','answer':'已生成待复核稿','artifact':{'id':'a'*32,'url':'/api/statements/'+'a'*32,'name':'测试.xlsx'}}
    monkeypatch.setattr(a.tail_workflow,'generate',fake)
    r=a.chat({'message':'为石厦公社西村167号生成尾期对账单'})
    assert r['status']=='statement_ready' and len(calls)==1
    from agent.entities import norm
    assert norm(calls[0]['building'])==norm('西村167号') and calls[0]['building_id']

def test_project_must_select_one_building_before_generation(monkeypatch):
    a=Agent(Settings.load(),model=Local());calls=[]
    monkeypatch.setattr(a.tail_workflow,'generate',lambda d,**k:calls.append(d) or {'status':'statement_ready','answer':'模拟'})
    first=a.chat({'message':'为石厦公社生成尾期对账单'})
    assert first['status']=='clarify_scope' and len(first['choices'])==15 and not calls
    second=a.chat({'session_id':first['session_id'],'selection':first['choices'][0]['id']})
    assert second['status']=='statement_ready' and len(calls)==1

def test_period_not_silently_discarded_for_statement(monkeypatch):
    a=Agent(Settings.load(),model=Local())
    monkeypatch.setattr(a.tail_workflow,'generate',lambda *a,**k:pytest.fail('不得忽略期间出账'))
    r=a.chat({'message':'为石厦公社西村167号生成2026年1月尾期对账单'})
    assert r['status']=='clarify_period'

def test_model_intent_cannot_trigger_unrequested_output(monkeypatch):
    class Fake:
        enabled=True
        def parse(self,*args):return {'intent':'generate_statement','scope_type':'project','project':'石厦公社'}
    a=Agent(Settings.load(),model=Fake())
    monkeypatch.setattr(a.tail_workflow,'generate',lambda *a,**k:pytest.fail('模型不可擅自生成'))
    r=a.chat({'message':'帮我看看那边费用'})
    assert r['status']=='clarify_metric'

def test_fixed_bundle_fingerprints_validate_and_invalid_id_does_not_spawn():
    workflow=TailWorkflow();bundle,source=workflow.verify()
    assert (bundle/'对账单最终工作流.py').is_file() and source.is_dir()
    with pytest.raises(ValueError,match='真实ID'):
        workflow.generate({'building_id':'../../file','project':'p','building':'b'})

def test_download_rejects_incomplete_bad_tokens_and_tampering(tmp_path):
    workflow=TailWorkflow();workflow.outputs=tmp_path
    for token in ['../secrets','xyz','a'*31]:
        with pytest.raises(ValueError):workflow.download(token)
    token='a'*32;job=tmp_path/token;job.mkdir();(job/'statement.xlsx').write_bytes(b'mock')
    receipt=job/'receipt.json';receipt.write_text(json.dumps({'status':'blocked'}))
    with pytest.raises(ValueError):workflow.download(token)
    receipt.write_text(json.dumps({'status':'completed','output_sha256':hashlib.sha256(b'mock').hexdigest()}))
    assert workflow.download(token)==job/'statement.xlsx'
    (job/'statement.xlsx').write_bytes(b'changed')
    with pytest.raises(ValueError):workflow.download(token)

def test_download_http_unknown_is_404():
    import app
    with TestClient(app.app) as client:
        assert client.get('/api/statements/'+'f'*32).status_code==404
        assert client.get('/api/statements/bad').status_code==404

def test_subprocess_cancelled_before_start():
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(ValueError,match='取消'):
        TailWorkflow().generate({'building_id':'11111111-1111-1111-1111-111111111111'},cancelled=cancelled)


def test_model_parse_contract_contains_intent_and_cannot_guess_building():
    calls=[]
    class Fake:
        enabled=True
        def parse(self,q,prompt):
            calls.append((q,prompt))
            return {'intent':'generate_statement','scope_type':'building','project':'石厦公社','building':'西村167号'}
    a=Agent(Settings.load(),model=Fake())
    r=a.chat({'message':'我需要一份尾期对账单'})
    assert len(calls)==1 and 'generate_statement' in calls[0][1]
    assert r['status']=='clarify_scope' and len(r['choices'])==5
    assert all(c['score']<=90 for c in r['choices'])


def test_regular_financial_query_does_not_invoke_workflow(monkeypatch):
    a=Agent(Settings.load(),model=Local())
    monkeypatch.setattr(a.tail_workflow,'generate',lambda *a,**k:pytest.fail('普通问数不应出账'))
    assert a.chat({'message':'石厦公社2026年1月实收多少钱'})['status']=='answered'
