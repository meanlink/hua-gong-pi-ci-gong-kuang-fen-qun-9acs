import {jobs,trials} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {TrialStatus,TrialActions} from '../constants/enums.js';

const pad=n=>String(n).padStart(2,'0');
const toLocal=ts=>{const d=new Date(ts*1000);return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+'T'+pad(d.getHours())+':'+pad(d.getMinutes());};
// 浏览器 datetime-local 不带时区；提交前转成带偏移的 ISO，后端按 UTC 落库，
// 避免服务器与浏览器时区不同时显示时间整体漂移。
const asIso=value=>value?new Date(value).toISOString():value;
const FEATURE_LABELS={temperature_c:'反应温度(℃)',pressure_bar:'釜压(bar)',residence_min:'停留时间(min)',yield_pct:'收率(%)'};

// 页面内轻状态：选中单、冲突来源、最近一次操作结果。
// 切换小试单时清掉上一单的冲突卡片，避免把旧来源展示到新单上。
const ui={trialId:null,conflicts:[],message:'',messageError:false};

export async function trialsPage(id){
 const [allTrials,board,allJobs]=await Promise.all([trials.list(),trials.board(),jobs.list()]);
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed');
 const previous=ui.trialId;
 if(id)ui.trialId=Number(id);
 if(!ui.trialId||!allTrials.some(t=>t.id===ui.trialId))ui.trialId=allTrials[0]?.id||null;
 if(ui.trialId!==previous){ui.conflicts=[];ui.message='';ui.messageError=false;}
 const current=ui.trialId?await trials.get(ui.trialId):null;
 const history=current?await trials.history(current.id):[];
 workspace.trials=allTrials;workspace.trialBoard=board;workspace.approvedJobs=approved;
 const canEdit=session.user.role==='admin';
 const reactors=board.reactors;
 return heading('05 / PILOT SCHEDULE','小试单与实验釜排程','从获批分析中选族群创建小试单，创建时固定当时工况。负责人为整单安排多个釜与起止时间；反应结束后清洗时段仍计入占用，相邻时段可无缝衔接。')
  + trialCreator(approved,session.user.role!=='viewer')
  + conflictPanel()
  + '<div class="trial-grid"><section class="panel"><div class="section-title"><h2>小试单</h2><span>'+allTrials.length+' 张 · 占用按确认状态计算</span></div>'
  + '<div class="trial-list">'+(allTrials.length?allTrials.map(t=>'<a class="trial-item '+(t.id===ui.trialId?'active':'')+'" href="#/trials/'+t.id+'"><div><strong>'+esc(t.name)+'</strong><span class="'+(t.status==='cancelled'?'muted':'')+'">'+esc(TrialStatus[t.status]||t.status)+'</span></div><small>'+esc(t.cluster)+' · 来源分析 #'+t.job_id+' · '+esc(t.created_by_name)+'</small></a>').join(''):'<p class="empty">还没有小试单。先从获批分析中选择一个工况族群创建。</p>')+'</div></section>'
  + '<section class="panel">'+detailCard(current,reactors,canEdit)+'</section></div>'
  + scheduleBoard(board)
  + historyPanel(current,history)
  + maintenancePanel(board,canEdit);
}

function trialCreator(approved,canCreate){
 return '<section class="panel trial-creator"><div class="section-title"><h2>从获批分析创建小试单</h2><span>工况在创建时固定，不随分析结果后续变化</span></div>'
  + (approved.length?'<form id="trial-form"><div class="parameter-grid"><label>小试单名称<input name="name" required minlength="2" value="工况复核小试 · 首轮"></label><label>获批分析<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+'</option>').join('')+'</select></label><label>工况族群<select name="cluster" required>'+clusterOptions(approved[0])+'</select></label></div><button type="submit" class="primary" '+(canCreate?'':'disabled')+'>创建小试单 ↗</button><p class="hint">'+(canCreate?'创建后为草稿，不占用任何实验釜；负责人再为整单排程并确认。':'只有业务负责人和分析员可以从获批分析创建小试单。')+'</p></form>':'<p class="empty">目前没有已完成且获批的分析结果。<a href="#/results">前往结果复核 ↗</a></p>')
  + '</section>';
}

function clusterOptions(job){
 return (job?.result?.table?.rows||[]).map(r=>'<option value="'+esc(r.cluster)+'">'+esc(r.cluster)+' · '+r.count+' 批 · 中心收率 '+r.yield_pct+'%</option>').join('');
}

function conflictPanel(){
 if(!ui.conflicts.length&&!ui.message)return '';
 return '<section class="conflict-panel'+(ui.messageError?' error':'')+'">'
  + (ui.message?'<p class="conflict-head">'+esc(conflictHeadline(ui.message,ui.conflicts))+'</p>':'')
  + ui.conflicts.map(c=>'<article class="conflict-card"><div><strong>'+esc(c.conflict_type==='maintenance'?'检修封锁':'小试占用')+' · 实验釜 '+esc(c.reactor_code)+'</strong><p>'+esc(conflictText(c))+'</p></div><dl><div><dt>请求时段（含清洗）</dt><dd>'+esc(clashRange(c.requested))+'</dd></div><div><dt>已被占用</dt><dd>'+esc(clashRange(c.occupied))+'</dd></div><div><dt>重叠</dt><dd>'+esc(clashRange(c.overlap))+'</dd></div><div><dt>来源</dt><dd>'+(c.conflict_type==='maintenance'?'检修 #'+c.source_id+' · '+esc(c.reason):'小试单 #'+c.source_id+' · '+esc(c.source_name))+'</dd></div></dl></article>').join('')
  + '</section>';
}

// 后端 message 用服务器本地时间；页面统一按浏览器时区重排，避免跨时区展示不一致。
const clashRange=r=>date(r.start_at)+' – '+date(r.end_at);
function conflictText(c){
 return '实验釜 '+c.reactor_code+' 在 '+clashRange(c.occupied)+(c.conflict_type==='maintenance'
   ?' 处于检修封锁「'+(c.reason||'未填写原因')+'」'
   :' 已被小试单 #'+c.source_id+'「'+c.source_name+'」占用（含清洗时段）');
}
// 冲突明细已按卡片展示，头部只保留后端给出的一句结论，不重复服务器时区时间。
function conflictHeadline(message,conflicts){
 if(!conflicts.length)return message;
 const period=message.indexOf('。');
 return (period>=0?message.slice(0,period+1):message)+' 共 '+conflicts.length+' 处冲突，详见下方来源。';
}

function detailCard(trial,reactors,canEdit){
 if(!trial)return '<div class="section-title"><h2>小试单详情</h2></div><p class="empty">选择或创建一张小试单后，在这里整单排程、确认、开试或取消。</p>';
 const conditions=Object.entries(FEATURE_LABELS).map(([key,label])=>'<div><span>'+esc(label)+'</span><strong>'+esc(trial.frozen_conditions[key]??trial.conditions[key]??'—')+'</strong></div>').join('');
 const editable=trial.editable&&canEdit;
 return '<div class="section-title"><div><h2>'+esc(trial.name)+'</h2><p class="hint">工况族群 '+esc(trial.cluster)+' · 来源分析 #'+trial.job_id+'「'+esc(trial.context.job_name||'')+'」 · 数据批次 '+esc(trial.context.dataset_name||'—')+'</p></div><span class="trial-state '+(trial.status)+'">'+esc(TrialStatus[trial.status]||trial.status)+'</span></div>'
  + '<div class="frozen-conditions"><p class="eyebrow">创建时固定的工况中心</p><div class="metrics compact-metrics">'+conditions+'</div><small>结果指纹 '+esc((trial.context.result_checksum||'').slice(0,24))+'… · 数据集指纹 '+esc((trial.context.dataset_checksum||'').slice(0,24))+'…</small></div>'
  + (trial.frozen_at?'<p class="hint">已于 '+esc(date(trial.frozen_at))+' 开试冻结，工况与排程不再接受修改。</p>':'')
  + scheduleEditor(trial,reactors,editable)
  + actionBar(trial,canEdit)
  + (trial.warnings?.length?'<div class="warning">当前排程含 '+trial.warnings.length+' 处冲突，确认时会被整单拦截：'+esc(trial.warnings.map(conflictText).join('；'))+'</div>':'');
}

function scheduleEditor(trial,reactors,editable){
 const released=releaseSummary(trial);
 if(!trial.schedule.length&&!editable){
   return '<div class="section-title"><h2>整单排程</h2><span>无占用中的排程段</span></div>'
     + '<p class="hint">'+(trial.status==='cancelled'?'该单已取消，创建时未开试，占用已全部释放。':'该单尚无排程。')+'</p>' + released;
 }
 const rows=(trial.schedule.length?trial.schedule:[{reactor_id:reactors[0]?.id,start_at:null,end_at:null,cleaning_minutes:reactors[0]?.cleaning_minutes||0}]);
 const defaultStart=toLocal(Math.ceil(Date.now()/3600000)*3600+3600);
 const defaultEnd=toLocal(Math.ceil(Date.now()/3600000)*3600+3600+3*3600);
 const mark=frozen=>' <span class="muted">'+(frozen?'（已冻结）':'')+'</span>';
 return '<form id="schedule-form" class="schedule-form"><div class="section-title"><h2>整单排程'+(trial.status==='started'?mark(true):'')+'</h2><span>一段排程占用 [开始, 结束 + 该釜清洗]；整单提交</span></div>'
  + '<div class="slot-rows">'+rows.map((s,i)=>'<div class="slot-row" data-slot><span class="slot-index">'+(i+1)+'</span>'
    +'<label>实验釜<select data-field="reactor_id" '+(editable?'':'disabled')+'>'+reactors.filter(r=>r.active||r.id===s.reactor_id).map(r=>'<option value="'+r.id+'" '+(r.id===s.reactor_id?'selected':'')+'>'+esc(r.code)+' · '+esc(r.name)+'（清洗 '+r.cleaning_minutes+' 分）</option>').join('')+'</select></label>'
    +'<label>开始<input type="datetime-local" data-field="start_at" value="'+(s.start_at?toLocal(s.start_at):defaultStart)+'" '+(editable?'':'disabled')+'></label>'
    +'<label>结束<input type="datetime-local" data-field="end_at" value="'+(s.end_at?toLocal(s.end_at):defaultEnd)+'" '+(editable?'':'disabled')+'></label>'
    +'<button type="button" class="text-button" data-remove '+(editable?'':'disabled')+'>移除</button>'
    +'</div>').join('')+'</div>'
  + (editable?'<div class="slot-actions"><button type="button" id="add-slot">＋ 增加一段</button><button type="button" id="check-slot">检查空位</button><button type="submit" class="primary">'+(trial.status==='confirmed'?'整单改期':'保存整单排程')+' ↗</button></div>':'')
  + released
  + '</form>';
}

function releaseSummary(trial){
 if(!trial.slots.some(s=>s.released_at))return '';
 return '<details class="released"><summary>历史排程段（已释放）</summary>'+trial.slots.filter(s=>s.released_at).map(s=>'<p>'+esc(s.reactor_code)+' · '+esc(date(s.start_at))+' → '+esc(date(s.occupied_until))+'（含清洗 '+s.cleaning_minutes+' 分）· 释放于 '+esc(date(s.released_at))+'</p>').join('')+'</details>';
}

function actionBar(trial,canEdit){
 const disabled=canEdit?'':'disabled';
 return '<div class="trial-actions">'
  + (trial.can_confirm?'<button class="primary" data-action="confirm" '+disabled+'>确认占用整单</button>':'')
  + (trial.can_start?'<button data-action="start" '+disabled+'>开试并冻结</button>':'')
  + (trial.can_cancel?'<input id="cancel-reason" class="inline-input" maxlength="300" placeholder="取消原因（写入变更历史）"><button data-action="cancel" '+disabled+'>取消小试单</button>':'')
  + '<span class="hint">确认时全单所有段都有空位才占用；任一段冲突则整单不占用。开试后不可再改期或取消。</span></div>';
}

function scheduleBoard(board){
 const occ=board.occupancies;
 const rows=board.reactors.map(r=>{
   const items=occ.filter(o=>o.reactor_id===r.id);
   const windows=board.maintenance.filter(m=>m.reactor_id===r.id);
   const timeline=items.sort((a,b)=>a.start_at-b.start_at).map(o=>'<div class="lane-block '+(o.trial_status==='started'?'started':'')+'"><strong>'+esc(o.trial_name)+'</strong><span>'+esc(date(o.start_at))+' → '+esc(date(o.end_at))+'</span><small>清洗至 '+esc(date(o.occupied_until))+' · '+esc(TrialStatus[o.trial_status]||o.trial_status)+'</small></div>').join('')
     + windows.map(m=>'<div class="lane-block maintenance"><strong>检修 · '+esc(m.reason)+'</strong><span>'+esc(date(m.start_at))+' → '+esc(date(m.end_at))+'</span><small>'+esc(m.created_by_name)+'</small></div>').join('');
   return '<div class="lane"><div class="lane-head"><strong>'+esc(r.code)+'</strong><span>'+esc(r.name)+'</span><small>清洗 '+r.cleaning_minutes+' 分</small></div><div class="lane-body">'+(timeline||'<span class="empty-line">未来 30 天暂无占用</span>')+'</div></div>';
 }).join('');
 return '<section class="panel"><div class="section-title"><h2>实验釜占用看板</h2><span>近 7 天至未来 30 天 · 含清洗与检修封锁</span></div><div class="board">'+rows+'</div><p class="hint">已确认与已开试的单才显示为占用；草稿与已取消的单不占釜。相邻两段的衔接以清洗结束时刻为准。</p></section>';
}

function historyPanel(trial,history){
 if(!trial)return '';
 return '<section class="panel"><div class="section-title"><h2>变更历史</h2><span>'+history.length+' 条 · 冲突拒绝同样留痕</span></div><ol class="trial-history">'+history.map(e=>'<li><strong>'+esc(TrialActions[e.action]||e.action)+'</strong><span>'+esc(e.actor)+' · '+esc(date(e.at))+'</span>'+(summary(e.detail)?'<p>'+esc(summary(e.detail))+'</p>':'')+'</li>').join('')+'</ol></section>';
}

function summary(detail){
 if(!detail)return '';
 if(detail.conflicts?.length)return detail.conflicts.map(conflictText).join('；');
 if(detail.reason)return '原因：'+detail.reason;
 if(detail.note)return '备注：'+detail.note;
 if(detail.released_slots!=null)return '释放 '+detail.released_slots+' 段未开试占用';
 if(detail.segments!=null)return detail.segments+' 段整单提交';
 if(detail.cluster)return '族群 '+detail.cluster;
 return '';
}

function maintenancePanel(board,canEdit){
 const active=board.maintenance;
 return '<section class="panel"><div class="section-title"><h2>检修封锁</h2><span>封锁时段拒绝任何新占用，也不覆盖已有占用</span></div>'
  + (canEdit?'<form id="maintenance-form"><div class="parameter-grid"><label>实验釜<select name="reactor_id">'+board.reactors.map(r=>'<option value="'+r.id+'">'+esc(r.code)+' · '+esc(r.name)+'</option>').join('')+'</select></label><label>封锁原因<input name="reason" required minlength="2" value="例行检修"></label><label>开始<input type="datetime-local" name="start_at" required></label><label>结束<input type="datetime-local" name="end_at" required></label></div><button type="submit">建立检修封锁</button><p class="hint">仅业务负责人可建立或解除检修封锁。</p></form>':'<p class="hint">只读角色可查看检修封锁，不能建立或解除。</p>')
  + (active.length?'<div class="maintenance-list">'+active.map(m=>'<div><strong>'+esc(m.reactor_code)+'</strong><span>'+esc(date(m.start_at))+' → '+esc(date(m.end_at))+'</span><p>'+esc(m.reason)+' · '+esc(m.created_by_name)+'</p>'+(canEdit?'<button class="text-button" data-release="'+m.id+'">解除封锁</button>':'')+'</div>').join('')+'</div>':'<p class="empty">当前没有生效的检修封锁。</p>')+'</section>';
}

export function bindTrials(refresh){
 const creator=document.querySelector('#trial-form');
 if(creator){
   const jobSelect=creator.querySelector('[name=job_id]');
   const clusterSelect=creator.querySelector('[name=cluster]');
   const approved=(workspace.approvedJobs||[]);
   jobSelect.onchange=()=>{
     const job=approved.find(j=>String(j.id)===jobSelect.value);
     clusterSelect.innerHTML=clusterOptions(job);
   };
   creator.addEventListener('submit',async e=>{
     e.preventDefault();
     const data=new FormData(creator);
     try{
       const t=await trials.create({name:data.get('name'),job_id:Number(data.get('job_id')),cluster:data.get('cluster')});
       ui.trialId=t.id;ui.conflicts=[];ui.message='小试单已创建，工况已固定；请为整单安排实验釜和时间。';ui.messageError=false;
       notify('小试单已创建，可开始整单排程');
       location.hash='#/trials/'+t.id;refresh();
     }catch(error){ui.conflicts=error.conflicts||[];ui.message=error.message;ui.messageError=true;notify(error.message,true);refresh();}
   });
 }
 const form=document.querySelector('#schedule-form');
 if(form){
   const collect=()=>Array.from(form.querySelectorAll('[data-slot]')).map(row=>({
     reactor_id:Number(row.querySelector('[data-field=reactor_id]').value),
     start_at:asIso(row.querySelector('[data-field=start_at]').value),
     end_at:asIso(row.querySelector('[data-field=end_at]').value),
   }));
   const add=document.querySelector('#add-slot');
   if(add)add.onclick=()=>{
     const list=form.querySelector('.slot-rows'),template=list.lastElementChild;
     const clone=template.cloneNode(true);
     // 新段默认从上一段结束时刻开始、换下一个釜，负责人再微调；
     // 若沿用上一段会造成同釜自重叠，保存时会被后端整单拦截。
     const prevEnd=template.querySelector('[data-field=end_at]').value;
     const startField=clone.querySelector('[data-field=start_at]');
     const endField=clone.querySelector('[data-field=end_at]');
     if(prevEnd){
       startField.value=prevEnd;
       const start=new Date(prevEnd);
       endField.value=toLocal((start.getTime()+3*3600000)/1000);
     }
     const reactorField=clone.querySelector('[data-field=reactor_id]');
     const options=Array.from(reactorField.options);
     const used=reactorField.selectedIndex;
     if(options.length>1)reactorField.selectedIndex=(used+1)%options.length;
     list.appendChild(clone);bindRemove(form);
   };
   bindRemove(form);
   const checkButton=document.querySelector('#check-slot');
   if(checkButton)checkButton.onclick=async()=>{
     try{
       const result=await trials.check(ui.trialId,{slots:collect()});
       ui.conflicts=result.conflicts;ui.message=result.ok?'所选时段整单无冲突，可以确认占用。':'所选时段存在冲突，确认时会被整单拦截。';ui.messageError=!result.ok;
       notify(ui.message,!result.ok);refresh();
     }catch(error){ui.message=error.message;ui.messageError=true;notify(error.message,true);refresh();}
   };
   form.addEventListener('submit',async e=>{
     e.preventDefault();
     try{
       const result=await trials.schedule(ui.trialId,{slots:collect()});
       ui.conflicts=[];
       ui.message=result.changed?('整单排程已保存，共 '+result.schedule.length+' 段。'):'排程与当前一致，无需变更。';
       ui.messageError=false;notify('排程已保存');refresh();
     }catch(error){
       ui.conflicts=error.conflicts||[];ui.message=error.message;ui.messageError=true;notify(error.message,true);refresh();
     }
   });
 }
 document.querySelectorAll('[data-action]').forEach(button=>button.onclick=async()=>{
   const action=button.dataset.action;
   try{
     if(action==='confirm'){
       const result=await trials.confirm(ui.trialId);
       ui.message=result.replayed?'该单此前已确认，未重复占用。':'整单已确认占用，所有时段均已锁定。';
     }
     if(action==='start'){
       const result=await trials.start(ui.trialId);
       ui.message=result.replayed?'该单此前已开试，工况与排程保持冻结。':'已开试，工况与排程冻结；仍占用至结束加清洗。';
     }
     if(action==='cancel'){
       const reason=document.querySelector('#cancel-reason')?.value||'';
       const result=await trials.cancel(ui.trialId,reason);
       ui.message=result.replayed?'该单此前已取消，未重复释放占用。':'已取消，未开试的占用已释放。';
     }
     ui.conflicts=[];ui.messageError=false;notify(ui.message);refresh();
   }catch(error){ui.conflicts=error.conflicts||[];ui.message=error.message;ui.messageError=true;notify(error.message,true);refresh();}
 });
 const maintenance=document.querySelector('#maintenance-form');
 if(maintenance){
   const start=maintenance.querySelector('[name=start_at]');
   const base=new Date(Math.ceil(Date.now()/3600000)*3600000+86400000);
   start.value=toLocal(base.getTime()/1000);
   maintenance.querySelector('[name=end_at]').value=toLocal(base.getTime()/1000+4*3600);
   maintenance.addEventListener('submit',async e=>{
     e.preventDefault();
     const data=new FormData(maintenance);
     try{
       await trials.createMaintenance({reactor_id:Number(data.get('reactor_id')),reason:data.get('reason'),start_at:asIso(data.get('start_at')),end_at:asIso(data.get('end_at'))});
       ui.conflicts=[];ui.message='检修封锁已建立。';ui.messageError=false;notify('检修封锁已建立');refresh();
     }catch(error){ui.conflicts=error.conflicts||[];ui.message=error.message;ui.messageError=true;notify(error.message,true);refresh();}
   });
 }
 document.querySelectorAll('[data-release]').forEach(button=>button.onclick=async()=>{
   try{await trials.releaseMaintenance(button.dataset.release);ui.message='检修封锁已解除。';notify('检修封锁已解除');refresh();}
   catch(error){notify(error.message,true);}
 });
}

function bindRemove(form){
 form.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{
   if(form.querySelectorAll('[data-slot]').length>1)button.closest('[data-slot]').remove();
   else notify('整单至少保留一段排程',true);
 });
}
