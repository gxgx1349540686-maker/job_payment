"""Target-only SELECTs compiled from reviewed templates, never model SQL."""
import sqlglot
from .r35_database import compile_plan,Statement

MONTHLY='楼栋月度已确认金额'
EXEC='执行合同（独立查询，不展开金额）'
SETTLEMENT='T09楼栋月度实付汇总'
WRITEOFF='T10有效核销（账单月份）'
BALANCE='核销单据余额（单据登记月份，唯一单据）'
EXTRA='合同级核销汇总（未分栋，不计月账实付）'
QA='楼栋信息完整性（当前QA登记，非逐月结清）'
QA_CONTRACT='合同在楼栋范围内的QA状态'

FIELDS={'receipts':'receipts_cents','receivable':'receivable_cents','payable':'payable_cents',
        'adjustment':'adjustment_cents','reserve':'reserve_cents','water':'water_cents','electricity':'electricity_cents'}

def plan(scope,slots,output,index):
    all_queries=compile_plan(scope,slots.get('start'),slots.get('end'),index)
    metrics=set(slots.get('metrics',[]));intent=slots.get('intent','finance')
    needed={QA}
    if scope.get('contract_id'):needed.add(QA_CONTRACT)
    if intent=='contracts':needed.add(EXEC)
    elif intent!='completeness':
        needed.add(MONTHLY)
        if scope.get('contract_id'):needed.add(EXEC)
        if metrics&{'paid','difference'}:needed.add(SETTLEMENT)
        if 'written_off' in metrics:needed.update({WRITEOFF,EXTRA})
        if 'unmatched_documents' in metrics:needed.add(BALANCE)
        if output.get('details') and metrics&set(FIELDS):needed.add('采用来源预览')
    chosen=[q for q in all_queries if q.name in needed]
    # Financial reporting retains nullable fields internally, without fetching unrelated columns.
    wanted={'record_id','dimension_key','building_id','project','building','month_id','clean_status','clean_reason',
            'clean_checked_at','clean_rule_version','calculation_status','data_note'}
    for key in metrics:
        if key in FIELDS:wanted.add(FIELDS[key])
    if 'difference' in metrics:wanted.add('payable_cents')
    if 'receipt_difference' in metrics:wanted.update({'receipts_cents','payable_cents'})
    for i,q in enumerate(chosen):
        if q.name==MONTHLY:
            tree=sqlglot.parse_one(q.sql,read='sqlite')
            tree.set('expressions',[e for e in tree.expressions if e.alias_or_name in wanted])
            chosen[i]=Statement(q.name,tree.sql(dialect='sqlite'),q.params)
    return chosen
