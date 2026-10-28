from collections import defaultdict
from .r35_database import cents,yuan

MONEY_FIELDS={'receipts':'receipts_cents','receivable':'receivable_cents','payable':'payable_cents','adjustment':'adjustment_cents','reserve':'reserve_cents','water':'water_cents','electricity':'electricity_cents'}


def financial_report(scope,monthly,executions,payments,index,document=None,breakdown=False,settlements=None,writeoffs=None,balances=None):
    if settlements is not None:
        from .finance_v38 import report
        return report(scope,monthly,executions,payments,index,settlements,writeoffs or [],balances or [],document,breakdown)
    execution_map=defaultdict(set)
    for row in executions:execution_map[row['dimension_key'],row['month_id']].add(row['contract_id'])
    unique_docs={}
    for row in payments:
        if row['document_id'] in unique_docs:raise ValueError('成功付款单据号重复，停止汇总。')
        if row['status']=='支付成功':unique_docs[row['document_id']]=row
    all_payments=list(unique_docs.values())

    def aggregate(records,dimensions,month=None):
        result={'month_id':month or '合计','record_count':len(records),'clean_review_record_count':sum(r.get('clean_status','pending')!='passed' for r in records),'clean_policy':'正式已采用全范围；清洗非通过记录保留并提示'}
        if not records:
            result.update({field+'_yuan':None for field in MONEY_FIELDS})
            result.update({'paid_yuan':None,'difference_yuan':None,'receipt_difference_yuan':None,'unknown_payable_count':0,'unallocated_payment_count':0,'attribution':'没有月度记录，不能认定为0元'})
            return result
        for label,col in MONEY_FIELDS.items():
            known=[r[col] for r in records if r[col] is not None]
            result[label+'_yuan']=yuan(sum(known)) if known else None
        unknown=sum(r['payable_cents'] is None for r in records)
        attributable=True
        if scope['kind'] in ('contract','document'):
            cid=scope['contract_id']
            attributable=all(execution_map[r['dimension_key'],r['month_id']]=={cid} for r in records)
            for field in ('receipts','receivable','payable'):
                result['reference_'+field+'_yuan']=result[field+'_yuan']
                if not attributable:result[field+'_yuan']=None
        months={r['month_id'] for r in records}
        relevant=[p for p in all_payments if p['month_id'] in months]
        assigned=[];unallocated=[]
        for payment in relevant:
            if scope['kind'] in ('contract','document'):
                owned=index.contracts.get(payment['contract_id'],{}).get('dimensions',set())
                (assigned if payment['contract_id']==scope['contract_id'] and owned and owned<=set(dimensions) else unallocated).append(payment)
            else:
                owned=index.contracts.get(payment['contract_id'],{}).get('dimensions',set())
                (assigned if owned and owned<=set(dimensions) else unallocated).append(payment)
        amounts=[cents(p['amount_yuan']) for p in assigned]
        missing_payment=sum(v is None for v in amounts)
        paid=sum(v for v in amounts if v is not None)
        result.update({'paid_yuan':yuan(paid),'payment_count':len(assigned),'clean_review_payment_count':sum(p.get('clean_status','pending')!='passed' for p in assigned),'unknown_payment_count':missing_payment,
                       'unknown_payable_count':unknown,'unallocated_payment_count':len(unallocated),
                       'unallocated_payment_yuan':yuan(sum(cents(p['amount_yuan']) or 0 for p in unallocated)),
                       'attribution':'当月唯一执行合同归属' if scope['kind'] in ('contract','document') and attributable else '相关楼栋参考，非合同专属金额' if not attributable else '项目/楼栋月度口径'})
        can_compare=not unknown and attributable and not unallocated and not missing_payment
        result['difference_yuan']=yuan(cents(result['payable_yuan'])-paid) if can_compare else None
        result['receipt_difference_yuan']=yuan(cents(result['receipts_yuan'])-cents(result['payable_yuan'])) if not unknown and attributable else None
        if document:
            applies=document['month_id'] in months
            doc_amount=cents(document['amount_yuan']) if applies and document['status']=='支付成功' else None
            result['document_amount_yuan']=yuan(doc_amount)
            result['payable_minus_document_yuan']=yuan(cents(result['payable_yuan'])-doc_amount) if doc_amount is not None and attributable and not unknown else None
        return result

    groups=defaultdict(list)
    for row in monthly:groups[row['month_id'],row['dimension_key'] if breakdown else 'scope'].append(row)
    rows=[]
    for (month,key),records in sorted(groups.items()):
        dimensions=[key] if breakdown else scope['dimension_keys']
        r=aggregate(records,dimensions,month)
        if breakdown:
            d=index.by_dimension[key];r.update({'project':d['project'],'building':d['building'],'dimension_key':key})
        else:r['scope']=scope['label']
        rows.append(r)
    summary=aggregate(monthly,scope['dimension_keys']);summary['scope']=scope['label']
    return rows,summary
