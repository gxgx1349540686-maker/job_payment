from types import SimpleNamespace
import contextlib
import pytest
from agent.finance_v38 import report
from agent.engine import Agent
from agent.settings import Settings
from agent.r35_database import compile_plan,cents


def fixture():
    scope={'kind':'building','label':'样本A','dimension_keys':['a'],'contract_id':None}
    index=SimpleNamespace(contracts={'c':{'dimensions':{'a'}}},by_dimension={'a':{'project':'项目','building':'A'}})
    monthly=[dict(record_id='m',dimension_key='a',month_id='202602',receipts_cents=10000,
                  payable_cents=9000,receivable_cents=1000,adjustment_cents=0,reserve_cents=1000,
                  water_cents=0,electricity_cents=0,clean_status='passed')]
    settlements=[dict(detail_id='b',dimension_key='a',month_id='202602',contract_id='c',paid_cents=8000)]
    writeoffs=[dict(writeoff_id='w',dimension_key='a',month_id='202602',contract_id='c',effective_cents=4000)]
    balances=[dict(document_id='d',month_id='202602',contract_id='c',cash_cents=10000,written_off_cents=4000,unmatched_cents=6000)]
    return scope,index,monthly,settlements,writeoffs,balances


def run(f):
    scope,index,monthly,settlements,writeoffs,balances=f
    return report(scope,monthly,[],[],index,settlements,writeoffs,balances)


def test_paid_uses_monthly_register_not_bank_document_or_writeoff():
    rows,total=run(fixture())
    assert total['paid_yuan']=='80.00' and total['cash_yuan']=='100.00'
    assert total['written_off_yuan']=='40.00' and total['unmatched_documents_yuan']=='60.00'
    assert total['difference_yuan']=='10.00'


def test_unknown_income_does_not_block_known_payable_or_become_zero():
    f=fixture();f[2][0]['receipts_cents']=None
    rows,total=run(f)
    assert total['receipts_yuan'] is None and total['unknown_receipts_count']==1
    assert total['payable_yuan']=='90.00'
    assert total['receipt_difference_yuan'] is None


def test_missing_monthly_register_is_not_assumed_zero():
    f=fixture();f[3].clear()
    rows,total=run(f)
    assert total['paid_yuan'] is None and total['missing_settlement_count']==1
    assert total['difference_yuan'] is None


def test_shared_document_balance_not_repeated_as_building_owned():
    f=fixture();f[1].contracts['c']['dimensions']={'a','b'}
    rows,total=run(f)
    assert total['unmatched_documents_yuan']=='0.00'
    assert total['shared_unmatched_documents_yuan']=='60.00'
    f[0]['dimension_keys']=['a','b'];f[0]['kind']='project'
    rows,total=run(f)
    assert total['unmatched_documents_yuan']=='60.00'
    assert total['shared_unmatched_documents_yuan']=='0.00'


def test_bill_month_and_document_month_are_distinct():
    f=fixture();f[5][0]['month_id']='202601'
    rows,total=run(f)
    assert total['written_off_yuan']=='40.00'
    feb=next(r for r in rows if r['month_id']=='202602')
    assert feb['document_written_off_yuan']=='0.00'
    assert feb['unmatched_documents_yuan']=='0.00'
    assert total['document_written_off_yuan']=='40.00'
    assert total['unmatched_documents_yuan']=='60.00'


def test_negative_writeoff_and_explicit_zero_preserved():
    f=fixture();f[4][0]['effective_cents']=-500
    f[4].append(dict(writeoff_id='candidate',dimension_key='a',month_id='202602',contract_id='c',effective_cents=0))
    assert run(f)[1]['written_off_yuan']=='-5.00'


def test_duplicate_writeoff_id_stops_instead_of_summing_twice():
    f=fixture();f[4].append(dict(f[4][0]))
    with pytest.raises(ValueError,match='重复唯一键'):run(f)


def test_partial_month_totals_show_known_subtotal():
    f=fixture();f[2].append(dict(f[2][0],record_id='m2',month_id='202603',receipts_cents=None))
    f[3].append(dict(f[3][0],detail_id='b2',month_id='202603',paid_cents=None))
    rows,total=run(f)
    assert total['receipts_yuan'] is None and total['known_receipts_yuan']=='100.00'
    assert total['paid_yuan'] is None and total['known_paid_yuan']=='80.00'


class Local:
    enabled=False


def test_latest_database_formula_and_both_writeoff_ends_independent_gold():
    a=Agent(Settings.load(),model=Local())
    scope={'kind':'project','label':'全范围核验','dimension_keys':list(a.index.by_dimension),'contract_id':None}
    queries=compile_plan(scope,None,None,a.index)
    raw={q.name:a.db.run(q) for q in queries}
    monthly=raw['楼栋月度已确认金额']
    assert len(monthly)==9576
    assert sum(r['receipts_cents'] is None for r in monthly)==1072
    assert sum(r['receipts_cents'] or 0 for r in monthly)==10456969839
    assert sum(r['payable_cents'] or 0 for r in monthly)==12277890516
    for r in monthly:
        if r['receipts_cents'] is not None:
            assert r['receipts_cents']==r['rent_cents']+r['compensation_cents']+r['previous_water_cents']+r['previous_electricity_cents']
        if r['payable_cents'] is not None:
            assert r['payable_cents']==r['rent_cents']+r['compensation_cents']-r['reserve_cents']+r['adjustment_cents']
    with contextlib.closing(a.db.connect()) as c:
        cash,applied,balance,detail=c.execute('SELECT * FROM T10_核销控制').fetchone()
    assert (cash,applied,balance,detail)==(11256567073,10192305709,1064261364,10192305709)
    assert sum(r['effective_cents'] for r in raw['T10有效核销（账单月份）'])+sum(r['effective_cents'] for r in raw['合同级核销汇总（未分栋，不计月账实付）'])==applied
    assert sum(r['unmatched_cents'] for r in raw['核销单据余额（单据登记月份，唯一单据）'])==balance
    assert sum(r['paid_cents'] or 0 for r in raw['T09楼栋月度实付汇总'])==10938463750


def test_monthly_answer_and_visible_unmatched_notice_for_reverted_documents():
    a=Agent(Settings.load(),model=Local())
    d=next(d for d in a.index.dimensions if d['project']=='泊寓-大浪水围时尚社区' and d['building']=='48栋')
    result=a.chat({'message':'2026年1月费用','scope_type':'building','project':d['project'],'building':d['building']})
    if result['status']=='clarify_scope':
        choice=next(c for c in result['choices'] if c['id']=='b:'+d['dimension_key'])
        result=a.chat({'session_id':result['session_id'],'selection':choice['id']})
    assert result['status']=='answered' and 'T10有效核销' in result['answer']
    summary=result['data']['summary_rows'][0]
    assert summary['unmatched_document_count'] or summary['shared_unmatched_document_count']
    assert '余额' in result['answer'] and '重复计入' in result['answer']


def test_unknown_month_register_is_separate_and_included_once_in_total():
    f=fixture();f[3].append(dict(f[3][0],detail_id='unknown',month_id=None,paid_cents=300))
    rows,total=run(f)
    unknown=next(r for r in rows if r['month_id']=='月份待确认')
    assert unknown['paid_yuan']=='3.00' and unknown['payable_yuan'] is None
    assert total['paid_yuan']=='83.00'


def test_v8_three_confirmed_documents_fully_written_off_and_bill_gap_preserved():
    a=Agent(Settings.load(),model=Local())
    with contextlib.closing(a.db.connect()) as c:
        docs=c.execute('SELECT 单据号,未核销余额分 FROM T10_单据核销余额 WHERE 单据号 IN (?,?,?)',('NPSZFS202601140059','NPSZFS202602250009','NPSZFS202603040002')).fetchall()
        assert len(docs)==3 and all(r[1]==0 for r in docs)
        assert c.execute('SELECT COUNT(*) FROM T09 WHERE 楼栋维度键 IS NULL').fetchone()[0]==29
    scope={'kind':'project','label':'全范围','dimension_keys':list(a.index.by_dimension),'contract_id':None}
    queries=compile_plan(scope,None,None,a.index)
    monthly=a.db.run(queries[4]); extra=a.db.run(queries[-1])
    assert all(r['dimension_key'] is not None for r in monthly)
    assert extra and len({r['writeoff_id'] for r in extra})==len(extra)
