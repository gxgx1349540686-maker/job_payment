"""Execute a confirmed, structured request and return only requested business results."""
import time
from .query_plan import plan,MONTHLY,EXEC,SETTLEMENT,WRITEOFF,BALANCE,EXTRA,QA,QA_CONTRACT,FIELDS
from .reporting import financial_report
from .r35_database import yuan,Statement,cents
from .completeness import explain

LABELS={'receipts':'我方实收','receivable':'我方应收业主费用','payable':'我方应付',
        'paid':'T09实付登记','written_off':'有效核销（账单月）','unmatched_documents':'单据未核销余额（登记月）',
        'difference':'应付－实付登记','receipt_difference':'实收－应付','reserve':'水电预留',
        'adjustment':'已采用调整','water':'水费划扣（历史对照）','electricity':'电费划扣（历史对照）'}

def run(agent,state,emit,cancelled):
    started=time.monotonic();slots=state['slots'];scope=slots['scope'];metrics=slots['metrics'];output=state['output_spec']
    confirmed=state.get('confirmed_items',{})
    if state.get('status')!='ready' or not confirmed.get('metrics',{}).get('confirmed') or confirmed['metrics'].get('value')!=metrics or confirmed.get('scope',{}).get('value',{}).get('id')!=scope['id'] or confirmed.get('output_spec',{}).get('value')!=output:
        raise ValueError('需求尚未确认或确认后已变化，本轮不执行查询。')
    def event(name,data):
        if cancelled and cancelled.is_set():raise ValueError('本轮已取消，未自动重试。')
        emit(name,data)
    raw={};records=[]
    rules=agent.rules.search(state['question']+' '+' '.join(metrics),limit=6)
    event('analysis',{'title':'采用需求','text':describe(state)})
    document=agent.index.documents.get(scope.get('document_id'))
    # A single document amount is a bank fact, not the whole contract's monthly allocation.
    doc_only=document and set(metrics)=={'paid'} and slots['intent']=='finance'
    document_comparison=document and 'difference' in metrics and not any(w in state['question'] for w in ('整月','同月实付','月度实付','T09'))
    query_slots=dict(slots,metrics=['payable']) if document_comparison else slots
    queries=plan(scope,dict(slots,intent='completeness') if doc_only else query_slots,output,agent.index) if scope['dimension_keys'] else []
    if document and document['status']=='支付成功':
        queries.insert(0,Statement('指定成功实付单据（唯一）','SELECT "单据号" AS document_id,"归属月份" AS month_id,"金额分" AS amount_cents FROM "T09_核销单据" WHERE "单据号"=? AND "CSV一致"=1',[document['document_id']]))
    for i,q in enumerate(queries):
        qid='q'+str(i+1);event('sql_start',{'query_id':qid,'title':q.name,'text':'只执行当前目标所需的参数化只读查询。'})
        for n in range(0,len(q.sql),160):event('sql_delta',{'query_id':qid,'text':q.sql[n:n+160]})
        event('sql_ready',{'query_id':qid,'params_preview':q.params[:10],'parameter_count':len(q.params),'validation':'执行前校验语法树、允许表与只读权限'})
        raw[q.name]=agent.db.run(q,cancelled)
        records.append({'title':q.name,'sql':q.sql,'params':q.params,'rows':len(raw[q.name])})
        event('sql_executed',{'query_id':qid,'row_count':len(raw[q.name]),'text':'已校验并只读执行'})
    if document and document['status']=='支付成功':
        facts=raw['指定成功实付单据（唯一）']
        if len(facts)!=1 or facts[0]['amount_yuan']!=document['amount_yuan']:raise ValueError('单据事实与已验收索引不一致，停止回答。')
    completeness=explain(scope,raw.get(QA,[]),raw.get(QA_CONTRACT,[]),agent.index)
    executions=raw.get(EXEC,[])
    if scope.get('contract_id'):executions=[r for r in executions if r['contract_id']==scope['contract_id']]
    for r in executions:
        d=agent.index.by_dimension[r['dimension_key']];r.update(project=d['project'],building=d['building'])
    period=f"{slots.get('start') or '最早'}—{slots.get('end') or '最晚'}" if slots.get('start') or slots.get('end') else '全部已存日期'
    lines=[f"查找对象：{scope['label']}",f"账期：{period}；数据：{agent.binding['query_version']}。"]
    rows=[];summary=None;intent=slots['intent'];warnings=[]
    if intent=='contracts':
        unique={}
        for r in executions:
            k=(r['dimension_key'],r['contract_id']);item=unique.setdefault(k,dict(r));item['months']=sorted(set(item.get('months',[])+[r['month_id']]))
        rows=list(unique.values());lines.append(f"查到{len(set(r['contract_id'] for r in rows))}份执行合同，覆盖{len(rows)}个楼栋合同关系；详情见合同表。")
        warnings.append('合同展示采用已存执行月份，不代表完整历史合同或法律效力判断。')
    elif intent=='completeness':lines.append(completeness['explanation'])
    elif doc_only:
        in_period=(not slots.get('start') or document['month_id']>=slots['start']) and (not slots.get('end') or document['month_id']<=slots['end'])
        lines.append(f"单据{document['document_id']}：{document['status']}；登记月{document['month_id']}；{'成功实付金额' if in_period else '原单据金额（不属本次账期，仅供参考）'}：{document['amount_yuan']+'元' if document['status']=='支付成功' else '不计入成功实付'}。")
    else:
        monthly=raw.get(MONTHLY,[])
        for r in monthly:
            for field in FIELDS.values():r.setdefault(field,None)
        rows,summary=financial_report(scope,monthly,raw.get(EXEC,[]),[],agent.index,document,output.get('breakdown',False),
            settlements=raw.get(SETTLEMENT,[]),writeoffs=raw.get(WRITEOFF,[]),balances=raw.get(BALANCE,[]))
        if 'written_off' in metrics and not raw.get(WRITEOFF):
            summary['written_off_yuan']=None;warnings.append('未查到本楼栋账单核销记录，不能据此认定已结清。')
        if 'unmatched_documents' in metrics and not raw.get(BALANCE):
            summary['unmatched_documents_yuan']=None;warnings.append('未查到相关单据余额记录，不能推定余额为0或已全部结清。')
        if any(r.get('adoption')=='待确认' for r in raw.get(WRITEOFF,[])):warnings.append('有待确认核销关系，建议金额未计入有效核销。')
        for metric in metrics:
            field='document_amount_yuan' if document_comparison and metric=='paid' else 'payable_minus_document_yuan' if document_comparison and metric=='difference' else metric+'_yuan'
            label='本单据成功实付' if document_comparison and metric=='paid' else '应付－本单据成功实付' if document_comparison and metric=='difference' else LABELS[metric]
            value=summary.get(field);lines.append(label+'：'+(value+'元' if value is not None else '未知／资料不完整或不可归属')+'。')
            if value is None and summary.get('known_'+metric+'_yuan') is not None:
                lines.append('已知部分小计：'+summary['known_'+metric+'_yuan']+'元，不作为完整合计。')
        if not monthly:warnings.append('没有符合条件的月度记录，空结果不等于0元。')
        if metrics and summary.get('clean_review_record_count'):warnings.append('所选范围有清洗待核对记录，清洗状态不等于业务审批。')
        if set(metrics)&{'paid','difference'}:
            if summary.get('missing_settlement_count'):warnings.append(f"{summary['missing_settlement_count']}个楼栋月缺T09登记，未按0处理。")
            if summary.get('settlement_review_count'):warnings.append('实付登记含特殊或暂分配记录，尚需核对。')
        if set(metrics)&{'written_off','unmatched_documents'}:
            if summary.get('shared_unmatched_document_count'):warnings.append('相关共享合同另有'+summary['shared_unmatched_documents_yuan']+'元余额，不能全部归本楼栋。')
            if raw.get(EXTRA):warnings.append('另有未分栋合同级有效核销'+yuan(sum(r['effective_cents'] for r in raw[EXTRA]))+'元，单列参考，不加入本楼栋月账。')
        if set(metrics)&{'difference','written_off'}:warnings.append('账面差额与有效核销均不能直接解释为核销后欠款。')
        if 'unmatched_documents' in metrics:warnings.append('单据余额按登记月，含部分核销、待确认和无明细；与账单月分开。')
    if intent!='completeness' and not scope['dimension_keys']:lines.append('无法确认楼栋关联，楼栋资料完整性未知。')
    elif intent!='completeness':
        counts=completeness['counts'];lines.append(f"资料状态（QA登记范围，非本次账期结清判断）：齐全{counts['complete']}栋，仍不完整{counts['incomplete']}栋，未登记{counts['unknown']}栋；原因和覆盖范围可展开。")
    if slots.get('time_note'):warnings.append(slots['time_note'])
    if '为什么' in state['question'] or '原因' in state['question']:warnings.append('目前列示登记金额和账面差额，不能仅凭金额推断差额原因；可继续指定调整或预留明细。')
    lines.extend(warnings)
    display_keys=['month_id','project','building','dimension_key']+[m+'_yuan' for m in metrics]
    display_keys+=['reference_'+m+'_yuan' for m in metrics]
    if document_comparison:display_keys+=['document_amount_yuan','payable_minus_document_yuan']
    visible_rows=rows if intent=='contracts' else [{k:v for k,v in r.items() if k in display_keys} for r in rows]
    visible_summary={k:v for k,v in (summary or {}).items() if k in display_keys}
    data=dict(intent=intent,metrics=metrics,rows=visible_rows[:agent.settings.max_rows] if intent=='contracts' or output['mode']=='monthly' or output.get('details') else [],
              summary_rows=[visible_summary] if summary else [],executions=executions if output.get('details') else [],
              related_buildings=[agent.index.by_dimension[k] for k in scope['dimension_keys']],documents=[],
              selected_document=document,binding=agent.binding,period={'start':slots.get('start'),'end':slots.get('end'),'label':period},
              sql_records=records,completeness=completeness,source_preview=raw.get('采用来源预览',[])[:50],source_preview_truncated=False,
              row_count=len(rows),display_truncated=output['mode']!='total' and len(rows)>agent.settings.max_rows,
              execution_count=len(executions),unique_contract_count=len({r['contract_id'] for r in executions}),
              financial_basis=agent.binding['financial_rules_version'],warnings=warnings,
              output_spec=output,elapsed_ms=round((time.monotonic()-started)*1000))
    if output.get('details'):
        data.update(settlement_preview=[dict(r,registered_paid_yuan=yuan(r['paid_cents']),settlement_income_yuan=yuan(r['settlement_income_cents'])) for r in raw.get(SETTLEMENT,[])[:100]],
                    writeoff_preview=[dict(r,effective_yuan=yuan(r['effective_cents'])) for r in raw.get(WRITEOFF,[])[:100]],
                    unmatched_document_preview=[dict(r,cash_yuan=yuan(r['cash_cents']),written_off_yuan=yuan(r['written_off_cents']),unmatched_yuan=yuan(r['unmatched_cents'])) for r in raw.get(BALANCE,[]) if r['unmatched_cents']!=0][:100])
    agent.audit(dict(question=state['question']+' | '+describe(state),scope=scope,start=slots.get('start'),end=slots.get('end')),records)
    return dict(status='answered',answer='\n'.join(lines),scope=scope,data=data,rules=rules)


def describe(state):
    s=state['slots'];scope=s.get('scope')
    target=scope['label'] if scope else '对象待确认'
    metrics='执行合同' if s.get('intent')=='contracts' else '楼栋资料完整性' if s.get('intent')=='completeness' else '、'.join(LABELS[m] for m in s.get('metrics',[])) or '指标待确认'
    period=f"{s.get('start') or '最早'}—{s.get('end') or '最晚'}" if s.get('start') or s.get('end') else '全部已存日期'
    o=state['output_spec'];return f"{target}；{metrics}；{period}；{'仅合计' if o['mode']=='total' else '按月' if o['mode']=='monthly' else '简洁结果'}{'，含明细' if o.get('details') else '，不展开来源明细'}。"
