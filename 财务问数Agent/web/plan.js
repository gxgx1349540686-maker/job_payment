(() => {
 const find=id=>document.getElementById(id);
 let buildings=[],loading=false;
 function node(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
 async function json(url,options){const r=await fetch(url,options);const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'输入无效，请检查月份及楼栋。');return d;}
 function options(){const s=find('plan-building'),q=find('plan-search').value.trim();s.replaceChildren();s.append(new Option('请选择已登记楼栋',''));buildings.filter(b=>!q||(b.project+' '+b.building).includes(q)).forEach(b=>s.append(new Option(b.project+' / '+b.building,b.building_id)));}
 function render(d){
  const box=find('plan-result');box.replaceChildren();
  box.append(node('h3',d.scope.project+' / '+d.scope.building),node('p',d.stale?'旧任务已失效，请重新核对。':d.answer));
  box.append(node('p','支付月份：'+d.input.payment_month+'；结算核对月份：'+d.input.settlement_month));
  box.append(node('p','申请金额：未知；人工核验：未完成。'));
  if(d.description_draft)box.append(node('h4','说明草稿'),node('pre',d.description_draft));
  const issues=node('ul','');d.issues.forEach(t=>issues.append(node('li',t)));box.append(issues);
  box.append(node('h4','历史计划'));
  d.plans.forEach(p=>box.append(node('p',p.month_id+' · '+p.payment_plan+' · '+p.plan_id)));
  if(!d.plans.length)box.append(node('p','未查到历史计划，不代表无需支付。'));
  box.append(node('h4','合同候选（不自动认定当前执行合同）'));
  d.contract_candidates.forEach(c=>{
   const part=node('div','');part.append(node('p',[c.contract_name,c.contract_type,c.payee_name||'收款人未登记',c.bank_name||'银行未登记',c.account_masked].join(' · ')),node('p','合同ID：'+c.contract_id+'；起止：'+c.contract_start_date+'—'+c.contract_end_date));
   const choose=node('button','使用此合同重新核对');choose.type='button';choose.onclick=()=>{find('plan-contract').value=c.contract_id;find('plan-message').textContent='已填入合同ID，请确认采用依据后点击开始核对。';};part.append(choose);box.append(part);
  });
  const evidence=node('details','');evidence.append(node('summary','来源与执行记录'),node('pre',JSON.stringify({skill:d.skill,source_refs:d.source_refs,steps:d.steps,failed_documents:d.failed_documents},null,2)));box.append(evidence);
  box.append(node('p','已保存核对任务：'+d.task_id+'。此记录不是付款批准。'));
  try{localStorage.setItem('finance-last-plan-task',d.task_id);}catch(e){box.append(node('p','浏览器未允许保存任务编号，请保留上方编号。'));}
 }
 async function open(){
  document.body.classList.add('plan-mode');find('plan-panel').hidden=false;find('welcome').hidden=true;find('feature-menu').open=false;
  find('plan-message').textContent='正在加载已登记楼栋…';
  try{const d=await json('/api/plans/options');buildings=d.buildings;options();find('plan-message').textContent=d.skill.mode;}
  catch(e){find('plan-message').textContent=e.message;}
  find('plan-panel').scrollIntoView({behavior:'smooth',block:'start'});
 }
 find('plan-feature').onclick=open;find('plan-suggestion').onclick=open;
 window.addEventListener('open-payment-plan',open);
 find('plan-search').addEventListener('input',options);
 find('plan-building').addEventListener('change',()=>{find('plan-contract').value='';find('plan-result').replaceChildren();});
 find('new-chat').addEventListener('click',()=>{if(!loading){document.body.classList.remove('plan-mode');find('plan-panel').hidden=true;find('plan-result').replaceChildren();}});
 find('plan-close').onclick=()=>{document.body.classList.remove('plan-mode');find('plan-panel').hidden=true;if(!find('messages').children.length)find('welcome').hidden=false;};
 find('plan-form').onsubmit=async e=>{
  e.preventDefault();if(loading)return;loading=true;find('plan-submit').disabled=true;find('plan-message').textContent='正在执行本地只读核对…';
  try{const d=await json('/api/plans/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({building_id:find('plan-building').value,payment_month:find('plan-payment-month').value,settlement_month:find('plan-settlement-month').value,settlement_kind:'tail',contract_id:find('plan-contract').value.trim()})});render(d);find('plan-message').textContent='核对已保存，仍需人工复核。';}
  catch(e){find('plan-result').replaceChildren();find('plan-message').textContent=e.message;}
  finally{loading=false;find('plan-submit').disabled=false;}
 };
 const restore=node('button','恢复上次核对记录');restore.type='button';restore.onclick=async()=>{try{const id=localStorage.getItem('finance-last-plan-task');if(!/^[a-f0-9]{32}$/.test(id||'')){find('plan-message').textContent='此浏览器暂无已保存任务。';return;}render(await json('/api/plans/tasks/'+id));}catch(e){find('plan-message').textContent=e.message;}};find('plan-form').append(restore);
})();
