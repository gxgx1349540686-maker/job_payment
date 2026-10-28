import contextlib,json,sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from agent.completeness import explain
from agent.engine import Agent
from agent.settings import Settings,ROOT
from agent.r35_database import compile_plan,R36Database

class Local:
    enabled=False

@pytest.fixture(scope='module')
def a():return Agent(Settings.load(),model=Local())

def status_rows(a):
    with contextlib.closing(a.db.connect()) as c:
        return [dict(r) for r in c.execute('SELECT 楼栋维度键 AS dimension_key,测试状态 AS test_status,完成状态 AS completion_status,标签 AS completeness_label,版本 AS registry_version,原因 AS completeness_reason,覆盖范围 AS coverage FROM QA_当前楼栋完成情况')]

def ask(a,r,kind='finance'):
    d=a.index.by_dimension[r['dimension_key']]
    result=a.chat({'message':'2026年1月费用' if kind=='finance' else '2026年1月执行合同','project':d['project'],'building':d['building'],'scope_type':'building'})
    if result['status']=='clarify_scope':result=a.chat({'session_id':result['session_id'],'selection':'b:'+r['dimension_key']})
    assert result['status']=='answered'
    return result

def test_complete_and_incomplete_both_visible(a):
    rows=status_rows(a)
    for category,complete in [('全部现有单据可测试',True),('可测试已对应部分',False),('暂不可测试',False)]:
        r=next(r for r in rows if r['test_status']==category and r['dimension_key'] in a.index.by_dimension)
        result=ask(a,r)
        q=result['data']['completeness']['rows'][0]
        assert q['information_complete'] is complete
        assert q['completeness_reason']==r['completeness_reason'] and q['coverage']==r['coverage']
        assert 'QA测试口径' in result['answer'] and '不等于严格逐月财务全部对应' in result['answer']
        assert r['completeness_reason'] in result['answer']
        assert result['data']['binding']['formal_version']=='OBS-V39'

def test_contracts_query_has_labels_without_money_queries(a):
    r=next(r for r in status_rows(a) if r['dimension_key'] in a.index.by_dimension)
    result=ask(a,r,'contracts')
    assert result['data']['completeness']['rows']
    titles=[r['title'] for r in result['data']['sql_records']]
    assert len(titles)==3 and not any('月度已确认金额' in t for t in titles)

def test_all_registered_dimensions_match_independent_database_rows(a):
    scope={'kind':'project','label':'全库登记','dimension_keys':list(a.index.by_dimension),'contract_id':None}
    plans=compile_plan(scope,None,None,a.index)
    qa=a.db.run(next(q for q in plans if q.name.startswith('楼栋信息完整性')))
    contracts=a.db.run(next(q for q in plans if q.name.startswith('合同在楼栋')))
    result=explain(scope,qa,contracts,a.index)
    gold={r['dimension_key']:r for r in status_rows(a)}
    assert len(qa)==len({r['dimension_key'] for r in qa})
    for r in result['rows']:
        g=gold.get(r['dimension_key'])
        assert r['information_complete']==(g['completion_status']=='已处理完成' and g['test_status']=='全部现有单据可测试' and not g['completeness_label']) if g else r['information_complete'] is None
    assert sum(r['completion_status']=='已处理完成' for r in gold.values())==656
    assert sum(bool(r['completeness_label']) for r in gold.values())==412

def test_contract_scope_filters_other_contracts(a):
    r=next(r for r in status_rows(a) if r['dimension_key'] in a.index.by_dimension)
    cid=next(cid for cid,c in a.index.contracts.items() if r['dimension_key'] in c['dimensions'])
    scope={'kind':'contract','label':cid,'contract_id':cid,'dimension_keys':list(a.index.contracts[cid]['dimensions'])}
    q=next(q for q in compile_plan(scope,None,None,a.index) if q.name.startswith('合同在楼栋'))
    rows=a.db.run(q)
    assert rows and all(r['contract_id']==cid for r in rows)
    result=explain(scope,a.db.run(next(q for q in compile_plan(scope,None,None,a.index) if q.name.startswith('楼栋信息完整性'))),rows,a.index)
    assert '不代表合同在其他楼栋范围全部完成' in result['explanation']

def test_missing_registry_never_means_complete():
    index=SimpleNamespace(by_dimension={'x':{'project':'p','building':'b'}})
    r=explain({'dimension_keys':['x']},[],[],index)
    assert r['rows'][0]['information_complete'] is None and r['counts']['unknown']==1

def test_blank_label_alone_not_complete_and_duplicate_stops():
    index=SimpleNamespace(by_dimension={'x':{'project':'p','building':'b'}})
    row={'dimension_key':'x','completion_status':'尚未完整','test_status':'暂不可测试','completeness_label':None}
    assert explain({'dimension_keys':['x']},[row],[],index)['rows'][0]['information_complete'] is False
    with pytest.raises(ValueError,match='重复维度'):explain({'dimension_keys':['x']},[row,row],[],index)

def test_qa_pointer_mismatch_blocked_even_when_hash_cached(a,tmp_path):
    settings=Settings.load()
    for name in ['数据表/当前版本.json','数据表/当前正式Excel版本.json','数据库结构/当前结构版本.json','数据表/当前测试状态登记版本.json']:
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((settings.project_root/name).read_bytes())
    db=R36Database(replace(settings,project_root=tmp_path));db.verify_binding()
    p=tmp_path/'数据表/当前测试状态登记版本.json';r=json.loads(p.read_text(encoding='utf-8-sig'));r['version']='OBS-V0';p.write_text(json.dumps(r),encoding='utf-8')
    with pytest.raises(ValueError,match='QA楼栋状态登记版本'):db.verify_binding()

def test_money_stays_separate_from_qa_join(a):
    scope={'kind':'project','label':'全库','dimension_keys':list(a.index.by_dimension),'contract_id':None}
    monthly=a.db.run(compile_plan(scope,None,None,a.index)[0])
    assert len(monthly)==9576 and len({r['record_id'] for r in monthly})==9576
    with contextlib.closing(a.db.connect()) as c:
        gold=c.execute('SELECT SUM(我方实收分),SUM(我方应付分) FROM T08_月度金额分').fetchone()
    assert sum(r['receipts_cents'] or 0 for r in monthly)==gold[0]
    assert sum(r['payable_cents'] or 0 for r in monthly)==gold[1]
