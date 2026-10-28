import contextlib
from dataclasses import replace
from decimal import Decimal
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from agent.engine import Agent,QueryCancelled,parse_intent
from agent.entities import select_candidates,name_score,project_aliases
from agent.periods import parse_months
from agent.r35_database import cents,yuan,compile_plan,validate_statement
from agent.reporting import financial_report
from agent.settings import Settings
from agent.streaming import EventStream


class LocalModel:
    enabled=False


@pytest.fixture(scope='module')
def agent():return Agent(Settings.load(),model=LocalModel())


@pytest.mark.parametrize('question,intent',[
    ('应收多少','clarify_metric'),('租客应收多少','clarify_metric'),('我方应收业主费用','finance'),
    ('这个项目有哪些执行合同','contracts'),('这个合同费用是多少','finance'),('收入应付实付差额','finance'),
    ('核销后余额','unsupported'),('每月财务总览','finance'),('我方收入有多少','finance')])
def test_intent(question,intent):assert parse_intent(question)[0]==intent


@pytest.mark.parametrize('q,period',[
    ('2026年1月至9月',('202601','202609')),('2026年1—9月',('202601','202609')),('3月',('202603','202603')),
    ('2026年',('202601','202612')),('2026-01到2026-03',('202601','202603')),('总数',(None,None))])
def test_period(q,period):assert parse_months(q,2026)[:2]==period


@pytest.mark.parametrize('q',['本月费用','2026年13月','2026年9月至1月','2026年1月和9月','2026-01-03','按付款日期2026年1月'])
def test_invalid_period(q):
    with pytest.raises(ValueError):parse_months(q,2026)


def test_candidate_threshold_strict_and_five_fallback():
    rows=[{'id':str(i),'label':str(i),'score':v} for i,v in enumerate([99,91,90,89,87,70])]
    assert [r['score'] for r in select_candidates(rows)]==[99,91]
    rows[0]['score']=88;rows[1]['score']=89
    result=select_candidates(rows)
    assert len(result)==5 and max(r['score'] for r in result)==90


def test_numeric_buildings_not_false_high():
    assert name_score('西村167号',['西村167栋'],True)==100
    assert name_score('西村167号',['西村67号'],True)<=60
    assert name_score('西村1号',['西村11号'],True)<=60


@pytest.mark.parametrize('name',['石厦公社','新南公社','上角环公社'])
def test_project_year_never_becomes_building_number(agent,name):
    found=agent.index.extract(name+'2026年1月费用')
    assert found['scope_type']=='project' and not found['building']


def test_explicit_project_auto_and_identifies_used_scope(agent):
    r=agent.chat({'message':'石厦公社2026年1月至4月收入应付实付差额'})
    assert r['status']=='answered' and r['scope']['kind']=='project'
    assert '石厦' in r['scope']['label'] and '查找对象' in r['answer']
    assert r['data']['period']['end']=='202604' and r['data']['summary_rows']


def test_explicit_building_auto_alias_and_exact_cents(agent):
    r=agent.chat({'message':'石厦公社西村167号2026年1月至4月我方应收费用'})
    assert r['status']=='answered' and r['scope']['kind']=='building'
    assert '西村167栋' in r['scope']['label']
    with contextlib.closing(agent.db.connect()) as c:
        expected=c.execute('SELECT sum(m."应收业主费用分") FROM T08 t JOIN "T08_月度金额分" m ON m."应付记录ID"=t."应付记录ID" WHERE t."楼栋维度键"=? AND t."归属月份" BETWEEN ? AND ?', (r['scope']['dimension_keys'][0],'202601','202604')).fetchone()[0]
    assert cents(r['data']['summary_rows'][0]['receivable_yuan'])==expected


def test_ambiguous_metric_only_then_no_redundant_entity_prompt(agent):
    r=agent.chat({'message':'石厦公社2026年2月应收多少'})
    assert r['status']=='clarify_metric'
    result=agent.chat({'session_id':r['session_id'],'selection':'metric:receivable'})
    assert result['status']=='answered' and result['data']['period']['start']=='202602'


def test_low_candidates_five_and_selection_preserves_period(agent):
    r=agent.chat({'message':'2026年3月费用','scope_type':'project','project':'石夏公设'})
    assert r['status']=='clarify_scope' and len(r['choices'])==5
    assert all(c['score']<=90 for c in r['choices'])
    result=agent.chat({'session_id':r['session_id'],'selection':r['choices'][0]['id']})
    assert result['status']=='answered' and result['data']['period']['start']=='202603'


def test_invalid_selection_cannot_change_scope(agent):
    r=agent.chat({'message':'应收多少'})
    with pytest.raises(ValueError):agent.chat({'session_id':r['session_id'],'selection':"x' OR 1=1--"})


def test_contract_identifier_auto_and_related_buildings(agent):
    cid=next(iter(agent.index.contracts))
    r=agent.chat({'message':cid+'2026年1月收入应付实付差额'})
    assert r['status']=='answered' and r['scope']['contract_id']==cid
    assert r['data']['related_buildings']


def test_document_exact_id_not_confused_by_near_serials(agent):
    doc=next(d for d in agent.index.documents.values() if d['status']=='支付成功' and agent.index.contracts[d['contract_id']]['dimensions'])
    candidates=agent.index.candidates('document',entity=doc['document_id'])
    assert len(candidates)==1 and candidates[0]['score']==100
    r=agent.chat({'message':doc['document_id']+'的实付和应付差额'})
    assert r['status']=='answered' and r['data']['selected_document']['document_id']==doc['document_id']
    assert r['data']['period']['start']==doc['month_id']
    assert all(p['status']=='支付成功' for p in r['data']['documents'])


def test_failed_document_not_counted_as_paid(agent):
    doc=next(d for d in agent.index.documents.values() if d['status']!='支付成功' and agent.index.contracts.get(d['contract_id'],{}).get('dimensions'))
    r=agent.chat({'message':doc['document_id']+'的已付金额与应付差额'})
    assert r['status']=='answered'
    assert r['data']['selected_document']['status']!='支付成功'
    assert r['data']['summary_rows'][0]['document_amount_yuan'] is None


def test_only_total_no_misleading_empty_months(agent):
    r=agent.chat({'message':'石厦公社只查合计应付费用'})
    assert r['status']=='answered' and r['data']['rows']==[] and r['data']['summary_rows']


def test_monthly_contract_execution_does_not_expand_finance(agent):
    r=agent.chat({'message':'石厦公社2026年1月有哪些执行合同'})
    assert r['status']=='answered' and r['data']['intent']=='contracts'
    assert all(e['month_id']=='202601' for e in r['data']['executions'])
    assert len(r['data']['sql_records'])==1


def test_missing_building_id_preserves_unknown(agent):
    r=agent.chat({'message':'2026年1月费用','project':'坪山上坝公社','building':'50号','scope_type':'building'})
    if r['status']=='clarify_scope':r=agent.chat({'session_id':r['session_id'],'selection':next(c['id'] for c in r['choices'] if ' / 50' in c['label'])})
    s=r['data']['summary_rows'][0]
    assert s['payable_yuan'] is None and s['difference_yuan'] is None and s['unknown_payable_count']==1


def test_no_records_outside_source_coverage_not_zero(agent):
    r=agent.chat({'message':'石厦公社2027年1月费用'})
    s=r['data']['summary_rows'][0]
    assert s['payable_yuan'] is None and s['paid_yuan'] is None and not s['record_count']


def test_all_r35_amounts_reconcile_to_formal_validation(agent):
    scope={'kind':'project','label':'验收全范围','dimension_keys':list(agent.index.by_dimension),'contract_id':None}
    statements=compile_plan(scope,None,None,agent.index)
    rows=agent.db.run(statements[0]);assert len(rows)==9576
    assert sum(r['payable_cents'] is None for r in rows)==9
    assert sum(r['payable_cents'] or 0 for r in rows)==12277890516
    assert sum(r['receipts_cents'] or 0 for r in rows)==10456969839
    assert sum(r['receipts_cents'] is None for r in rows)==1072
    assert sum(r['receivable_cents'] for r in rows)==1278826058
    assert any(r['payable_cents']<0 for r in rows if r['payable_cents'] is not None)
    for r in rows:
        if r['payable_cents'] is not None:assert r['payable_cents']==r['rent_cents']+r['compensation_cents']-r['reserve_cents']+r['adjustment_cents']


@pytest.mark.parametrize('value',[None,'','0','-1.02','123456789.99'])
def test_money_precision(value):
    n=cents(value)
    assert n is None if value is None or value=='' else Decimal(yuan(n))==Decimal(value)


@pytest.mark.parametrize('value',['NaN','Infinity','0.001','not-money'])
def test_invalid_money(value):
    with pytest.raises((ValueError,ArithmeticError)):cents(value)


@pytest.mark.parametrize('sql',['DELETE FROM T08','SELECT 1; DROP TABLE T08','SELECT * FROM T01','SELECT load_extension(\'x\')','ATTACH DATABASE \'x\' AS x','SELECT * FROM sqlite_master'])
def test_sql_tampering_blocked(sql):
    with pytest.raises(ValueError):validate_statement(sql,'SELECT "归属月份" FROM T08 WHERE "楼栋维度键"=?')


def test_sql_values_are_bound_not_interpolated(agent):
    scope={'kind':'building','dimension_keys':["x' OR 1=1--"],'contract_id':None}
    statement=compile_plan(scope,None,None,agent.index)[0]
    assert "x' OR 1=1--" not in statement.sql and "x' OR 1=1--" in statement.params
    assert agent.db.run(statement)==[]


def test_business_database_readonly(agent):
    with contextlib.closing(agent.db.connect()) as c:
        with pytest.raises(sqlite3.OperationalError):c.execute('DELETE FROM T08')


def test_query_progress_contains_analysis_and_sql_before_execution(agent):
    events=[]
    r=agent.chat({'message':'石厦公社2026年1月实收应付差额'},on_event=lambda e,d:events.append((e,d)))
    names=[e for e,d in events]
    assert r['status']=='answered'
    assert 'analysis' in names and names.index('sql_delta')<names.index('sql_executed')
    assert any(e=='analysis' and 'T08' in d['text'] for e,d in events)
    assert r['data']['sql_records'][0]['params']


def test_cancel_before_sql_execution_does_not_run_query(agent,monkeypatch):
    event=threading.Event();calls=[]
    monkeypatch.setattr(agent.db,'run',lambda *args,**kwargs:calls.append(1))
    def emit(name,data):
        if name=='sql_ready':event.set()
    with pytest.raises(QueryCancelled):agent.chat({'message':'石厦公社收入'},on_event=emit,cancelled=event)
    assert not calls


def test_model_only_fallback_not_allowed_to_invent_auto_scope(agent):
    class Fake:
        enabled=True
        def __init__(self):self.calls=0
        def parse(self,question,prompt):self.calls+=1;return {'scope_type':'project','project':'石厦公社'}
    fake=Fake();a=Agent(agent.settings,model=fake)
    result=a.chat({'message':'帮我看那边费用'})
    assert fake.calls==1 and result['status']=='clarify_scope'
    assert len(result['choices'])==5 and all(c['score']<=90 for c in result['choices'])


def test_explicit_local_question_does_not_need_model_call(agent):
    class Fake:
        enabled=True
        def parse(self,*args):raise AssertionError('明确问题不必调用模型')
    a=Agent(agent.settings,model=Fake())
    assert a.chat({'message':'石厦公社2026年1月收入'})['status']=='answered'


def test_r35_rules_and_prompts_loaded(agent):
    assert len(agent.rules.documents)==11
    text=' '.join(r['text'] for r in agent.rules.search('实收应付'))
    assert 'T05-2仅差异核对' in text and '核销' in text
    assert 'json' in agent.prompts['parse']


def test_status_stream_and_cross_site_protection(agent,monkeypatch):
    import app
    monkeypatch.setattr(app,'_agent',agent)
    with TestClient(app.app) as client:
        assert client.get('/api/status').json()['binding']['query_version']=='OBS-V31 / R38 / T09-T10 V8'
        assert client.get('/assets/v2.js').status_code==200
        r=client.post('/api/chat/stream',json={'message':'石厦公社2026年1月收入'})
        assert r.headers['content-type'].startswith('text/event-stream')
        for event in ['analysis','sql_start','sql_delta','sql_ready','sql_executed','answer_delta','result','done']:assert 'event: '+event+'\n' in r.text
        assert '数据库' in r.text and 'OBS-V31' in r.text
        assert client.post('/api/chat/stream',json={'message':'收入'},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/chat',json={'scope_type':'unsafe'}).status_code==422


def test_binding_formal_r35_unchanged(agent):
    assert agent.db.verify_binding()['formal_workbook']=='OBS-R36'


def test_undefined_current_month_without_document_clarifies(agent):
    with pytest.raises(ValueError,match='当月'):agent.chat({'message':'石厦公社当月费用'})


def test_specific_receipt_type_not_replaced_with_total_or_deduction(agent):
    result=agent.chat({'message':'石厦公社水费实收多少'})
    assert result['status']=='unsupported' and 'data' not in result


def test_conflicting_form_project_and_question_not_silently_adopted(agent):
    with pytest.raises(ValueError,match='项目不同'):
        agent.chat({'message':'石厦公社收入','project':'新南公社'})


def test_conflicting_form_building_and_question_not_silently_adopted(agent):
    with pytest.raises(ValueError,match='楼栋不同'):
        agent.chat({'message':'石厦公社西村167号收入','project':'石厦公社','building':'西村70号'})
