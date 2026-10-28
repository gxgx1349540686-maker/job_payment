const $=s=>document.querySelector(s);let session=null,busy=false;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
const labels={month_id:'归属／来源月份',category:'科目／核对状态',description:'流水备注',record_count:'记录数',known_amount_count:'金额已知条数',amount_yuan:'金额（元）',record_id:'业务记录ID',contract_id:'合同ID',contract_name:'合同名称',contract_type:'合同类型',contract_start_date:'合同开始',contract_end_date:'合同结束',building_id:'楼栋ID',exit_month:'退出月份',exit_form:'退出形式'};
function table(rows,parent){if(!rows?.length)return;const wrap=el('div',undefined,'table-wrap'),t=el('table'),thead=el('thead'),tr=el('tr');const keys=Object.keys(rows[0]);keys.forEach(k=>tr.append(el('th',labels[k]||k)));thead.append(tr);t.append(thead);const body=el('tbody');rows.forEach(r=>{const row=el('tr');keys.forEach(k=>{const cell=el('td',r[k]===null?'未知／空白':String(r[k]));cell.title=cell.textContent;row.append(cell);});body.append(row);});t.append(body);wrap.append(t);parent.append(wrap);}
function detail(title,parent){const d=el('details'),s=el('summary',title);d.append(s);parent.append(d);return d;}
function message(text,type='assistant'){const box=el('article',undefined,'message '+type);box.append(el('div',type==='user'?'你':'财务问数','message-label'));box.append(el('div',text,'answer-text'));$('#messages').append(box);return box;}
async function refresh(){try{const r=await fetch('/api/status'),s=await r.json();if(!s.ok)throw new Error(s.detail);$('#status').textContent=`● R33数据库指纹验证通过 · 正式入口 ${s.binding.formal_workbook} / ${s.binding.formal_version} · ${s.rules}条取数规则`;$('#mode-note').textContent=s.model_configured?`DeepSeek · ${s.model} · 只读查询`:'本地模板模式 · API Key尚未配置';if(!s.model_configured)$('#status').textContent+='。当前使用本地模板，可先试查；配置Key后启用DeepSeek。';}catch(e){$('#status').textContent=e.message;$('#status').classList.add('error');}}
function renderResult(d,box){
  box.querySelector('.answer-text').textContent=d.answer;
if(d.choices?.length){const options=el('div',undefined,'choices');d.choices.forEach(c=>{const b=el('button',c.label+(c.score!==undefined?` · 相似度 ${c.score}`:''));b.type='button';b.onclick=()=>send(null,c.id);options.append(b);});box.append(options);}
if(d.data){table(d.data.rows,box);const p=d.data.provenance;for(const issue of p.issues||[]){const part=el('div',undefined,'issue');part.append(el('b',`${issue.项目名} / ${issue.楼栋名} · ${issue.字段}`),el('div',`${issue.问题}；原值：${issue.原值}`));for(const loc of issue.实际来源位置||[])part.append(el('div',`${loc.来源工作表} · 实际行 ${loc.原工作表行号}（来源记录号 ${loc.来源记录号}）`));box.append(part);}
const src=detail('查看数据来源与取数口径',box);src.append(el('p',`数据库：${d.data.binding.query_version}（${d.data.binding.query_state}）`,'source-line'),el('p',`业务表：${p.physical_table}；${p.business_note}`,'source-line'),el('p',`文件：${d.data.binding.database_file}`,'source-line'),el('p',`验收：${p.validation_file}`,'source-line'));if(p.source_workbook)src.append(el('p',`原始迁移工作簿：${p.source_workbook}`,'source-line'));if(p.source_rows?.length){src.append(el('p','楼栋全部来源对照（并非全部均被本次账期采用）'));table(p.source_rows,src);}if(p.lineage?.length){const line=detail('逐笔采用与来源字段',src);table(p.lineage,line);}
const records=detail('查看业务记录明细（最多预览50条）',box);table(d.data.detail_preview,records);if(d.data.detail_truncated)records.append(el('p','明细仅预览50条；上方分组与合计使用全部符合条件记录。'));
const rules=detail('查看本次使用的取数规则',box);(d.rules||[]).forEach(rule=>{rules.append(el('p',`${rule.id} · ${rule.title}`),el('div',rule.text),el('div',`依据：${rule.source}`));});const sql=detail('查看只读SQL与执行范围',box);sql.append(el('pre',d.data.sql),el('p',`服务器预先限定楼栋：${d.building.building_id}；日期：${d.data.period.label}`));box.append(el('div',d.trace.join(' → '),'trace'));}

}
let activeController=null;
async function send(question,selection=null){
  if(busy)return;
  busy=true;const controller=new AbortController();activeController=controller;
  const sendButton=$('#send');sendButton.disabled=false;sendButton.textContent='停止接收';
  const priorChoices=[...document.querySelectorAll('.choices button:not(:disabled)')];priorChoices.forEach(b=>b.disabled=true);
  $('#welcome').hidden=true;if(question)message(question,'user');
  const box=message(''),progress=el('div','正在建立流式连接…','stream-progress');box.insertBefore(progress,box.querySelector('.answer-text'));
  const text=box.querySelector('.answer-text');text.classList.add('stream-text');
  let complete=false,resultReceived=false,receivedError=false;
  try{
    const payload={message:question||'',session_id:session,selection,project:$('#project').value.trim(),building:$('#building').value.trim()};
    const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});
    if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:'问题格式不正确，请检查输入');}
    if(!response.body||!response.headers.get('content-type')?.includes('text/event-stream'))throw new Error('服务未返回流式响应，请刷新页面或重启服务。');
    await FinanceSSE.consume(response.body,(event,data)=>{
      if(event==='connected')progress.textContent=data.message;
      else if(event==='session')session=data.session_id;
      else if(event==='progress')progress.textContent=data.state==='started'?`正在${data.label}…`:`${data.label} ✓`;
      else if(event==='answer_start'){progress.textContent='正在接收已校验的答复…';}
      else if(event==='answer_delta')text.append(document.createTextNode(data.text));
      else if(event==='result'){
        session=data.session_id;resultReceived=true;renderResult(data,box);
        progress.textContent=data.status==='answered'?'查询完成 · 已校验结果':data.choices?.length?'等待你确认':'处理完成';
        progress.classList.add('complete');
      }else if(event==='error'){receivedError=true;throw new Error(data.detail||'本轮处理未完成，请手动重试。');}
      else if(event==='done')complete=true;
    });
    if(!complete)throw new Error('连接提前结束，本轮未确认完成。不会自动重试，请手动重新查询。');
  }catch(error){
    const stopped=error.name==='AbortError';
    progress.textContent=stopped?'已停止接收；已发出的模型请求无法撤回，后续步骤将停止。':error.message;
    progress.classList.add('stream-error');
    if(!resultReceived)priorChoices.forEach(b=>b.disabled=false);
    if(!text.textContent)text.textContent=stopped?'你可以修改问题后重新提交。':'本轮没有可展示的完整结果。';
  }finally{
    text.classList.remove('stream-text');busy=false;activeController=null;
    sendButton.disabled=false;sendButton.textContent='查询 ↗';$('#question').focus();
  }
}
$('#composer').addEventListener('submit',e=>{e.preventDefault();if(busy){activeController?.abort();return;}const q=$('#question').value.trim();if(q){$('#question').value='';send(q);}});
$('#question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#composer').requestSubmit();}});
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>{$('#question').value=b.dataset.question;$('#question').focus();});
$('#new-chat').onclick=()=>{if(busy)return;session=null;$('#messages').replaceChildren();$('#welcome').hidden=false;$('#question').value='';};
refresh();
