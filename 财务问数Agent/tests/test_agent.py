import contextlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.catalog import METRICS,canonical_sql
from agent.database import DecimalSum,validate_sql,sha256
from agent.matching import candidates
from agent.settings import Settings
from agent.workflow import Agent,parse_months


class LocalModel:
    enabled=False


@pytest.fixture(scope='module')
def agent():
    settings=Settings.load()
    if not settings.database.exists():pytest.skip('需要本机R33数据进行只读集成测试')
    return Agent(settings,model=LocalModel())


@pytest.mark.parametrize('sql',[
    'DELETE FROM q_data','SELECT * FROM q_data; DROP TABLE T01',
    'SELECT * FROM T01','SELECT * FROM main.q_data','PRAGMA database_list',
    'ATTACH DATABASE \'x\' AS x','SELECT load_extension(\'x\') FROM q_data',
    'WITH x AS (SELECT * FROM q_data) SELECT * FROM x',
    'SELECT SUM(amount_yuan) FROM q_data',
    canonical_sql('company_receivable')+' LIMIT 1',
    canonical_sql('company_receivable').replace('q_data','q_data JOIN q_data AS b ON 1=1'),
])
def test_unsafe_or_semantically_changed_sql_rejected(sql):
    with pytest.raises(ValueError):validate_sql(sql,'company_receivable')


@pytest.mark.parametrize('intent',list(METRICS))
def test_approved_sql_accepted(intent):
    assert validate_sql(canonical_sql(intent),intent)


def test_decimal_null_zero_negative_and_precision():
    with sqlite3.connect(':memory:') as c:
        c.create_aggregate('DECIMAL_SUM',1,DecimalSum)
        c.execute('CREATE TABLE v (n TEXT)')
        c.executemany('INSERT INTO v VALUES (?)',[(None,),('0',),('0.10',),('0.20',),('-0.05',)])
        assert c.execute('SELECT DECIMAL_SUM(n) FROM v').fetchone()[0]=='0.25'
        assert c.execute('SELECT DECIMAL_SUM(n) FROM v WHERE n IS NULL').fetchone()[0] is None
        assert c.execute('SELECT DECIMAL_SUM(n) FROM v WHERE n=\'0\'').fetchone()[0]=='0.00'
        c.execute("INSERT INTO v VALUES ('1.001')")
        with pytest.raises(sqlite3.OperationalError):c.execute('SELECT DECIMAL_SUM(n) FROM v').fetchone()


@pytest.mark.parametrize('question,expected',[
    ('多少应收',(None,None)),('2026年3月多少',('202603','202603')),
    ('2026年1月至4月',('202601','202604')),('2026年1至4月',('202601','202604')),
    ('2026年',('202601','202612')),('2026-01到2026-03',('202601','202603')),
    ('查202601的实付',('202601','202601')),('2025年12月至2026年2月',('202512','202602')),
])
def test_dates(question,expected):assert parse_months(question,2026)[:2]==expected


@pytest.mark.parametrize('question',['上月收入','2026年13月','2026年4月至1月','2026年1月和4月','按实际付款日期2026年1月','2026-01-01实付','2026年上半年应收'])
def test_unclear_or_invalid_dates_not_silently_all_dates(question):
    with pytest.raises(ValueError):parse_months(question,2026)


def test_ambiguity_then_building_confirmation_preserves_period(agent):
    a=agent.chat({'message':'2026年1月至4月应收多少','project':'石厦公社','building':'西村167号'})
    assert a['status']=='clarify_metric'
    b=agent.chat({'session_id':a['session_id'],'selection':'company_receivable'})
    assert b['status']=='clarify_building'
    assert b['choices']
    chosen=next(c for c in b['choices'] if '西村167' in c['label'])
    c=agent.chat({'session_id':a['session_id'],'selection':chosen['id']})
    assert c['status']=='answered'
    assert c['data']['period']['start']=='202601'
    assert c['data']['period']['end']=='202604'
    assert c['mode']=='local_templates'
    with contextlib.closing(agent.db.connect()) as db:
        expected=sum((Decimal(r[0]) for r in db.execute('SELECT company_fee_yuan FROM "T06-1" WHERE building_id=? AND expense_month_id BETWEEN ? AND ?', (chosen['id'],'202601','202604')) if r[0] is not None),Decimal(0))
    assert Decimal(c['data']['known_amount_total'])==expected


def test_selected_option_cannot_inject_building_id(agent):
    a=agent.chat({'message':'应收多少'})
    with pytest.raises(ValueError):agent.chat({'session_id':a['session_id'],'selection':"x' OR 1=1--"})


def test_short_text_clarification_preserves_original_period(agent):
    a=agent.chat({'message':'2026年2月应收多少','project':'石厦公社','building':'西村167号'})
    b=agent.chat({'session_id':a['session_id'],'message':'我方应收'})
    assert b['status']=='clarify_building'
    sid=a['session_id']
    assert agent.sessions[sid]['state']['start']=='202602'
    assert agent.sessions[sid]['state']['intent']=='company_receivable'


def test_no_balance_fabricated(agent):
    a=agent.chat({'message':'这个楼栋还有多少未收余额？'})
    assert a['status']=='unsupported'
    assert 'data' not in a


def test_typo_match_never_executes_before_selection(agent):
    a=agent.chat({'message':'这个楼栋有哪些合同','project':'石夏公社','building':'西村167'})
    assert a['status']=='clarify_building'
    assert any('石厦' in c['label'] for c in a['choices'])
    assert 'data' not in a


@pytest.mark.parametrize('intent',list(METRICS))
def test_all_metrics_execute_scoped_and_readonly(agent,intent):
    metric=METRICS[intent]
    with contextlib.closing(agent.db.connect()) as c:
        if intent=='actual_payment':
            building=c.execute('SELECT building_id FROM T01 WHERE contract_id IN (SELECT "对应新模式合同ID" FROM "T08-2") LIMIT 1').fetchone()[0]
        else:building=c.execute(f'SELECT "{metric.building_col}" FROM "{metric.table}" WHERE "{metric.building_col}" IS NOT NULL LIMIT 1').fetchone()[0]
    data=agent.db.query(intent,building)
    assert data['record_count']>0
    assert data['binding']['query_version']=='R33 / OBS-V18'


def test_business_connection_is_readonly(agent):
    with contextlib.closing(agent.db.connect()) as c:
        with pytest.raises(sqlite3.OperationalError):c.execute("UPDATE T03 SET building='should-not-write'")


def test_water_conflicts_have_real_source_rows(agent):
    building='39f765d8-2299-1222-1a20-0e83932eb192'
    data=agent.db.query('water',building,'202602','202603',fee_filter='水费')
    assert all(row['category']=='水费' for row in data['rows'])
    assert data['provenance']['issues']
    issue=data['provenance']['issues'][0]
    assert issue['实际来源位置']
    assert all(p['来源工作表'] and p['原工作表行号'] for p in issue['实际来源位置'])


def test_rules_mandatory_and_metric_specific(agent):
    rules=agent.rules.search('这个楼栋应收管理费多少','company_receivable')
    ids={r['id'] for r in rules}
    assert {'R001','R002','R003','R005','R007','R012'}<=ids
    assert (Path(__file__).resolve().parents[1]/'runtime/rules.sqlite').exists()


class FakeDeepSeek:
    enabled=True
    def __init__(self):self.calls=[]
    def json(self,system,data):
        self.calls.append(data)
        if 'approved_sql' in data:return {'sql':data['approved_sql']}
        return {'intent':'contracts','project':'石厦公社','building':'西村167号'}


def test_model_generation_path_with_injected_adapter(agent):
    fake=FakeDeepSeek(); a=Agent(agent.settings,model=fake)
    pending=a.chat({'message':'石厦公社西村167号有哪些合同？'})
    result=a.chat({'session_id':pending['session_id'],'selection':pending['choices'][0]['id']})
    assert result['status']=='answered' and result['mode']=='deepseek'
    assert len(fake.calls)==2
    assert 'rules' in fake.calls[1]
    assert 'rows' not in fake.calls[1]  # 明细和银行卡号均不作为模型输入


def test_adapter_disables_automatic_retries_without_real_key(monkeypatch):
    from types import SimpleNamespace
    import langchain_openai
    from agent.model import DeepSeek
    captured={}
    class MockChat:
        def __init__(self,**kwargs):captured.update(kwargs)
        def bind(self,**kwargs):return self
        def invoke(self,messages):return SimpleNamespace(content='{"ok":true}')
    monkeypatch.setattr(langchain_openai,'ChatOpenAI',MockChat)
    settings=SimpleNamespace(key=lambda:'dummy-test-value',base_url='https://example.invalid',model='mock-model')
    assert DeepSeek(settings).json('测试json',{})=={'ok':True}
    assert captured['max_retries']==0


def test_http_routes_and_cross_site_blocking(agent):
    import app
    app._agent=agent
    with TestClient(app.app) as client:
        assert client.get('/').status_code==200
        assert client.get('/assets/app.js').status_code==200
        assert client.get('/api/status').json()['ok']
        assert client.post('/api/chat',json={'message':'应收多少'}).json()['status']=='clarify_metric'
        assert client.post('/api/chat',json={'message':'应收多少'},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.get('/',headers={'Host':'evil.example'}).status_code==403


def test_version_pointers_unchanged_and_r33_hash_matches(agent):
    assert sha256(agent.settings.database)==json.loads(agent.settings.validation.read_text(encoding='utf8'))['database_sha256']
    p=json.loads((agent.settings.project_root/'数据表/当前版本.json').read_text(encoding='utf8'))
    assert p['version']=='OBS-V15' and p['workbook_version']=='OBS-R30'
