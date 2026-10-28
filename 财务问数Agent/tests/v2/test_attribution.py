from types import SimpleNamespace
from agent.reporting import financial_report


def row(key='a',month='202601',payable=8000):
    return {'dimension_key':key,'month_id':month,'receipts_cents':10000,'receivable_cents':2000,'payable_cents':payable,'adjustment_cents':0,'reserve_cents':0,'water_cents':0,'electricity_cents':0}


def payment(ident,cid='c',amount='20',month='202601'):
    return {'document_id':ident,'contract_id':cid,'amount_yuan':amount,'month_id':month,'status':'支付成功'}


def test_single_document_is_not_whole_month_payment():
    index=SimpleNamespace(contracts={'c':{'dimensions':{'a'}}})
    scope={'kind':'document','contract_id':'c','dimension_keys':['a'],'label':'单据1'}
    p=[payment('one'),payment('two',amount='30')]
    rows,total=financial_report(scope,[row()],[{'dimension_key':'a','month_id':'202601','contract_id':'c'}],p,index,p[0])
    assert total['paid_yuan']=='50.00' and total['document_amount_yuan']=='20.00'
    assert total['difference_yuan']=='30.00' and total['payable_minus_document_yuan']=='60.00'


def test_contract_multiple_candidates_yields_reference_not_contract_amount():
    index=SimpleNamespace(contracts={'c':{'dimensions':{'a'}}})
    scope={'kind':'contract','contract_id':'c','dimension_keys':['a'],'label':'合同c'}
    executions=[{'dimension_key':'a','month_id':'202601','contract_id':c} for c in ['c','other']]
    _,total=financial_report(scope,[row()],executions,[payment('p')],index)
    assert total['reference_payable_yuan']=='80.00' and total['payable_yuan'] is None and total['difference_yuan'] is None


def test_multibuilding_payment_not_allocated_to_one_building():
    index=SimpleNamespace(contracts={'c':{'dimensions':{'a','b'}}})
    scope={'kind':'building','dimension_keys':['a'],'label':'楼栋a'}
    _,total=financial_report(scope,[row()],[],[payment('p')],index)
    assert total['paid_yuan']=='0.00' and total['unallocated_payment_count']==1 and total['difference_yuan'] is None


def test_project_includes_whole_contract_and_deduplicates_payment():
    index=SimpleNamespace(contracts={'c':{'dimensions':{'a','b'}}})
    scope={'kind':'project','dimension_keys':['a','b'],'label':'项目'}
    _,total=financial_report(scope,[row('a'),row('b')],[],[payment('p')],index)
    assert total['payable_yuan']=='160.00' and total['paid_yuan']=='20.00' and total['difference_yuan']=='140.00'


def test_unknown_payable_does_not_become_zero():
    index=SimpleNamespace(contracts={})
    scope={'kind':'project','dimension_keys':['a'],'label':'项目'}
    _,total=financial_report(scope,[row(payable=None)],[],[],index)
    assert total['payable_yuan'] is None and total['difference_yuan'] is None
