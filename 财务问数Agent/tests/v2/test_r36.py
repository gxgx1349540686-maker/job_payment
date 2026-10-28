import contextlib
from agent.engine import Agent
from agent.settings import Settings
from agent.r35_database import compile_plan

class Local:
    enabled=False

def test_clean_views_preserve_adopted_amounts_and_expose_status():
    a=Agent(Settings.load(),model=Local())
    scope={'kind':'project','label':'全量','dimension_keys':list(a.index.by_dimension),'contract_id':None}
    monthly=a.db.run(compile_plan(scope,None,None,a.index)[0])
    assert len(monthly)==9576
    assert sum(r['clean_status']!='passed' for r in monthly)>0
    assert sum(r['payable_cents'] or 0 for r in monthly)==12277890516
    assert all({'clean_status','clean_reason','clean_checked_at','clean_rule_version'}<=r.keys() for r in monthly)

def test_contract_names_are_current_t01_and_do_not_expand_rows():
    a=Agent(Settings.load(),model=Local())
    r=a.chat({'message':'石厦公社2026年1月有哪些执行合同'})
    with contextlib.closing(a.db.connect()) as c:
        for e in r['data']['executions']:
            names={row[0] for row in c.execute('SELECT contract_name FROM T01 WHERE contract_id=?',(e['contract_id'],))}
            assert e['contract_name'] in names
            assert 'contract_review_row_count' in e

def test_nonpassed_records_have_explicit_warning():
    a=Agent(Settings.load(),model=Local())
    row=next(d for d in a.index.dimensions if not d['building_id'])
    r=a.chat({'message':'费用','project':row['project'],'building':row['building'],'scope_type':'building'})
    assert '清洗' in r['answer'] and '未知' in r['answer']
    assert r['data']['summary_rows'][0]['clean_review_record_count']>0

def test_passed_subset_not_silently_ignored():
    a=Agent(Settings.load(),model=Local())
    r=a.chat({'message':'石厦公社仅清洗通过的应付多少钱'})
    assert r['status']=='unsupported' and 'data' not in r

def test_r36_binding_and_mandatory_clean_rule():
    a=Agent(Settings.load(),model=Local())
    assert a.binding['query_version']=='OBS-V31 / R38 / T09-T10 V8'
    assert any(r['id']=='CLEAN36' for r in a.rules.search('合同'))
