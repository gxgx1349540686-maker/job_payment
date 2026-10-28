"""R36轻量Agent：自编顺序状态机，模型只补充解析，规则和SQL计划由代码约束。"""
import contextlib
from dataclasses import asdict
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid

from .completeness import explain as explain_completeness
from .memory import SessionStore
from .conversation import Conversation
from .plan_skill import PlanSkills
from .entities import EntityIndex
from .entities import norm
from .periods import parse_months
from .provider import Provider
from .r35_database import R36Database,compile_plan,yuan
from .reporting import financial_report
from .rules import RuleStore
from .settings import ROOT
from .tail_workflow import TailWorkflow,statement_request

METRICS={'receipts':'新实收（R38账期）','receivable':'我方应收业主费用','payable':'我方应付','paid':'T09月度实付汇总登记额',
         'difference':'新应付－T09实付登记额（账面比较）','receipt_difference':'实收－应付','adjustment':'调整','reserve':'预留','water':'水费划扣（历史对照）','electricity':'电费划扣（历史对照）','written_off':'T10有效核销（按T09账单月）'}


class QueryCancelled(ValueError):pass


def parse_intent(question):
    if re.search(r"制定(?:支付)?计划|支付计划制定|资金计划",question) and not re.search(r"不要|不需要|别制定",question):return "payment_plan",[]
    if re.search(r'月度(?:对)?账单|每月账单|月账单',question) and re.search(r'生成|制作|导出|输出|出具|做|需要|给我',question):return 'monthly_unavailable',[]
    if statement_request(question):return 'generate_statement',[]
    if re.search(r'尾期|退出结算',question) and re.search(r'对账单|结算单',question):return 'statement_help',[]
    if re.search(r'(仅|只|筛选).{0,12}(清洗通过|passed)|clean_status\s*=',question,re.I):return 'unsupported_clean_filter',[]
    if re.search(r'房间实收|租客实收|T05-2|水费实收|电费实收',question,re.I):return 'unsupported_detail',[]
    if re.search(r'未收余额|净应收|核销后|预留池余额',question):return 'unsupported',[]
    if '应收' in question and not re.search(r'我方|公司|业主费用|管理费|代管费|分成费',question):return 'clarify_metric',[]
    finance=re.search(r'实收|收入|费用|应收|应付|实付|付款|支付|金额|差额|差异|还差|对账|预留|水费|电费|核销',question)
    if '合同' in question and not finance:return 'contracts',[]
    metrics=[]
    for pattern,key in [(r'实收|收入','receipts'),(r'应收|管理费|代管费','receivable'),(r'应付','payable'),(r'实付|付款|支付|已付','paid'),
                        (r'差额|差异|还差|对账','difference'),(r'实收与应付差额|收入与应付差额','receipt_difference'),(r'预留','reserve'),(r'调整','adjustment'),(r'水费','water'),(r'电费','electricity'),(r'核销','written_off')]:
        if re.search(pattern,question):metrics.append(key)
    if not metrics or re.search(r'费用|财务|总览|概览',question):metrics=['receipts','receivable','payable','paid','difference','receipt_difference']
    # 比较必须把参与比较的两项一起展示。
    if 'difference' in metrics:metrics=list(dict.fromkeys(metrics+['payable','paid']))
    return 'finance',metrics


class Agent:
    def __init__(self,settings,model=None,memory_path=None):
        self.settings=settings;self.db=R36Database(settings);self.binding=self.db.verify_binding()
        self.index=EntityIndex(*self.db.indexes());self.model=model or Provider(settings)
        self.rules=RuleStore(ROOT/'runtime/finance_v31_rules.sqlite',ROOT/'knowledge/finance_v31_rules.json')
        self.prompts=json.loads((ROOT/'knowledge/r36_prompts.json').read_text(encoding='utf8'))
        self.tail_workflow=TailWorkflow()
        self.plan_skills=PlanSkills(ROOT,self.db,self.index)
        self.sessions={};self.lock=threading.Lock();self.audit_lock=threading.Lock()
        self.memory=SessionStore(memory_path or ROOT/'runtime/session_memory.sqlite')
        self.conversation=Conversation(self,self.memory)

    def chat(self,request,on_event=None,cancelled=None):
        return self.conversation.chat(request,on_event,cancelled)

    def _legacy_chat(self,request,on_event=None,cancelled=None):
        self.binding=self.db.verify_binding()
        def emit(event,data):
            if cancelled and cancelled.is_set():raise QueryCancelled('已取消，本轮不会自动重试。')
            if on_event:on_event(event,data)
        def progress(stage,label):emit('progress',{'stage':stage,'label':label,'state':'started'})
        now=time.monotonic()
        with self.lock:
            self.sessions={k:v for k,v in self.sessions.items() if now-v['time']<7200}
            sid=request.get('session_id')
            if sid not in self.sessions:
                if len(self.sessions)>=128:raise ValueError('会话已满，请重启服务后重试。')
                sid=uuid.uuid4().hex;self.sessions[sid]={'time':now,'state':{},'lock':threading.Lock()}
            session=self.sessions[sid]
        with session['lock']:
            emit('session',{'session_id':sid});session['time']=now
            prior=session['state'];selection=request.get('selection')
            if selection:
                choices={c['id']:c for c in prior.get('response',{}).get('choices',[])}
                if selection not in choices:raise ValueError('请选择本轮有效选项。')
                state={k:v for k,v in prior.items() if k!='response'}
                if prior['response']['status']=='clarify_metric':state.update({'intent':'finance','metrics':['receivable'] if selection=='metric:receivable' else ['payable'] if selection=='metric:payable' else ['receipts']})
                else:state['scope']=self.index.resolve(choices[selection])
            else:
                question=(request.get('message') or '').strip()
                if not question:raise ValueError('请输入查询问题。')
                progress('interpret','解析对象、账期和指标')
                intent,metrics=parse_intent(question);start,end,time_note=(None,None,'') if intent in ('monthly_unavailable','payment_plan') else parse_months(question,self.settings.business_year)
                fields=self.index.extract(question,request.get('project',''),request.get('building',''),request.get('entity',''),request.get('scope_type','auto'))
                detected=self.index.extract(question)
                def unique(items):
                    exact=[c for c in items if c['score']==100];high=[c for c in items if c['score']>90]
                    return exact[0] if len(exact)==1 else high[0] if len(high)==1 else None
                if detected['project'] and request.get('project'):
                    from_question=unique(self.index.candidates('project',project=detected['project']))
                    from_form=unique(self.index.candidates('project',project=request['project']))
                    if from_question and from_form and from_question['id']!=from_form['id']:
                        raise ValueError('问题中的项目与输入框项目不同，请保留同一个明确项目后查询。')
                    if from_question and not from_form:fields['project']=detected['project']
                if detected['building'] and request.get('building') and fields['project']:
                    from_question=unique(self.index.candidates('building',project=fields['project'],building=detected['building']))
                    from_form=unique(self.index.candidates('building',project=fields['project'],building=request['building']))
                    if from_question and from_form and from_question['id']!=from_form['id']:
                        raise ValueError('问题中的楼栋与输入框楼栋不同，请保留同一个明确楼栋后查询。')
                if '当月' in question and fields['scope_type']!='document' and not start:raise ValueError('请明确“当月”对应的年月；单据查询可按其来源月份确定。')
                state={'question':question,'intent':intent,'metrics':metrics,'start':start,'end':end,'time_note':time_note,'fields':fields,'mode':'local_rules'}
                if fields['scope_type']=='auto' and prior.get('scope') and re.search(r'这个项目|这个楼栋|该项目|这栋|这个合同|这份合同|这张单据',question):state['scope']=prior['scope']
                if intent in ('finance','generate_statement') and fields['scope_type']=='auto' and not state.get('scope') and self.model.enabled:
                    progress('interpret','调用模型补充提取对象（不发送财务明细）')
                    parsed=self.model.parse(question,self.prompts['parse']);state['mode']='deepseek_parse'
                    if parsed.get('intent')=='generate_statement' and statement_request(question):state['intent']='generate_statement';state['metrics']=[]
                    kind=parsed.get('scope_type','auto')
                    if kind not in ('auto','project','building','contract','document'):kind='auto'
                    state['fields']=self.index.extract(question,str(parsed.get('project') or '')[:150],str(parsed.get('building') or '')[:150],str(parsed.get('entity') or '')[:200],kind)
                    state['model_unverified']=any(norm(parsed.get(k)) not in norm(question) for k in ('project','building','entity') if parsed.get(k))
            intent_label={'payment_plan':'制定计划（本地只读核对）','monthly_unavailable':'月度账单工作流待接入','generate_statement':'生成尾期对账单','statement_help':'尾期出账说明','finance':'费用汇总与比较','contracts':'执行合同查询','clarify_metric':'应收口径待确认','unsupported':'核销口径检查','unsupported_detail':'明细口径检查'}.get(state['intent'],'查询')
            emit('analysis',{'title':'任务解析','text':f"需求：{intent_label}；指标：{'、'.join(METRICS.get(m,m) for m in state['metrics']) or '待确认'}；账期：{state['start'] or '最早'}—{state['end'] or '最晚'}。"})
            def finish(response):
                response.update({'session_id':sid,'binding':self.binding,'mode':state['mode']});state['response']=response;session['state']=state
                return response
            if state['intent']=='payment_plan':return finish({'status':'plan_entry','answer':'请选择楼栋，分别填写支付月份和结算核对月份，开始本地资料核对。金额、材料或合同采用尚未确认时列为待复核，不执行支付。','plan_entry':True,'skill':self.plan_skills.catalog()})
            if state['intent']=='monthly_unavailable':return finish({'status':'feature_unavailable','answer':'月度账单生成入口已预留，工作流尚未接入，目前不能生成月度账单文件。你可以先查询每月实收、应付、实付和差额。'})
            if state['intent']=='statement_help':return finish({'status':'clarify_action','answer':'如需生成文件，请输入“为项目名＋楼栋名生成尾期对账单”。最终工作流按申请资料的退出月份出账；本地生成待复核Excel，不自动付款或核销。'})
            if state['intent']=='unsupported_clean_filter':return finish({'status':'unsupported','answer':'当前财务查询保留正式已采用全范围，清洗状态另行提示。仅passed子集与正式全量不同，本版尚未提供该筛选，不能将全量结果冒充筛选结果。'})
            if state['intent']=='unsupported' and re.search(r'已核销',state['question']) and not re.search(r'核销后|净应收|预留池余额|未收余额',state['question']):state['intent']='finance';state['metrics']=['receipts','payable','paid','written_off']
            if state['intent']=='unsupported':return finish({'status':'unsupported','answer':'当前未完成分项核销和预留释放，不能提供核销后的余额。可以查询应付与成功实付的账面差额。'})
            if state['intent']=='unsupported_detail':return finish({'status':'unsupported','answer':'本版实收查询采用已确认的月度总实收（T05口径）。你指定的是房间或细科目实收，不能用总实收或水电划扣替代；需增加该明细口径后查询。'})
            if state['intent']=='clarify_metric':
                return finish({'status':'clarify_metric','answer':'“应收”口径尚不明确，请选择。','choices':[{'id':'metric:receivable','label':'我方应收业主费用'},{'id':'metric:payable','label':'我方应付费用'},{'id':'metric:receipts','label':'我方实收（收入）'}]})
            if not state.get('scope'):
                progress('resolve','计算对象名称重合率')
                fields=state['fields'];candidates=self.index.candidates(**fields,allow_high=not state.get('model_unverified',False))
                if not candidates:return finish({'status':'clarify_scope','answer':'请填写项目、项目＋楼栋，或合同ID／单据号。可直接查询整个项目。','choices':[]})
                perfect=[c for c in candidates if c['score']==100]
                if not state.get('model_unverified') and (len(perfect)==1 or len(candidates)==1 and candidates[0]['score']>90):
                    state['scope']=self.index.resolve(perfect[0] if perfect else candidates[0]);state['scope']['automatic']=True
                else:return finish({'status':'clarify_scope','answer':'存在多个高匹配候选，请选择。' if any(c['score']>90 for c in candidates) else '没有超过90%的唯一匹配，下面是前5个候选，请确认。','choices':candidates})
            scope=state['scope'];progress('resolve','确认已匹配对象与查询范围')
            emit('analysis',{'title':'采用对象','text':scope['label']+f"；名称重合率{scope['score']:.3f}%（字符串匹配分数，非正确率概率）；覆盖{len(scope['dimension_keys'])}个楼栋维度。"})
            if state['intent']=='generate_statement':
                if state['start'] or state['end']:
                    return finish({'status':'clarify_period','answer':'尾期最终工作流按原申请中的退出月份及规则出账，不按任意查询月份截取。请去掉查询期间后生成；若要更改退出月份，须先核对原申请，不能由模型猜测。','scope':scope})
                keys=scope['dimension_keys']
                if len(keys)!=1:
                    choices=[{'id':'b:'+k,'kind':'building','label':self.index.by_dimension[k]['project']+' / '+self.index.by_dimension[k]['building'],'score':100} for k in keys]
                    return finish({'status':'clarify_scope','answer':'尾期工作流按单栋生成，请选择本次楼栋。','choices':choices})
                dimension=self.index.by_dimension[keys[0]]
                progress('statement','准备最终尾期出账工作流')
                emit('analysis',{'title':'尾期出账依据','text':'调用已保存的最终工作流：租金→水电→汇总。采用原工作流Excel资料，当前数据库仅用于楼栋识别；原财务库和源文件只读，结果为待复核稿。'})
                response=self.tail_workflow.generate(dimension,on_event=emit,cancelled=cancelled)
                response['scope']={'label':dimension['project']+' / '+dimension['building'],'kind':'building','score':scope['score']}
                return finish(response)
            document=self.index.documents.get(scope.get('document_id'))
            if document and not state['start'] and not state['end']:
                state['start']=state['end']=document['month_id'];state['time_note']='单据查询未注明账期，采用该单据的来源月份。'
            if not scope['dimension_keys']:
                if document:
                    amount=document['amount_yuan'] if document['status']=='支付成功' else '不计成功实付'
                    return finish({'status':'answered','answer':f"查找对象：{scope['label']}\n单据状态：{document['status']}；已登记成功金额：{amount}。缺少可确认的楼栋关联，应付与差额保持未知；楼栋信息完整性：未能判定。",
                        'scope':scope,'data':{'intent':'document_only','rows':[],'summary_rows':[],'executions':[],'related_buildings':[],'documents':[document] if document['status']=='支付成功' else [],
                        'selected_document':document,'metrics':list(dict.fromkeys(state['metrics']+['paid','written_off'])) if state['intent']=='finance' else state['metrics'],
                  'source_preview':[],'source_preview_truncated':False,'row_count':0,'display_truncated':False,
                        'period':{'start':state['start'],'end':state['end'],'label':document['month_id']},'sql_records':[],'binding':self.binding},'rules':[]})
                return finish({'status':'unsupported','answer':'对象已识别，但缺少可确认的楼栋关联，不能猜测应付归属或差额。','scope':scope,'document':document})
            self.binding=self.db.verify_binding()
            progress('retrieve','载入当前正式版强制取数规则及相关规则')
            rules=self.rules.search(state['question']+' 我方应付',limit=6)
            emit('analysis',{'title':'取数口径','text':'实收采用R39、应付采用R41的T08及整数分表；实付采用T09楼栋月度汇总，T10有效核销单列；核销单据余额按单据登记月份，不能与账单月混算。'})
            progress('generate_sql','编排参数化只读SQL')
            statements=compile_plan(scope,state['start'],state['end'],self.index)
            if state['intent']=='contracts':statements=[s for s in statements if s.name.startswith('执行合同') or s.name.startswith('楼栋信息完整性') or s.name.startswith('合同在楼栋')]
            raw={};sql_records=[]
            for i,statement in enumerate(statements):
                query_id=f'q{i+1}'
                emit('sql_start',{'query_id':query_id,'title':statement.name,'text':'按已确认对象和账期生成SQL，值通过参数绑定，不拼接用户输入。'})
                for offset in range(0,len(statement.sql),160):emit('sql_delta',{'query_id':query_id,'text':statement.sql[offset:offset+160]})
                emit('sql_ready',{'query_id':query_id,'params_preview':statement.params[:10],'parameter_count':len(statement.params),'validation':'已生成，执行前进行语法树及只读校验'})
                progress('execute','校验并执行：'+statement.name)
                raw[statement.name]=self.db.run(statement,cancelled)
                sql_records.append({'title':statement.name,'sql':statement.sql,'params':statement.params,'rows':len(raw[statement.name])})
                emit('sql_executed',{'query_id':query_id,'row_count':len(raw[statement.name]),'text':'校验通过并已只读执行'})
            executions=raw.get('执行合同（独立查询，不展开金额）',[])
            related=[self.index.by_dimension[k] for k in scope['dimension_keys']]
            for e in executions:
                d=self.index.by_dimension[e['dimension_key']];e.update({'project':d['project'],'building':d['building']})
            if scope.get('contract_id'):executions=[e for e in executions if e['contract_id']==scope['contract_id']]
            progress('answer','组织月度、合计、差额及来源')
            total_only=bool(re.search(r'只.*(?:合计|总数|总额)|仅.*(?:合计|总数|总额)',state['question']))
            if state['intent']=='contracts':rows=executions;summary=None
            else:
                # 金额归属核验仍使用完整执行合同集合，而非筛选后的展示集合。
                all_exec=raw.get('执行合同（独立查询，不展开金额）',[])
                rows,summary=financial_report(scope,raw.get('楼栋月度已确认金额',[]),all_exec,
                    raw.get('成功实付单据（单据号唯一）',[]),self.index,document,bool(re.search(r'各楼栋|每栋|分楼栋|每个楼栋',state['question'])),
                    settlements=raw.get('T09楼栋月度实付汇总',[]),writeoffs=raw.get('T10有效核销（账单月份）',[]),balances=raw.get('核销单据余额（单据登记月份，唯一单据）',[]))
            source=raw.get('采用来源预览',[])
            data={'intent':state['intent'],'rows':[] if total_only else rows[:self.settings.max_rows],'summary_rows':[summary] if summary else [],'executions':executions[:self.settings.max_rows],
                  'execution_count':len(executions),'unique_contract_count':len({r['contract_id'] for r in executions}),
                  'related_buildings':related,'documents':raw.get('成功实付单据（单据号唯一）',[])[:100],'selected_document':document,
                  'metrics':list(dict.fromkeys(state['metrics']+['paid','written_off'])) if state['intent']=='finance' else state['metrics'],
                  'settlement_preview':[dict(r,registered_paid_yuan=yuan(r['paid_cents']),settlement_income_yuan=yuan(r['settlement_income_cents'])) for r in raw.get('T09楼栋月度实付汇总',[])[:100]],'writeoff_preview':[dict(r,effective_yuan=yuan(r['effective_cents'])) for r in raw.get('T10有效核销（账单月份）',[])[:100]],
                  'unmatched_document_preview':[dict(r,cash_yuan=yuan(r['cash_cents']),written_off_yuan=yuan(r['written_off_cents']),unmatched_yuan=yuan(r['unmatched_cents'])) for r in raw.get('核销单据余额（单据登记月份，唯一单据）',[]) if r['unmatched_cents']!=0][:100],
                  'unmatched_document_total_count':sum(r['unmatched_cents']!=0 for r in raw.get('核销单据余额（单据登记月份，唯一单据）',[])),
                  'contract_writeoff_preview':[dict(r,effective_yuan=yuan(r['effective_cents'])) for r in raw.get('合同级核销汇总（未分栋，不计月账实付）',[])[:100]],
                  'contract_writeoff_yuan':yuan(sum(r['effective_cents'] for r in raw.get('合同级核销汇总（未分栋，不计月账实付）',[]))),
                  'contract_writeoff_count':len(raw.get('合同级核销汇总（未分栋，不计月账实付）',[])),
                  'financial_basis':self.binding['financial_rules_version']+'；T09/T10 '+self.binding['t09_revision']+'；账单月与单据登记月分别展示','source_preview':source[:50],'source_preview_truncated':len(source)>50,
                  'row_count':len(rows),'display_truncated':not total_only and len(rows)>self.settings.max_rows,
                  'period':{'start':state['start'],'end':state['end'],'label':f"{state['start'] or '最早'}—{state['end'] or '最晚'}" if state['start'] or state['end'] else '全部已存日期'},
                  'clean_review_preview':[{k:r.get(k) for k in ['record_id','project','building','month_id','clean_status','clean_reason','clean_checked_at','clean_rule_version']} for r in raw.get('楼栋月度已确认金额',[]) if r.get('clean_status')!='passed'][:100],
                  'sql_records':sql_records,'binding':self.binding,'clean_policy':'正式已采用全范围；clean_status仅标示数据质量，不代表审批或业务生效'}
            data['completeness']=explain_completeness(scope,raw.get('楼栋信息完整性（当前QA登记，非逐月结清）',[]),raw.get('合同在楼栋范围内的QA状态',[]),self.index)
            emit('analysis',{'title':'楼栋资料完整性','text':data['completeness']['explanation']})
            answer=self.render(state,scope,data)
            response={'status':'answered','answer':answer,'scope':scope,'data':data,'rules':rules}
            self.audit(state,sql_records)
            return finish(response)

    def render(self,state,scope,data):
        answer=f"查找对象：{scope['label']}\n账期：{data['period']['label']}；数据：{data['binding']['query_version']}正式版。"
        if data.get('completeness'):answer+='\n'+data['completeness']['explanation']
        if state.get('time_note'):answer+='\n'+state['time_note']
        if state['intent']=='contracts':return answer+'\n合同采用当前正式库名称；contract_review_row_count为该主合同非passed原始行数，不等同合同无效。'+f"\n查到{data['unique_contract_count']}份执行合同、{data['execution_count']}条执行月份记录；类型和日期见表格。无执行合同不代表该楼栋没有历史合同。"
        s=data['summary_rows'][0]
        answer+='\n实收：1月＝当月租赁＋赔偿，2月起另加上一费用账期水电；应付＝当月租赁＋赔偿－预留＋已确认调整－我方应收业主费用，退出比例按R41已采用规则。'
        if s.get('clean_review_record_count'):answer+=f"\n有{s['clean_review_record_count']}条月度记录清洗非通过，需核对；清洗状态不等于业务审批。"
        for key in list(dict.fromkeys(state['metrics']+['paid','written_off'])):
            value=s.get(key+'_yuan')
            answer+=f"\n{METRICS[key]}：{value+'元' if value is not None else '未知／数据不完整或不可归属'}。"
        for field,label,count in [('receipts','实收','unknown_receipts_count'),('payable','应付','unknown_payable_count'),('paid','实付登记','unknown_payment_count')]:
            if s.get(count) or field=='paid' and s.get('missing_settlement_count'):
                known=s.get('known_'+field+'_yuan')
                answer+=f"\n{label}存在缺失：已知部分小计{known or '未知'}元，不作为完整合计。"
        if s.get('missing_settlement_count'):answer+=f"\n有{s['missing_settlement_count']}个楼栋月未在T09实付汇总登记，不能直接当0元。"
        if s.get('settlement_review_count'):answer+=f"\nT09有{s['settlement_review_count']}条特殊/暂分配记录需核对，展示登记值不代表分摊已最终确认。"
        if s.get('unmatched_document_count'):
            answer+=f"\n核销单据尚未对应实付汇总的余额：{s['unmatched_documents_yuan']}元，涉及{s['unmatched_document_count']}张单据；含部分核销、待确认及无明细，并非全部缺失单据。"
        if s.get('shared_unmatched_document_count'):
            answer+=f"\n相关共享合同另有{s['shared_unmatched_documents_yuan']}元单据余额（{s['shared_unmatched_document_count']}张），不能全部归到本楼栋。"
        if s.get('unmatched_document_count') or s.get('shared_unmatched_document_count'):
            answer+='\n以上余额按单据登记月份统计；账单与单据完全对应后，可停止展示这项临时说明。'
        if data.get('contract_writeoff_count'):
            answer+=f"\n另有相关合同级汇总有效核销{data['contract_writeoff_yuan']}元，缺少逐栋月度覆盖依据，单列参考，不加入本楼栋月度实付或核销。"
        if not s['record_count']:answer+='\n没有符合账期的月度记录，不能认定为0元。'
        if data.get('selected_document'):
            d=data['selected_document'];answer+=f"\n所查单据：{d['document_id']}；登记月{d['month_id']}；状态{d['status']}；原金额{d['amount_yuan']}元。单笔金额与T09整月登记额分开。"
        return answer+'\n有效核销对应T09原结算账单收益，不直接表示新T08应付已结清；账单月与单据登记月不同，不将单据余额重复计入实付或欠款。'

    def audit(self,state,statements):
        with self.audit_lock,contextlib.closing(sqlite3.connect(ROOT/'runtime/audit.sqlite')) as c:
            c.execute('CREATE TABLE IF NOT EXISTS queries_v2 (at TEXT,question_sha256 TEXT,scope TEXT,period TEXT,sql_records TEXT,database_sha256 TEXT)')
            c.execute("INSERT INTO queries_v2 VALUES (datetime('now'),?,?,?,?,?)",(hashlib.sha256(state['question'].encode()).hexdigest(),state['scope']['label'],json.dumps([state['start'],state['end']]),json.dumps(statements,ensure_ascii=False),self.db.hash));c.commit()
