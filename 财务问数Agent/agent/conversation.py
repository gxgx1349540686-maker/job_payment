"""Requirement-first multi-turn coordinator: slots, corrections, confirmation, then SQL."""
import copy
import json
import re
import time
import threading
import uuid
from .periods import parse_months
from .entities import norm
from .query_service import run,describe,LABELS

PATTERNS=[('receipts',r'实收|收入'),('receivable',r'我方应收|公司应收|业主费用|管理费|代管费|分成费'),
          ('payable',r'应付'),('paid',r'实付|已付|付款|支付金额'),('written_off',r'核销'),
          ('reserve',r'预留'),('adjustment',r'调整'),('water',r'水费'),('electricity',r'电费')]
METRIC_CHOICES=[{'id':'metric:'+k,'label':LABELS[k]} for k in ['receipts','receivable','payable','paid','written_off','difference']]


def metrics_in(q):
    unmatched=bool(re.search(r'未核销|未对应.*余额|单据余额',q))
    text=re.sub(r'未核销|未对应.*余额|单据余额','',q) if unmatched else q
    result=[key for key,pattern in PATTERNS if re.search(pattern,text)]
    if unmatched:result.append('unmatched_documents')
    if re.search(r'差额|差异|相差|还差|差多少|差了多少|差多少钱|对比|比较',q):
        if 'receipts' in result and 'payable' in result and 'paid' not in result:
            result+=['receipt_difference','receipts','payable']
        else:result+=['difference','payable','paid']
    return list(dict.fromkeys(result))


class Conversation:
    def __init__(self,agent,store):self.agent=agent;self.store=store

    def chat(self,request,on_event=None,cancelled=None):
        turn_started=time.monotonic()
        agent=self.agent;agent.binding=agent.db.verify_binding();emit=on_event or (lambda *_:None)
        state=self.store.load(request.get('_user_id','local_cli'),request.get('session_id'),agent.binding['database_sha256'])
        request_id=request.get('request_id') or uuid.uuid4().hex
        payload={k:v for k,v in request.items() if k not in ('_user_id','request_id','state_version')}
        cached=self.store.claim(state,request_id,payload,request.get('state_version'))
        if cached:
            if cached.get('binding',{}).get('database_sha256')!=agent.binding['database_sha256']:
                raise ValueError('该旧请求属于另一数据库版本，请重新提交；不会回放旧金额。')
            emit('session',{'session_id':state['session_id'],'state_version':cached['state_version']});return cached
        emit('session',{'session_id':state['session_id'],'state_version':state['version']})
        state['mode']='local_rules'
        state['turn_diagnostics']={'started':turn_started,'model_calls':0,'context_chars':0,'token_usage':None}
        question=(request.get('message') or '').strip();choice=request.get('selection')
        prior=copy.deepcopy(state);state['question']=(prior_question if (prior_question:=state.get('question','')) and request.get('selection') else question or prior_question)
        try:
            result=self.advance(state,prior,question,choice,request,emit,cancelled)
            result.update(session_id=state['session_id'],binding=agent.binding,mode=state.get('mode','local_rules'))
            d=state.pop('turn_diagnostics');d['elapsed_ms']=round((time.monotonic()-d.pop('started'))*1000);result['diagnostics']=d
            self.store.remember(state,question or choice or '',result)
            if result.get('data'):state['tool_refs'].append({'at':time.time(),'database_sha256':agent.binding['database_sha256'],
                                                          'queries':[r['title'] for r in result['data']['sql_records']]})
            self.store.save(state,result,request_id)
            return result
        except Exception:
            self.store.fail(state,request_id);raise

    def advance(self,state,prior,q,choice,request,emit,cancelled):
        agent=self.agent
        if cancelled and cancelled.is_set():raise ValueError('已取消，本轮未自动重试。')
        if state['database_sha256']!=agent.binding['database_sha256']:
            state['slots'].pop('scope',None);state['confirmed_items']={};state['needs_confirmation']=True
            state['database_sha256']=agent.binding['database_sha256'];state['choices']=[]
            state['assumptions']=['数据版本已变化，旧确认失效，需要重新核对。']
            if choice or re.fullmatch(r'确认|是|好的|就这样|没问题',q):
                return self.ask(state,'scope','数据版本已变化，请重新说明查询对象和需求；不会沿用旧确认。',[])
        if prior.get('legacy_pending'):
            return self.legacy(state,request,emit,cancelled)
        if not choice and prior.get('choices'):
            numbered=re.fullmatch(r'第?([1-5一二三四五])(?:个|项)?',q)
            if numbered:
                n='一二三四五'.find(numbered[1])+1 if numbered[1] in '一二三四五' else int(numbered[1])
                if n<=len(prior['choices']):choice=prior['choices'][n-1]['id']
        if re.fullmatch(r'谢谢|感谢|你好|您好|嗨|再见',q) or prior['status']=='answered' and re.fullmatch(r'好的|好|收到|知道了',q):
            return dict(status='acknowledged',answer='已保留当前查询条件；需要时可以继续补充问题。本轮没有查询数据库。',choices=state.get('choices',[]))
        slots=state['slots'];was_pending=prior['status'] in ('awaiting_fields','awaiting_confirmation')
        if choice:
            choices={c['id']:c for c in state['choices']}
            if choice not in choices:raise ValueError('请选择本轮有效选项，旧选项不能覆盖当前需求。')
            selected=choices[choice]
            if choice=='confirm:yes':
                if state['status']!='awaiting_confirmation':raise ValueError('当前没有待确认需求。')
                state['needs_confirmation']=False
            elif choice.startswith('metric:'):
                slots.update(intent='finance',metrics=[choice.split(':',1)[1]]);state['needs_confirmation']=True
                if slots['metrics']==['difference']:slots['metrics']+=['payable','paid']
            else:
                slots['scope']=agent.index.resolve(selected);slots['fields']=self.fields_from_scope(slots['scope'])
                state['needs_confirmation']=True
            state['question']=prior.get('question','')+'；补充：'+selected['label']
        elif re.fullmatch(r'确认|确认查询|是|是的|好的|就这样|没问题|开始查询',q):
            if prior['status']!='awaiting_confirmation':return self.ask(state,'intent','当前没有待确认需求，请输入查询问题。',[])
            state['needs_confirmation']=False
        else:
            if not q:raise ValueError('请输入查询问题。')
            if re.fullmatch(r'取消|不用了|结束查询',q):
                state.update(status='archived',slots={},constraints={},pending_questions=[],choices=[],confirmed_items={})
                return dict(status='cancelled',answer='本次需求已取消；继续输入将开始新的查询。')
            if prior['status']=='archived':
                state.update(slots={},constraints={},confirmed_items={},needs_confirmation=False,assumptions=[],output_spec={'mode':'summary','details':False,'breakdown':False})
                slots=state['slots']
            # Preserve separate features, but don't let model output trigger a workflow by itself.
            from .engine import parse_intent
            legacy_intent,_=parse_intent(q)
            if legacy_intent in ('payment_plan','monthly_unavailable','generate_statement','statement_help','unsupported_clean_filter','unsupported_detail','unsupported'):
                return self.legacy(state,request,emit,cancelled)
            emit('progress',{'stage':'interpret','label':'理解问题并整合会话需求','state':'started'})
            explicit=agent.index.extract(q,request.get('project',''),request.get('building',''),request.get('entity',''),request.get('scope_type','auto'))
            # An explicit independent full question starts fresh slots; short follow-ups inherit them.
            independent=(((explicit.get('project') and explicit.get('building')) or explicit.get('scope_type') in ('contract','document') and explicit.get('entity')!=slots.get('fields',{}).get('entity')) and not was_pending and
                         not re.search(r'再|改|换|继续|还是|这个|这栋|这份|这张',q))
            if independent:
                state.update(slots={},constraints={},confirmed_items={},output_spec={'mode':'summary','details':False,'breakdown':False},assumptions=[])
                slots=state['slots']
            negatives=re.findall(r'(?:不要|不需要|不看|不用|排除)([^，。；;]+)',q)
            excluded=list(dict.fromkeys(m for text in negatives for m in metrics_in(text)))
            positive=re.sub(r'(?:不要|不需要|不看|不用|排除)[^，。；;]+','',q)
            selected_metrics=metrics_in(positive)
            if re.search(r'总览|全部费用|所有费用',positive):selected_metrics=['receipts','receivable','payable','paid','difference']
            intent='completeness' if re.search(r'齐全|完整性|信息完整|可测试',positive) and not selected_metrics else 'contracts' if '合同' in positive and not selected_metrics else 'finance'
            if selected_metrics:
                slots.update(intent='finance',metrics=selected_metrics)
            elif intent in ('contracts','completeness'):slots.update(intent=intent,metrics=[])
            elif re.search(r'应收',positive) or re.search(r'费用|查什么',positive) and not selected_metrics or re.search(r'金额|多少钱',positive) and not slots.get('metrics'):
                slots.update(intent='finance',metrics=[])
            if excluded:
                state['constraints']['excluded_metrics']=excluded
                slots['metrics']=[m for m in slots.get('metrics',[]) if m not in excluded]
                # Excluding an operand does not silently change a requested difference definition.
                if 'difference' in slots['metrics'] and set(excluded)&{'paid','payable'}:
                    return self.ask(state,'metrics','计算差额需要应付和实付。你希望只看应付，还是保留差额所需数据？',METRIC_CHOICES)
            elif selected_metrics:state['constraints'].pop('excluded_metrics',None)
            o=state['output_spec']
            if re.search(r'只.*(?:合计|总数|总额)|仅.*(?:合计|总额)|不要明细|不看明细',q):o.update(mode='total',details=False)
            elif re.search(r'每月|逐月|按月',q):o['mode']='monthly'
            if re.search(r'查看明细|展开明细|详细|来源|依据|SQL|sql',positive):o.update(details=True,mode='monthly' if slots.get('intent')=='finance' else 'summary')
            if re.search(r'各楼栋|每栋|分楼栋|每个楼栋',q):o.update(breakdown=True,mode='monthly')
            if re.search(r'不要明细|不看明细|不展开',q):o['details']=False
            period_text=q
            if re.search(r'不对|不是|改成|改为',q) and not re.search(r'至|到',q):
                period_text=re.split(r'不对|改成|改为|，是|,是',q)[-1]
            if re.search(r'全部日期|所有月份|不限时间|全部月份',q):slots.update(start=None,end=None,time_note='全部已存日期。')
            else:
                try:
                    cn={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'十一':11,'十二':12}
                    period_text=re.sub(r'(十二|十一|十|[一二三四五六七八九])(?=月|至|到)',lambda m:str(cn[m[1]]),period_text)
                    base_year=int(slots['start'][:4]) if slots.get('start') else agent.settings.business_year
                    start,end,note=parse_months(period_text,base_year)
                except ValueError as exc:
                    if explicit.get('scope_type')!='auto':slots['fields']=explicit;slots.pop('scope',None)
                    return self.ask(state,'period',str(exc),[])
                if start or end:slots.update(start=start,end=end,time_note=note)
                else:slots.setdefault('start',None);slots.setdefault('end',None)
                if note:state['assumptions']=[note]
            fields=explicit
            if re.search(r'这个项目|该项目',q) and slots.get('fields',{}).get('project') and not explicit.get('project'):
                fields=dict(scope_type='project',project=slots['fields']['project'],building='',entity='')
            old_fields=slots.get('fields',{})
            if fields['scope_type']=='auto' and (slots.get('scope') or old_fields.get('scope_type','auto')!='auto'):
                fields=old_fields
            elif fields['scope_type']=='building' and not fields.get('project') and old_fields.get('project'):
                fields=dict(fields,project=old_fields['project'])
            # Free-text clarification can name a previously offered object, without clicking.
            if was_pending and prior.get('pending_questions') and prior['pending_questions'][0]['slot']=='scope':
                offered=[c for c in prior['choices'] if norm(q)==norm(c['label']) or norm(q)==norm(c['label'].split(' / ')[-1])]
                if len(offered)==1:slots['scope']=agent.index.resolve(offered[0]);fields=self.fields_from_scope(slots['scope'])
                elif fields['scope_type']=='auto' and not slots.get('scope'):
                    fields=agent.index.extract(q,project=q if old_fields.get('scope_type') in ('auto','project') else old_fields.get('project',''),
                                               building=q if old_fields.get('scope_type')=='building' else '',scope_type=old_fields.get('scope_type','project'))
            if fields!=old_fields:slots.pop('scope',None)
            slots['fields']=fields
            state['needs_confirmation']=state.get('needs_confirmation',False) or was_pending
            if re.search(r'取消筛选|不筛选|不用筛选|直接按月|去掉筛选',q):state['constraints'].pop('unsupported_filter',None)
            unsupported=re.search(r'超过|高于|低于|大于|小于|不少于|不超过|前\d+|最高|最低|平均|排名|环比|同比|占比|百分比|机构|按主体|币种|美元|港币|欧元|USD|HKD|EUR',positive)
            if unsupported:state['constraints']['unsupported_filter']=q
            if state['constraints'].get('unsupported_filter'):
                return self.ask(state,'unsupported_filter','当前版本支持按对象、月份、指标查询及合计，尚不支持金额/机构/币种筛选、排名或复杂统计。该条件没有被忽略执行；可回复“取消筛选，直接按月查询”，或重新说明需求。',[])
            # One optional parse request; no raw SQL, no financial tool results sent to the model.
            unknown=not slots.get('intent') or (slots.get('intent')=='finance' and not slots.get('metrics'))
            if (unknown or fields['scope_type']=='auto' and not slots.get('scope')) and agent.model.enabled and not re.search(r'费用|应收',q):
                context={'slots':{k:v for k,v in slots.items() if k!='scope'},'constraints':state['constraints'],
                         'pending_questions':state['pending_questions'],'summary':state['summary'],
                         'output_spec':state['output_spec'],'assumptions':state['assumptions'],
                         'recent_messages':state['recent_messages'][-6:]}
                model_input=json.dumps({'question':q,'working_state':context},ensure_ascii=False)
                state['turn_diagnostics'].update(model_calls=1,context_chars=len(model_input))
                try:parsed=agent.model.parse(model_input,agent.prompts['conversation'])
                except ValueError:return self.ask(state,'metrics','模型解析未完成，本轮未查询且没有自动重试。请直接说明项目、楼栋、指标和月份。',METRIC_CHOICES)
                if not isinstance(parsed,dict):return self.ask(state,'metrics','未取得可靠需求，请明确查询指标。',METRIC_CHOICES)
                state['turn_diagnostics']['token_usage']=parsed.get('_usage')
                state['mode']='deepseek_parse'
                if isinstance(parsed.get('metrics'),list) and parsed['metrics'] and all(m in LABELS for m in parsed['metrics']):slots.update(metrics=[m for m in dict.fromkeys(parsed['metrics']) if m not in state['constraints'].get('excluded_metrics',[])],intent='finance')
                if parsed.get('intent') in ('contracts','completeness'):slots.update(intent=parsed['intent'],metrics=[])
                if parsed.get('project') or parsed.get('building') or parsed.get('entity'):
                    candidate=agent.index.extract(q,str(parsed.get('project') or '')[:150],str(parsed.get('building') or '')[:150],str(parsed.get('entity') or '')[:200],parsed.get('scope_type') if parsed.get('scope_type') in ('project','building','contract','document') else 'auto')
                    if not slots.get('scope'):slots['fields']=candidate
                state['needs_confirmation']=True
            if state.get('mode')!='deepseek_parse' and not was_pending and not selected_metrics and intent=='finance' and explicit.get('scope_type')=='auto' and not re.search(r'费用|应收|查|多少钱|金额|月|年|呢|这个|这栋|该项目|这份|这张|明细|合计|总额|按月|每月|全部|修改|改查|再查|重新|为什么|原因',q):
                return self.ask(state,'intent','本轮未识别出新的查询或修改，请说明指标、月份或输出要求；已保存的条件不会丢失。',[])
            state['confirmed_items']={k:{'source':'user_explicit_or_inherited','version':state['version']+1} for k in ('metrics','start','end','fields') if k in slots}
        if slots.get('intent')=='finance' and not slots.get('metrics') or not slots.get('intent'):
            return self.ask(state,'metrics','你想查什么？可直接输入“应付”“实付”“执行合同”等；费用也可选择下列口径。',METRIC_CHOICES)
        if not slots.get('scope'):
            fields=slots.get('fields',{'scope_type':'auto'})
            candidates=agent.index.candidates(**fields)
            exact=[c for c in candidates if c['score']==100]
            if len(exact)==1 or len(candidates)==1 and candidates[0]['score']>90:
                slots['scope']=agent.index.resolve(exact[0] if exact else candidates[0])
                slots['fields']=self.fields_from_scope(slots['scope'])
            else:return self.ask(state,'scope','请确认查询对象；可以点击候选，也可以直接输入项目＋楼栋、合同ID或单据号。',candidates)
        if state.get('needs_confirmation'):
            state['status']='awaiting_confirmation';state['pending_questions']=[{'id':'confirm','slot':'confirmation','question':describe(state)}]
            state['choices']=[{'id':'confirm:yes','label':'确认查询'}]
            return dict(status='confirm_requirements',answer='请核对本次需求：\n'+describe(state)+'\n回复“确认”执行，也可以直接输入修改内容。',choices=state['choices'])
        slots.setdefault('start',None);slots.setdefault('end',None)
        if slots['scope']['kind']=='document' and not slots['start'] and not slots['end']:
            doc=agent.index.documents[slots['scope']['document_id']];slots.update(start=doc['month_id'],end=doc['month_id'],time_note='单据未指定账期，采用单据来源月份。')
        state.update(status='ready',pending_questions=[],choices=[])
        state['confirmed_items']={k:{'confirmed':True,'source':'user_confirmation' if choice=='confirm:yes' or re.fullmatch(r'确认|确认查询|是|是的|好的|就这样|没问题|开始查询',q) else 'explicit_request','value':v,'version':state['version']+1} for k,v in slots.items() if k in ('intent','metrics','start','end','fields')}
        state['confirmed_items']['scope']={'confirmed':True,'value':{k:slots['scope'][k] for k in ('id','kind','label')},'version':state['version']+1}
        state['confirmed_items']['output_spec']={'confirmed':True,'value':copy.deepcopy(state['output_spec']),'version':state['version']+1}
        emit('progress',{'stage':'query','label':'执行确认范围的只读查询','state':'started'})
        result=run(agent,state,emit,cancelled);state['status']='answered'
        return result

    @staticmethod
    def fields_from_scope(scope):
        return dict(scope_type=scope['kind'],project=scope['label'].split(' / ')[0] if scope['kind'] in ('building','project') else '',
                    building=scope['label'].split(' / ')[-1] if scope['kind']=='building' else '',
                    entity=scope.get('contract_id','') if scope['kind']=='contract' else scope.get('document_id','') or '')

    @staticmethod
    def ask(state,slot,text,choices):
        state.update(status='awaiting_fields',needs_confirmation=True,choices=choices,
                     pending_questions=[{'id':slot,'slot':slot,'question':text}])
        return dict(status='clarify_metric' if slot=='metrics' else 'clarify_scope' if slot=='scope' else 'clarify_requirement',answer=text,choices=choices)

    def legacy(self,state,request,emit,cancelled):
        agent=self.agent;payload=dict(request);snapshot=state.get('legacy_snapshot')
        if snapshot:
            selection=payload.get('selection');q=(payload.get('message') or '').strip()
            if not selection and q.isdigit() and 0<int(q)<=len(state.get('choices',[])):selection=state['choices'][int(q)-1]['id']
            if selection:
                if selection not in {c['id'] for c in state['choices']}:raise ValueError('请选择当前工作流的有效对象。')
                legacy_sid=uuid.uuid4().hex
                agent.sessions[legacy_sid]={'time':time.monotonic(),'state':snapshot,'lock':threading.Lock()}
                payload.update(session_id=legacy_sid,selection=selection)
            else:payload.update(session_id=None,selection=None,message=state['legacy_request']+'；'+q)
        else:payload.update(session_id=None,selection=None)
        def forward(name,data):
            if name!='session':emit(name,data)
        result=agent._legacy_chat(payload,forward,cancelled)
        legacy_session=agent.sessions.pop(result.get('session_id'),None)
        if result.get('choices') and legacy_session:
            state.update(status='awaiting_fields',legacy_pending=True,legacy_snapshot=legacy_session['state'],
                         legacy_request=payload.get('message',''),choices=result['choices'],
                         pending_questions=[{'id':'legacy_scope','slot':'legacy_scope','question':result['answer']}])
        else:
            state.update(status='answered',pending_questions=[],choices=[])
            for key in ('legacy_pending','legacy_snapshot','legacy_request'):state.pop(key,None)
        return result
