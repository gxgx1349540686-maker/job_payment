"""Separate R38 income/payable, T09 monthly allocation and both T10 reconciliation ends."""
from collections import defaultdict
from .r35_database import yuan, cents


def unique(rows, key):
    found = {}
    for row in rows:
        if row[key] in found:
            raise ValueError('财务查询出现重复唯一键：'+key+'，停止累计。')
        found[row[key]] = row
    return list(found.values())


def report(scope, monthly, executions, payments, index, settlements, writeoffs, balances,
           document=None, breakdown=False):
    monthly = unique(monthly, 'record_id')
    settlements = unique(settlements, 'detail_id')
    writeoffs = unique(writeoffs, 'writeoff_id')
    balances = unique(balances, 'document_id')
    unique(payments, 'document_id')
    contract = scope.get('contract_id') if scope['kind'] in ('contract','document') else None
    execution_map = defaultdict(set)
    for row in executions:
        execution_map[row['dimension_key'],row['month_id']].add(row['contract_id'])

    def aggregate(records, dimensions, month=None):
        dimensions = set(dimensions)
        months = {r['month_id'] for r in records}
        # For empty coverage, keep the requested month so cash facts aren't erased.
        if month: months.add(month)
        def in_month(row):
            return month is None or row['month_id']==(None if month=='__UNKNOWN_MONTH__' else month)
        selected = [r for r in settlements if r['dimension_key'] in dimensions and in_month(r)
                    and (not contract or r['contract_id']==contract)]
        applied = [r for r in writeoffs if r['dimension_key'] in dimensions and in_month(r)
                   and (not contract or r['contract_id']==contract)]
        docs = [r for r in balances if in_month(r) and (not contract or r['contract_id']==contract)]
        # Related contract money must not be assigned in full to each building.
        owned_docs, shared_docs = [], []
        for row in docs:
            owned = index.contracts.get(row['contract_id'],{}).get('dimensions',set())
            (owned_docs if owned and set(owned)<=dimensions else shared_docs).append(row)

        result = {'month_id':('月份待确认' if month=='__UNKNOWN_MONTH__' else month or '合计'),'record_count':len(records),'settlement_count':len(selected),
                  'writeoff_count':len(applied),'scope':scope['label'],
                  'clean_review_record_count':sum(r.get('clean_status')!='passed' for r in records),
                  'clean_review_payment_count':0,'unallocated_payment_count':len(shared_docs),
                  'unallocated_payment_yuan':yuan(sum(r['cash_cents'] for r in shared_docs)),
                  'unknown_payment_count':sum(r['paid_cents'] is None for r in selected),
                  'unknown_receipts_count':sum(r['receipts_cents'] is None for r in records),
                  'unknown_payable_count':sum(r['payable_cents'] is None for r in records),
                  'attribution':'R40月度金额；T09楼栋实付登记；T10按账单月有效核销'}
        for label, field in [('receipts','receipts_cents'),('payable','payable_cents'),
                             ('receivable','receivable_cents'),('adjustment','adjustment_cents'),
                             ('reserve','reserve_cents'),('water','water_cents'),('electricity','electricity_cents')]:
            known = [r[field] for r in records if r[field] is not None]
            result['known_'+label+'_yuan'] = yuan(sum(known)) if known else None
            result[label+'_yuan'] = yuan(sum(known)) if records and len(known)==len(records) else None
        attributable = not contract or all(execution_map[r['dimension_key'],r['month_id']]=={contract} for r in records)
        if contract and not attributable:
            for field in ['receipts','receivable','payable']:
                result['reference_'+field+'_yuan'] = result[field+'_yuan']
                result[field+'_yuan'] = None
            result['attribution'] += '；合同非唯一执行，实收/应付仅楼栋参考'
        paid_known = [r['paid_cents'] for r in selected if r['paid_cents'] is not None]
        result['known_paid_yuan'] = yuan(sum(paid_known)) if paid_known else None
        result['paid_yuan'] = yuan(sum(paid_known)) if selected and len(paid_known)==len(selected) else None
        result['settlement_review_count'] = sum(bool(r.get('special_review')) or '暂' in (r.get('allocation_status') or '') or r.get('clean_status','passed')!='passed' for r in selected)
        registered_months = {(s['dimension_key'],s['month_id']) for s in selected}
        result['missing_settlement_count'] = sum((r['dimension_key'],r['month_id']) not in registered_months for r in records)
        # Known T09 sum is partial when some building-months are absent; do not call it complete.
        if result['missing_settlement_count']:
            result['paid_yuan'] = None
        result['written_off_yuan'] = yuan(sum(r['effective_cents'] for r in applied))
        result['cash_yuan'] = yuan(sum(r['cash_cents'] for r in owned_docs))
        result['document_written_off_yuan'] = yuan(sum(r['written_off_cents'] for r in owned_docs))
        result['unmatched_documents_yuan'] = yuan(sum(r['unmatched_cents'] for r in owned_docs))
        result['unmatched_document_count'] = sum(r['unmatched_cents']!=0 for r in owned_docs)
        result['shared_unmatched_documents_yuan'] = yuan(sum(r['unmatched_cents'] for r in shared_docs))
        result['shared_unmatched_document_count'] = sum(r['unmatched_cents']!=0 for r in shared_docs)
        result['shared_cash_yuan'] = yuan(sum(r['cash_cents'] for r in shared_docs))
        result['difference_yuan'] = yuan(cents(result['payable_yuan'])-cents(result['paid_yuan'])) if result['payable_yuan'] is not None and result['paid_yuan'] is not None else None
        result['receipt_difference_yuan'] = yuan(cents(result['receipts_yuan'])-cents(result['payable_yuan'])) if result['receipts_yuan'] is not None and result['payable_yuan'] is not None else None
        if document:
            doc_amount = cents(document['amount_yuan']) if document['status']=='支付成功' and (month is None or in_month(document)) else None
            result['document_amount_yuan'] = yuan(doc_amount)
            result['payable_minus_document_yuan'] = yuan(cents(result['payable_yuan'])-doc_amount) if doc_amount is not None and result['payable_yuan'] is not None else None
        return result

    groups = defaultdict(list)
    for row in monthly:
        groups[row['month_id'] or '__UNKNOWN_MONTH__',row['dimension_key'] if breakdown else 'scope'].append(row)
    # Include explicitly recorded settlement months even if T08 has no corresponding row.
    for row in settlements:
        groups[row['month_id'] or '__UNKNOWN_MONTH__',row['dimension_key'] if breakdown else 'scope']
    # Writeoff-only/unknown bill months must also remain visible in targeted queries.
    for row in writeoffs:
        groups[row['month_id'] or '__UNKNOWN_MONTH__',row['dimension_key'] if breakdown else 'scope']
    # Document-only months still appear, without inventing T09 paid amounts.
    if not breakdown:
        for row in balances:
            groups[row['month_id'] or '__UNKNOWN_MONTH__','scope']
    rows=[]
    for (month,key),records in sorted(groups.items()):
        row=aggregate(records,[key] if breakdown else scope['dimension_keys'],month)
        if breakdown:
            d=index.by_dimension[key]
            row.update(project=d['project'],building=d['building'],dimension_key=key)
        rows.append(row)
    return rows,aggregate(monthly,scope['dimension_keys'])
