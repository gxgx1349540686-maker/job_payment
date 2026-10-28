import copy
import json
import time
import uuid
import contextlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import app
from agent.engine import Agent
from agent.memory import SessionStore,fresh
from agent.settings import Settings
from agent.r35_database import cents

class Local:enabled=False

@pytest.fixture(scope='module')
def a(tmp_path_factory):
    return Agent(Settings.load(),model=Local(),memory_path=tmp_path_factory.mktemp('memory')/'sessions.sqlite')

def ask(a,q,sid=None,**kw):return a.chat(dict(message=q,session_id=sid,**kw))
def slots(r):return r['requirement_state']['slots']

def test_explicit_request_only_target_and_sql_projection(a):
    r=ask(a,'新南公社14栋2026年1月应付多少')
    assert r['status']=='answered' and slots(r)['metrics']==['payable']
    assert r['data']['rows']==[]
    queries=r['data']['sql_records']
    assert len(queries)==2 and all('T09' not in q['sql'] and 'T10' not in q['sql'] for q in queries)
    assert 'receipts_cents' not in queries[0]['sql']
    assert '实付登记：' not in r['answer'] and '有效核销（账单月）：' not in r['answer']
    assert r['data']['summary_rows'][0]['payable_yuan']=='81091.42'

def test_month_followup_inherits_scope_and_metric(a):
    r=ask(a,'新南公社14栋2026年1月应付多少');r2=ask(a,'3月呢',r['session_id'])
    assert r2['status']=='answered' and slots(r2)['scope']['id']==slots(r)['scope']['id']
    assert slots(r2)['metrics']==['payable'] and slots(r2)['start']=='202603'
    assert r2['data']['summary_rows'][0]['payable_yuan']=='99877.00'

def test_correction_and_negative_output_constraint(a):
    r=ask(a,'新南公社14栋2026年1月每月实收和应付')
    r=ask(a,'改为3月，只看应付合计，不要实收和实付，不要明细',r['session_id'])
    assert r['status']=='answered' and slots(r)['metrics']==['payable']
    assert slots(r)['start']=='202603' and r['data']['rows']==[]
    assert r['requirement_state']['output_spec']['mode']=='total'
    assert r['requirement_state']['output_spec']['details'] is False

def test_ambiguity_before_scope_no_sql_until_confirmation(a,monkeypatch):
    calls=[];old=a.db.run
    monkeypatch.setattr(a.db,'run',lambda *args,**kwargs:(calls.append(args[0].name) or old(*args,**kwargs)))
    r=ask(a,'查费用');assert r['status']=='clarify_metric' and not calls
    r=ask(a,'应付',r['session_id']);assert r['status']=='clarify_scope' and not calls
    r=ask(a,'新南公社14栋2026年1月',r['session_id'])
    assert r['status']=='confirm_requirements' and not calls
    r=ask(a,'确认',r['session_id']);assert r['status']=='answered' and calls

def test_amend_pending_confirmation_keeps_other_slots(a):
    r=ask(a,'新南公社14栋2026年1月费用');r=ask(a,'应付',r['session_id'])
    assert r['status']=='confirm_requirements'
    r=ask(a,'不是1月，改为3月，只看合计',r['session_id'])
    assert r['status']=='confirm_requirements' and slots(r)['start']=='202603' and slots(r)['metrics']==['payable']
    r=ask(a,'确认',r['session_id']);assert r['data']['rows']==[] and r['data']['summary_rows'][0]['payable_yuan']=='99877.00'

def test_metric_choices_and_numeric_free_text(a):
    r=ask(a,'新南公社14栋2026年1月费用')
    r=ask(a,'第三个',r['session_id']);assert r['status']=='confirm_requirements' and slots(r)['metrics']==['payable']
    r=ask(a,'',r['session_id'],selection='confirm:yes',state_version=r['state_version'])
    assert r['status']=='answered'

def test_missing_month_defaults_all_not_current(a):
    r=ask(a,'新南公社14栋应付多少')
    assert slots(r)['start'] is None and slots(r)['end'] is None
    assert r['data']['period']['label']=='全部已存日期'

def test_change_explicit_object_defaults_period_again(a):
    r=ask(a,'新南公社14栋2026年1月应付多少')
    r=ask(a,'石厦公社西村167号应付多少',r['session_id'])
    assert slots(r)['start'] is None and slots(r)['end'] is None

def test_this_project_upgrades_scope(a):
    r=ask(a,'新南公社14栋2026年1月应付多少')
    r=ask(a,'这个项目呢',r['session_id'])
    assert r['status']=='answered' and slots(r)['scope']['kind']=='project' and slots(r)['metrics']==['payable']

def test_paid_does_not_automatically_query_writeoffs(a):
    r=ask(a,'新南公社14栋2026年3月只看实付合计')
    assert r['status']=='answered' and r['data']['summary_rows'][0]['paid_yuan']=='99877.00'
    assert not any('T10' in q['sql'] for q in r['data']['sql_records'])

def test_monthly_only_requested_columns(a):
    r=ask(a,'新南公社14栋2026年1月至3月每月应付多少')
    assert len(r['data']['rows'])==3
    assert all('paid_yuan' not in row and 'receipts_yuan' not in row for row in r['data']['rows'])

def test_document_single_amount_not_contract_allocation(a):
    r=ask(a,'单据NPSZFS202601130001已付多少')
    assert r['status']=='answered' and '36663.04' in r['answer']
    assert not any('T09楼栋月度' in q['title'] for q in r['data']['sql_records'])

def test_contract_query_avoids_finance_tables(a):
    r=ask(a,'新南公社14栋2026年1月执行合同')
    assert r['status']=='answered' and r['data']['intent']=='contracts'
    assert all('月度金额分' not in q['sql'] and 'T10' not in q['sql'] for q in r['data']['sql_records'])

def test_completeness_is_targetable(a):
    r=ask(a,'新南公社14栋信息齐全吗')
    assert r['status']=='answered' and r['data']['intent']=='completeness' and 'QA测试口径' in r['answer']

def test_persistence_across_agent_restart(a):
    r=ask(a,'新南公社14栋2026年1月应付多少',_user_id='restart-test')
    b=Agent(Settings.load(),model=Local(),memory_path=a.memory.path)
    r2=ask(b,'3月呢',r['session_id'],_user_id='restart-test')
    assert r2['status']=='answered' and slots(r2)['metrics']==['payable'] and slots(r2)['start']=='202603'

def test_owner_isolation(a):
    r=ask(a,'新南公社14栋2026年1月应付多少',_user_id='alice')
    with pytest.raises(ValueError,match='会话不存在'):ask(a,'3月呢',r['session_id'],_user_id='bob')

def test_idempotent_request_never_executes_twice(a,monkeypatch):
    rid=uuid.uuid4().hex;r=ask(a,'新南公社14栋2026年1月应付多少')
    payload=dict(message='3月呢',session_id=r['session_id'],request_id=rid,state_version=r['state_version'])
    once=a.chat(payload)
    monkeypatch.setattr(a.db,'run',lambda *_:(_ for _ in ()).throw(AssertionError('duplicate SQL')))
    twice=a.chat(payload);assert twice==once
    with pytest.raises(ValueError,match='不同内容'):a.chat(dict(payload,message='4月呢'))

def test_old_version_and_old_selection_rejected(a):
    r=ask(a,'新南公社14栋2026年1月应付多少')
    ask(a,'3月呢',r['session_id'])
    with pytest.raises(ValueError,match='旧版需求'):ask(a,'4月呢',r['session_id'],state_version=r['state_version'])
    with pytest.raises(ValueError,match='有效选项'):ask(a,'',r['session_id'],selection='metric:paid')

def test_rolling_context_bounded_and_no_financial_assistant_history(a):
    r=ask(a,'新南公社14栋2026年1月应付多少')
    for _ in range(7):r=ask(a,'3月呢',r['session_id'])
    state=a.memory.load('local_cli',r['session_id'],a.binding['database_sha256'])
    assert len(state['recent_messages'])==12 and len(state['summary'])<=2400
    assert all('99877.00' not in m['content'] for m in state['recent_messages'] if m['role']=='assistant')

def test_expired_and_cancelled_state(a):
    r=ask(a,'新南公社14栋2026年1月应付多少');r=ask(a,'取消',r['session_id'])
    assert r['status']=='cancelled' and not slots(r)
    with contextlib.closing(a.memory.connect()) as c:
        c.execute('UPDATE sessions SET updated=? WHERE session_id=?',(time.time()-90000,r['session_id']));c.commit()
    with pytest.raises(ValueError,match='过期'):ask(a,'3月呢',r['session_id'])

def test_concurrent_claim_is_serial_and_cas(tmp_path):
    store=SessionStore(tmp_path/'memory.sqlite');s=fresh('u','hash');store.claim(s,'one',{})
    with pytest.raises(ValueError,match='仍在处理'):store.claim(s,'two',{})
    response={'status':'draft','answer':'x'};store.save(s,response,'one')
    stale=copy.deepcopy(s);stale['version']=0
    with pytest.raises(ValueError,match='会话已更新'):store.claim(stale,'three',{})

def test_mock_model_receives_only_bounded_working_context(a):
    class Parser:
        enabled=True
        def parse(self,q,prompt):
            data=json.loads(q)
            assert 'working_state' in data and len(data['working_state']['recent_messages'])<=6
            assert 'SELECT ' not in q and '81091.42' not in q
            return {'intent':'finance','metrics':['payable'],'scope_type':'building','project':'新南公社','building':'14栋'}
    old=a.model;a.model=Parser()
    try:
        r=ask(a,'帮我核对那个物业的开支')
        assert r['status']=='confirm_requirements' and r['diagnostics']['model_calls']==1
        r=ask(a,'确认',r['session_id']);assert r['status']=='answered'
    finally:a.model=old

def test_http_cookie_identity_and_resume_endpoint(a,monkeypatch):
    monkeypatch.setattr(app,'_agent',a)
    with TestClient(app.app) as alice,TestClient(app.app) as bob:
        r=alice.post('/api/chat',json={'message':'新南公社14栋2026年1月应付多少','user_id':'forged'})
        assert r.status_code==200
        data=r.json();sid=data['session_id']
        assert 'HttpOnly' in r.headers['set-cookie']
        assert alice.get('/api/sessions/'+sid).status_code==200
        assert bob.get('/api/sessions/'+sid).status_code==404
        assert 'user_id' not in data['requirement_state']
        stream=alice.post('/api/chat/stream',json={'session_id':sid,'message':'3月呢','state_version':data['state_version']})
        assert 'event: result' in stream.text and '99877.00' in stream.text


def test_unsupported_filter_not_silently_ignored(a):
    r=ask(a,'新南公社14栋2026年1月至3月只看应付超过10万元的月份')
    assert r['status']=='clarify_requirement' and not r.get('data')
    assert r['requirement_state']['constraints']['unsupported_filter']
    r=ask(a,'取消筛选，直接按月查询',r['session_id'])
    assert r['status']=='confirm_requirements'
    r=ask(a,'确认',r['session_id']);assert len(r['data']['rows'])==3


def test_chinese_month_and_followup_year_inheritance(a):
    r=ask(a,'新南公社14栋2026年三月应付多少')
    assert r['status']=='answered' and slots(r)['start']=='202603'
    r=ask(a,'新南公社14栋2025年1月应付多少')
    r=ask(a,'三月呢',r['session_id']);assert slots(r)['start']=='202503'


def test_confirmed_request_has_explicit_confirmation_items(a):
    r=ask(a,'新南公社14栋2026年1月应付多少')
    confirmed=r['requirement_state']['confirmed_items']
    assert confirmed['metrics']['confirmed'] is True and confirmed['metrics']['value']==['payable']
    assert confirmed['output_spec']['confirmed'] is True


def test_database_change_invalidates_confirmation(a):
    r=ask(a,'新南公社14栋2026年1月费用');r=ask(a,'应付',r['session_id'])
    with contextlib.closing(a.memory.connect()) as c:
        row=c.execute('SELECT state FROM sessions WHERE session_id=?',(r['session_id'],)).fetchone()
        state=json.loads(row[0]);state['database_sha256']='old'
        c.execute('UPDATE sessions SET state=? WHERE session_id=?',(json.dumps(state),r['session_id']));c.commit()
    r=ask(a,'确认',r['session_id'])
    assert r['status']=='clarify_scope' and not r.get('data') and '数据版本已变化' in r['answer']


def test_noop_initial_confirmation_does_not_query(a):
    r=ask(a,'确认');assert r['status']=='clarify_requirement' and not r.get('data')


def test_difference_matches_independent_integer_cent_gold(a):
    r=ask(a,'新南公社14栋2026年3月应付与实付差多少')
    assert r['status']=='answered' and 'difference' in slots(r)['metrics']
    key=slots(r)['scope']['dimension_keys'][0]
    with contextlib.closing(a.db.connect()) as c:
        payable=c.execute('SELECT m.我方应付分 FROM T08 t JOIN T08_月度金额分 m ON m.应付记录ID=t.应付记录ID WHERE t.楼栋维度键=? AND t.归属月份=?',(key,'202603')).fetchone()[0]
        paid=c.execute('SELECT m.分摊实付分 FROM T09 t JOIN T09_金额分 m ON m.明细ID=t.明细ID WHERE t.楼栋维度键=? AND t.归属月份=?',(key,'202603')).fetchone()[0]
    result=r['data']['summary_rows'][0]
    assert cents(result['payable_yuan'])==payable and cents(result['paid_yuan'])==paid
    assert cents(result['difference_yuan'])==payable-paid
    assert 'receipts_yuan' not in result


def test_three_runs_same_result_and_sql(a):
    results=[ask(a,'新南公社14栋2026年3月应付多少') for _ in range(3)]
    assert all(r['answer']==results[0]['answer'] for r in results)
    assert all(r['data']['sql_records']==results[0]['data']['sql_records'] for r in results)


def test_no_record_and_social_reply_never_invent_amount(a):
    r=ask(a,'新南公社14栋2025年1月应付多少')
    assert r['data']['summary_rows'][0]['payable_yuan'] is None and '未知' in r['answer']
    r2=ask(a,'谢谢',r['session_id']);assert r2['status']=='acknowledged' and not r2.get('data')
    r3=ask(a,'今天很热',r['session_id']);assert r3['status']=='clarify_requirement' and not r3.get('data')
