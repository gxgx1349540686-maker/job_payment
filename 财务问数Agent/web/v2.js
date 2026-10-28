const $=s=>document.querySelector(s);let session=sessionStorage.getItem('finance.session')||null,stateVersion=sessionStorage.getItem('finance.version')===null?null:Number(sessionStorage.getItem('finance.version')),busy=false,activeController=null;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
const labels={information_status:'楼栋资料状态',registry_version:'登记版本',test_status:'可测试状态',completion_status:'处理状态',completeness_label:'当前标签',completeness_reason:'原因',coverage:'登记覆盖范围',written_off_yuan:'有效核销（账单月，元）',document_written_off_yuan:'单据已核销（登记月，元）',unmatched_documents_yuan:'单据未对应余额（元）',shared_unmatched_documents_yuan:'共享合同待对应（不可全归本栋，元）',unmatched_document_count:'未对应单据数',unknown_receipts_count:'实收缺失条数',unknown_payment_count:'实付登记金额缺失条数',missing_settlement_count:'缺实付登记楼栋月数',registered_paid_yuan:'T09登记实付（元）',settlement_income_yuan:'原账单结算收益（元）',cash_yuan:'成功单据金额（元）',unmatched_yuan:'单据未对应余额（元）',effective_yuan:'本行有效核销（元）',document_month:'单据登记月',writeoff_id:'核销ID',detail_id:'T09明细ID',reconciliation_status:'单据核销状态',basis:'依据',allocation_status:'分摊/核对状态',clean_review_record_count:'清洗非通过月度条数',clean_review_payment_count:'清洗非通过付款笔数',contract_review_row_count:'主合同清洗非通过行数',contract_name:'合同名称',clean_status:'清洗状态',clean_reason:'清洗原因',clean_checked_at:'清洗检查时间',clean_rule_version:'清洗规则版本',scope:'采用范围',project:'项目',building:'楼栋',month_id:'归属／来源月份',dimension_key:'楼栋维度键',building_id:'楼栋ID',record_count:'月度记录数',
receipts_yuan:'我方实收（元）',receivable_yuan:'我方应收业主费用（元）',payable_yuan:'我方应付（元）',paid_yuan:'T09月度实付登记（元）',difference_yuan:'新应付－T09实付登记（元）',receipt_difference_yuan:'实收－应付（元）',
reference_receipts_yuan:'相关楼栋实收参考（元）',reference_receivable_yuan:'相关楼栋应收参考（元）',reference_payable_yuan:'相关楼栋应付参考（元）',adjustment_yuan:'调整（元）',reserve_yuan:'预留（元）',water_yuan:'水费划扣（元）',electricity_yuan:'电费划扣（元）',
document_amount_yuan:'本单据成功金额（元）',payable_minus_document_yuan:'应付－本单据（元）',unknown_payable_count:'未知应付条数',unallocated_payment_count:'待分摊付款笔数',unallocated_payment_yuan:'待分摊付款（元）',attribution:'金额归属说明',
contract_id:'合同ID',contract_type:'类型',contract_start_date:'合同开始',contract_end_date:'合同结束',usage:'用途',document_id:'单据号',amount_yuan:'原金额（元）',status:'状态',actual_date:'实际付款日期',
source_table:'来源表',source_rowid:'来源表实际rowid',source_expense_id:'来源费用ID',source_amount_yuan:'来源金额（元）',adoption_status:'采用状态',category:'科目',reason:'处理原因',payable_record_id:'应付记录ID'};
function table(rows,parent,keys=null){
 if(!rows?.length){parent.append(el('p','暂无符合条件的记录。','empty-note'));return;}
 keys=(keys||Object.keys(rows[0])).filter(k=>rows.some(r=>k in r));
 const wrap=el('div',undefined,'table-wrap'),t=el('table'),head=el('tr');keys.forEach(k=>head.append(el('th',labels[k]||k)));const thead=el('thead');thead.append(head);t.append(thead);
 const body=el('tbody');rows.forEach(row=>{const tr=el('tr');keys.forEach(k=>{const td=el('td',row[k]===null||row[k]===undefined?'未知／不可归属':String(row[k]));td.title=td.textContent;tr.append(td);});body.append(tr);});t.append(body);wrap.append(t);parent.append(wrap);
}
function detail(title,parent,open=false){const d=el('details'),s=el('summary',title);d.open=open;d.append(s);parent.append(d);return d;}
function message(text,type='assistant'){const box=el('article',undefined,'message '+type);box.append(el('div',type==='user'?'你':'财务问数','message-label'),el('div',text,'answer-text'));$('#messages').append(box);return box;}
async function refresh(){try{const r=await fetch('/api/status'),s=await r.json();if(!s.ok)throw new Error(s.detail);$('#status').textContent='本地数据 · '+s.binding.query_version;$('#status').title='已核对正式版本与数据库指纹';if(session){const mr=await fetch('/api/sessions/'+encodeURIComponent(session));if(mr.ok){const ms=await mr.json();stateVersion=ms.version;sessionStorage.setItem('finance.version',String(stateVersion));$('#status').title='已恢复需求条件；财务金额每次从当前库重新查询';}else{session=null;stateVersion=null;sessionStorage.removeItem('finance.session');sessionStorage.removeItem('finance.version');$('#status').title='旧会话已过期，本次请重新说明查询需求';}}}catch(e){$('#status').textContent=e.message;$('#status').classList.add('error');}}
function financeColumns(d){
 const fields=['project','building','month_id',...(d.metrics||[]).map(m=>m+'_yuan')];
 if(d.selected_document)fields.push('document_amount_yuan','payable_minus_document_yuan');
 for(const r of [...d.rows,...d.summary_rows]){
  if(r.attribution?.includes('参考'))fields.push('reference_receipts_yuan','reference_receivable_yuan','reference_payable_yuan');
  if(r.unknown_receipts_count)fields.push('unknown_receipts_count');if(r.unknown_payment_count)fields.push('unknown_payment_count');if(r.missing_settlement_count)fields.push('missing_settlement_count');
  if(r.unmatched_document_count)fields.push('unmatched_documents_yuan');if(r.shared_unmatched_document_count)fields.push('shared_unmatched_documents_yuan');
  if(r.unknown_payable_count)fields.push('unknown_payable_count');if(r.unallocated_payment_count&&!d.financial_basis)fields.push('unallocated_payment_count','unallocated_payment_yuan');
 }
return [...new Set(fields)];
}
function renderResult(response,box){
 box.querySelector('.answer-text').textContent=(response.answer||'').replace(/^查找对象：[^\n]*\n?/,'').split('\n').filter(line=>!line.startsWith('清洗口径：')).join('\n').replace(/我方实收（R36采用T05口径）/g,'我方实收');
 if(response.requirement_state){const m=detail('本次已记住的查询条件',box);const r=response.requirement_state;const ss=r.slots||{};m.append(el('p',`对象：${ss.scope?.label||'待确认'}；期间：${ss.start||'最早'}—${ss.end||'最晚'}；需求版本：${r.version}`),el('p',`指标：${(ss.metrics||[]).map(k=>(labels[k+'_yuan']||k).replace('（元）','')).join('、')||({contracts:'执行合同',completeness:'资料完整性'}[ss.intent]||'待确认')}；输出：${({summary:'简洁结果',total:'仅合计',monthly:'按月'})[r.output_spec.mode]||r.output_spec.mode}；状态：${({draft:'需求草稿',awaiting_fields:'待补充',awaiting_confirmation:'待确认',ready:'准备查询',answered:'已查询',archived:'已结束'})[r.status]||r.status}`));}
 if(response.plan_entry){window.dispatchEvent(new Event('open-payment-plan'));}
 if(response.scope){const title=el('h2',response.scope.label,'scope-title');box.insertBefore(title,box.querySelector('.answer-text'));}
 if(response.choices?.length){const choices=el('div',undefined,'choices');response.choices.forEach(c=>{const b=el('button',c.label+(c.score!==undefined?` · 重合率 ${c.score}%`:''));b.type='button';b.onclick=()=>{if(busy)return;message(c.label,'user');send(null,c.id);};choices.append(b);});box.append(choices);}
 if(response.artifact&&/^\/api\/statements\/[a-f0-9]{32}$/.test(response.artifact.url)){const a=el('a','↓ 下载尾期对账单','artifact-download');a.href=response.artifact.url;a.download=response.artifact.name;box.append(a);}
 if(response.workflow){const info=detail('工作流来源与待核对事项',box);info.append(el('p','原Excel工作流资料 · 机处理待复核稿'));const result=response.workflow.result||{};(result.high_risks||[]).forEach(t=>box.append(el('p','需核对：'+t,'workflow-warning')));(result.notes||[]).forEach(t=>info.append(el('p',t)));if(response.workflow.application?.remarks)info.append(el('p','申请备注：'+response.workflow.application.remarks));if(response.workflow.error)info.append(el('p',response.workflow.error));if(response.workflow.application_source)info.append(el('pre',JSON.stringify(response.workflow.application_source,null,2)));}
 const d=response.data;if(!d)return;
 if(d.completeness?.rows?.length){const qa=detail('楼栋资料完整性与原因',box);table(d.completeness.rows,qa,['project','building','information_status','test_status','completeness_reason','coverage','registry_version']);if(d.completeness.contract_rows?.length){qa.append(el('p','合同处理状态按楼栋分别登记：'));table(d.completeness.contract_rows,qa,['dimension_key','contract_id','completion_status','completeness_label','registry_version']);}}

 if(d.rows?.length){box.append(el('h3',d.intent==='contracts'?'执行合同':'每月明细'));table(d.rows,box,d.intent==='contracts'?['project','building','month_id','contract_id','contract_name','contract_type','contract_review_row_count','contract_start_date','contract_end_date']:financeColumns(d));}
 if(d.display_truncated)box.append(el('p',`显示前${d.rows.length}行，共${d.row_count}行；合计使用全部符合条件记录。`));
 const evidence=detail('查看查询依据',box);evidence.classList.add('basis');const target=box;box=evidence;
 if(d.selected_document){const part=detail('所查单据与同账期成功付款',box,true);table([d.selected_document],part,['document_id','contract_id','month_id','amount_yuan','status','clean_status','clean_reason','actual_date']);table(d.documents,part,['document_id','contract_id','month_id','amount_yuan','status']);}
 if(d.intent!=='contracts'){const c=detail('执行合同（不连接展开累计金额）',box);table(d.executions,c,['project','building','month_id','contract_id','contract_name','contract_type','contract_review_row_count','contract_start_date','contract_end_date']);}
 const buildings=detail('相关楼栋',box);table(d.related_buildings,buildings,['project','building','building_id','dimension_key']);
 const source=detail('采用来源与版本依据',box);source.append(el('p',d.binding.database_file,'source-line'),el('p',d.binding.validation_file,'source-line'));table(d.source_preview,source,['source_table','source_rowid','source_expense_id','month_id','category','source_amount_yuan','adoption_status','reason','clean_status','clean_reason']);if(d.source_preview_truncated)source.append(el('p','仅预览前50条采用来源，不将来源明细再次加入合计。'));
 if(d.clean_review_preview?.length){const quality=detail('月度清洗待核对原因（最多100条）',box);table(d.clean_review_preview,quality,['project','building','month_id','clean_status','clean_reason','clean_checked_at','clean_rule_version']);}
 if(d.settlement_preview?.length){const st=detail('T09月度实付登记与原账单收益（前100条）',box);table(d.settlement_preview,st,['project','building','month_id','contract_id','registered_paid_yuan','settlement_income_yuan','allocation_status','clean_status','clean_reason','basis']);}
 if(d.writeoff_preview?.length){const wr=detail('T10有效核销逐笔（前100条）',box);table(d.writeoff_preview,wr,['writeoff_id','detail_id','document_id','month_id','document_month','effective_yuan','adoption','basis']);}
 if(d.contract_writeoff_preview?.length){const cw=detail('合同级汇总核销（未分栋，前100条）',box);table(d.contract_writeoff_preview,cw,['writeoff_id','detail_id','document_id','contract_id','month_id','effective_yuan','basis']);}
 if(d.unmatched_document_preview?.length){const un=detail('核销单据未对应余额（前100张，含共享合同）',box);un.append(el('p','按单据登记月；共享合同余额不能全部归属本楼栋，完整合计见回答。'));table(d.unmatched_document_preview,un,['document_id','contract_id','month_id','cash_yuan','written_off_yuan','unmatched_yuan','reconciliation_status']);}
 const sql=detail('实际执行SQL及全部绑定参数',box);for(const record of d.sql_records){sql.append(el('b',record.title),el('pre',record.sql),el('pre',JSON.stringify(record.params,null,2)),el('p',`返回${record.rows}行`));}
 const rules=detail('本次取数规则',box);(response.rules||[]).forEach(r=>rules.append(el('p',r.id+' · '+r.title),el('div',r.text),el('small','依据：'+r.source)));
}
async function send(question,selection=null){
 if(busy)return;busy=true;activeController=new AbortController();const controller=activeController;$('#send').textContent='■';$('#send').classList.add('is-busy');$('#send').setAttribute('aria-label','停止接收');
 const oldChoices=[...document.querySelectorAll('.choices button:not(:disabled)')];oldChoices.forEach(b=>b.disabled=true);$('#welcome').hidden=true;if(question)message(question,'user');
 const box=message(''),progress=el('div','正在建立长连接…','stream-progress'),process=detail('正在查询',box,true),processLog=el('div',undefined,'process-log');process.classList.add('process');process.append(processLog);box.insertBefore(progress,box.querySelector('.answer-text'));
 const text=box.querySelector('.answer-text');text.classList.add('stream-text');const sqlBlocks=new Map();let complete=false,received=false;
 try{
  const request={message:question||'',selection,session_id:session,project:'',building:'',scope_type:'auto',entity:'',request_id:crypto.randomUUID(),state_version:stateVersion};
  const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request),signal:controller.signal});
  if(!response.ok){const e=await response.json();throw new Error(typeof e.detail==='string'?e.detail:'输入格式不正确。');}
  await FinanceSSE.consume(response.body,(event,data)=>{
   const follow=window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-280;
   if(follow&&!['result','done'].includes(event))requestAnimationFrame(()=>window.scrollTo({top:document.documentElement.scrollHeight}));
   if(event==='connected')progress.textContent=data.message;
   else if(event==='session'){session=data.session_id;sessionStorage.setItem('finance.session',session);}
   else if(event==='progress')progress.textContent=data.state==='started'?`正在${data.label}…`:data.label+' ✓';
   else if(event==='analysis'){const line=el('div',undefined,'analysis-step');line.append(el('b',data.title),el('p',data.text));processLog.append(line);}
   else if(event==='sql_start'){const part=detail(data.title+' · 生成中',processLog,false),code=el('pre','');part.append(el('p',data.text),code);sqlBlocks.set(data.query_id,{part,code});}
   else if(event==='sql_delta'){const block=sqlBlocks.get(data.query_id);if(block)block.code.append(document.createTextNode(data.text));}
   else if(event==='sql_ready'){const block=sqlBlocks.get(data.query_id);if(block)block.part.append(el('p',data.validation),el('pre',`绑定参数（前10个，共${data.parameter_count}个）：`+JSON.stringify(data.params_preview)));}
   else if(event==='sql_executed'){const block=sqlBlocks.get(data.query_id);if(block){block.part.querySelector('summary').textContent=block.part.querySelector('summary').textContent.replace('生成中','已校验执行');block.part.append(el('p',`${data.text}，返回${data.row_count}行。`));}}
   else if(event==='answer_start')progress.textContent='正在接收已校验结果…';
   else if(event==='answer_delta')text.append(document.createTextNode(data.text));
   else if(event==='result'){process.open=false;process.querySelector('summary').textContent='查看处理过程';received=true;session=data.session_id;stateVersion=data.state_version;sessionStorage.setItem('finance.session',session);sessionStorage.setItem('finance.version',String(stateVersion));renderResult(data,box);progress.textContent=data.status==='answered'?'查询完成 · 已列明采用对象':data.choices?.length?'仅需确认以下歧义':'本轮处理完成';progress.classList.add('complete');}
   else if(event==='error')throw new Error(data.detail||'本轮未完成。');
   else if(event==='done'){complete=true;if(follow)requestAnimationFrame(()=>window.scrollTo({top:Math.max(0,box.offsetTop-94)}));}
  });
  if(!complete)throw new Error('连接提前结束，本轮不自动重试，请手动重新提交。');
 }catch(e){progress.textContent=e.name==='AbortError'?'已停止接收；已发出的请求无法撤回，后续步骤停止。':e.message;progress.classList.add('stream-error');if(!received)oldChoices.forEach(b=>b.disabled=false);if(!text.textContent)text.textContent='本轮没有完整结果。';}
 finally{text.classList.remove('stream-text');busy=false;activeController=null;$('#send').textContent='↑';$('#send').classList.remove('is-busy');$('#send').setAttribute('aria-label','发送问题');$('#question').focus();}
}
$('#composer').addEventListener('submit',event=>{event.preventDefault();if(busy){activeController?.abort();return;}const question=$('#question').value.trim();if(question){$('#question').value='';send(question);}});
$('#question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#composer').requestSubmit();}});
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>{if(busy)return;$('#question').value=b.dataset.question;$('#feature-menu').open=false;$('#question').focus();});
$('#monthly-feature').onclick=()=>{$('#monthly-feature-note').hidden=false;};
document.addEventListener('keydown',e=>{if(e.key==='Escape')$('#feature-menu').open=false;});
document.addEventListener('click',e=>{if(!$('#feature-menu').contains(e.target))$('#feature-menu').open=false;});
$('#new-chat').onclick=()=>{if(busy)return;session=null;stateVersion=null;sessionStorage.removeItem('finance.session');sessionStorage.removeItem('finance.version');$('#messages').replaceChildren();$('#welcome').hidden=false;$('#question').value='';$('#question').style.height='auto';window.scrollTo({top:0});};
$('#question').addEventListener('input',()=>{const q=$('#question');q.style.height='auto';q.style.height=Math.min(q.scrollHeight,150)+'px';});
refresh();
