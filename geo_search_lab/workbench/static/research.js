"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const root = "/api/v1/research";
  let study = null, current = null;
  const statuses = {pending_output:"等待输出", pending_review:"待评阅", reviewed:"已评阅", failed:"运行失败", unassessable:"不可评"};
  const coverages = {complete:"完整覆盖", partial:"部分覆盖", missing:"缺失", uncertain:"不确定"};
  const conflicts = {absent:"无冲突", present:"有冲突", uncertain:"不确定"};
  function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
  function notice(message) { const n=$("research-notice"); n.textContent=message; n.hidden=false; n.focus(); }
  async function api(path, method="GET", body) {
    const r=await fetch(root+path,{method,headers:{"Content-Type":"application/json","X-Lab-Request":"1"},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await r.json();
    if(!r.ok) throw new Error(data.error?.message || (Array.isArray(data.detail)?data.detail.map(x=>x.msg).join("；"):data.detail) || "请求失败");
    return data;
  }
  function guarded(fn){return async event=>{try{$("research-notice").hidden=true;await fn(event);}catch(e){notice(e.message);}};}
  function base(){return `/studies/${encodeURIComponent(study.study_id)}`;}
  async function load(prefer){
    const data=await api("/studies"); $("studies").replaceChildren();
    if(!data.items.length){$("studies").append(new Option("尚无研究", ""));return;}
    for(const s of data.items) $("studies").append(new Option(`${s.study_id} · ${s.data_kind==="fixture"?"模拟":"导入"}`,s.study_id));
    $("studies").value=prefer && data.items.some(s=>s.study_id===prefer)?prefer:data.items[0].study_id;
    await selectStudy();
  }
  async function selectStudy(){
    study=await api(`/studies/${encodeURIComponent($("studies").value)}`); current=null;
    $("research-detail").replaceChildren(el("h2","选择一条样本"));renderStudy();
  }
  function renderStudy(){
    const c=study.counts;
    $("research-progress").textContent=`${study.data_kind==="fixture"?"模拟数据：不代表模型表现。":"导入数据：单人参考。"} 已评 ${c.reviewed}/36；待输出 ${c.pending_output}，待评 ${c.pending_review}，失败 ${c.failed}，不可评 ${c.unassessable}。24 个审计样本封存。`;
    $("research-list").replaceChildren();
    study.slots.forEach((s,i)=>{const b=el("button",`样本 ${String(i+1).padStart(2,"0")} · ${statuses[s.status]}`,"research-item");b.type="button";b.setAttribute("aria-current",String(current?.slot_id===s.slot_id));b.onclick=guarded(()=>selectSlot(s.slot_id));$("research-list").append(b);});
    $("analyze").disabled=!study.analysis_available;
    $("export-links").hidden=!study.analysis_available;$("export-links").replaceChildren();
    $("research-analysis").replaceChildren();
    if(study.analysis_available) for(const [format,label] of [["json","下载完整 JSON"],["csv","下载评分 CSV"]]){const a=el("a",label);a.href=root+base()+`/export/${format}`;a.download="";$("export-links").append(a);}
  }
  function selectInput(id, label, values, chosen){
    const wrap=el("label",label), input=el("select");input.id=id;input.required=true;
    input.append(new Option("请选择", ""));for(const [v,t]of Object.entries(values))input.append(new Option(t,v));
    input.value=chosen||"";wrap.append(input);return [wrap,input];
  }
  async function selectSlot(id){
    current=await api(base()+`/slots/${encodeURIComponent(id)}`);renderStudy();
    const detail=$("research-detail"); detail.replaceChildren(el("h2","原始问题"),el("p",current.prompt));
    detail.append(el("p",`评分规则：${current.rubric_version} · 时间参照：${current.reference_at}`,"muted"));
    if(current.status!=="succeeded"){
      detail.append(el("p",current.status==="pending_output"?"尚未导入输出。":"此条输出失败或不可评，不计零分。","hint"));
      if(current.status==="pending_output"){
        const label=el("label","导入此槽位的输出 JSON"),file=el("input");file.type="file";file.accept=".json";
        file.onchange=guarded(async()=>{const value=await readFile(file);await api(base()+`/slots/${id}/output`,"PUT",value);study=await api(base());await selectSlot(id);});label.append(file);detail.append(label);
      }return;
    }
    detail.append(el("h3",current.data_kind==="fixture"?"合成搜索 query（模拟）":"模型生成的搜索 query"));const qs=el("ol",undefined,"queries");current.queries.forEach(q=>qs.append(el("li",q)));detail.append(qs);
    const form=el("form"),prev=current.reviews.at(-1),reviewerLabel=el("label","评审代号（不需要真实姓名）"),reviewer=el("input");
    reviewer.id="reviewer";reviewer.required=true;reviewer.pattern="[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}";reviewer.maxLength=80;reviewer.value=prev?.reviewer_id||"reviewer-1";reviewerLabel.append(reviewer);form.append(reviewerLabel);
    const fields=[];
    for(const item of current.rubric){
      const old=prev?.labels.find(x=>x.item_id===item.item_id), box=el("fieldset",undefined,"rubric-row");
      box.append(el("legend",`${item.kind==="need"?"信息需求":"约束"} ${item.item_id}`),el("p",item.text));
      const pair=el("div",undefined,"field-pair"),[cl,ci]=selectInput(`coverage-${item.item_id}`,"覆盖情况",coverages,old?.coverage),[fl,fi]=selectInput(`conflict-${item.item_id}`,"是否冲突",conflicts,old?.conflict);pair.append(cl,fl);box.append(pair);
      const evLabel=el("label","证据来自哪个 query"),ev=el("select");ev.id=`evidence-${item.item_id}`;ev.append(new Option("无证据（缺失或不确定）","0"));current.queries.forEach((q,i)=>ev.append(new Option(`Query ${i+1}`,String(i+1))));ev.value=String(old?.evidence[0]?.query_index||0);evLabel.append(ev);
      const quoteLabel=el("label","逐字摘录的证据"),quote=el("textarea");quote.rows=2;quote.id=`quote-${item.item_id}`;quote.value=old?.evidence[0]?.quote||"";quoteLabel.append(quote);
      const noteLabel=el("label","判断说明（可选）"),note=el("textarea");note.rows=2;note.maxLength=2000;note.value=old?.note||"";noteLabel.append(note);
      const err=el("p","","field-error");err.id=`error-${item.item_id}`;quote.setAttribute("aria-describedby",err.id);ev.setAttribute("aria-describedby",err.id);
      const extraEvidence=old?.evidence.slice(1)||[];
      if(extraEvidence.length)box.append(el("p","此记录还有额外证据，保存时会保留；可在下方评分历史查看。","muted"));
      box.append(evLabel,quoteLabel,noteLabel,err);form.append(box);fields.push({item,ci,fi,ev,quote,note,err,extraEvidence});
    }
    const actions=el("div",undefined,"review-actions"),save=el("button",prev?"保存新的评分版本":"保存初评","primary");save.type="submit";actions.append(save,el("span","初评与修改历史都会保留。","muted"));form.append(actions);
    form.onsubmit=guarded(async e=>{
      e.preventDefault();let invalid=false;
      const labels=fields.map(f=>{f.err.textContent="";f.quote.removeAttribute("aria-invalid");const idx=Number(f.ev.value),text=f.quote.value;
        const required=["complete","partial"].includes(f.ci.value)||f.fi.value==="present";
        if((required&&!idx)||(idx&&(!text.trim()||!current.queries[idx-1].includes(text)))||(!idx&&text.trim())){f.err.textContent="请选择证据 query，并逐字摘录；没有证据时请留空。";f.quote.setAttribute("aria-invalid","true");invalid=true;}
        return {item_id:f.item.item_id,coverage:f.ci.value,conflict:f.fi.value,evidence:[...(idx?[{query_index:idx,quote:text}]:[]),...f.extraEvidence],note:f.note.value};});
      if(invalid){notice("评分尚未保存，请检查证据字段。");const link=el("a","跳到第一处错误");link.href="#"+fields.find(f=>f.err.textContent).quote.id;$("research-notice").append(document.createTextNode(" "),link);return;}
      save.disabled=true;try{await api(base()+`/slots/${id}/reviews`,"POST",{expected_revision:current.reviews.length,reviewer_id:reviewer.value,labels});study=await api(base());await selectSlot(id);$("research-progress").textContent+=" 评分已保存。";}finally{save.disabled=false;}
    });detail.append(form);
    if(current.reviews.length){const history=el("details",undefined,"review-history");history.append(el("summary",`查看 ${current.reviews.length} 个人工评分版本`),el("pre",JSON.stringify(current.reviews,null,2),"research-json"));detail.append(history);}
    if(study.analysis_available){
      const full=el("button","查看此条完整记录与裁判尝试");full.type="button";
      full.onclick=guarded(async()=>{const record=await api(base()+`/slots/${id}/result`),container=el("details");container.open=true;container.append(el("summary","此条结果（包括初评、修改历史和首次/诊断裁判记录）"),el("pre",JSON.stringify(record,null,2),"research-json"));detail.append(container);full.disabled=true;});detail.append(full);
    }
  }
  async function readFile(input){const file=input.files[0];if(!file)throw new Error("请选择 JSON 文件");if(file.size>2000000)throw new Error("文件不能超过 2 MB");return JSON.parse(await file.text());}
  $("studies").onchange=guarded(selectStudy);$("refresh").onclick=guarded(()=>load(study?.study_id));
  $("create-demo").onclick=guarded(async()=>{const b=$("create-demo");b.disabled=true;try{const id="demo-"+crypto.randomUUID();await api("/demo","POST",{study_id:id,seed:20261002});await load(id);}finally{b.disabled=false;}});
  $("import-study").onchange=guarded(async()=>{const value=await readFile($("import-study"));const s=await api("/studies","POST",value);await load(s.study_id);});
  $("analyze").onclick=guarded(async()=>{const data=await api(base()+"/analysis"),target=$("research-analysis");target.replaceChildren(el("p",data.limitations.join("；"),"hint"));
    const wrap=el("div",undefined,"research-table-wrap"),table=el("table",undefined,"research-table"),head=el("tr");["配置组","人工已评家族","完整覆盖上下界","有效人机对照家族"].forEach(v=>head.append(el("th",v)));table.append(head);
    for(const c of data.cohorts){const row=el("tr"),s=c.scores.need,b=s.human_mean_bounds;[`${c.arm} · ${c.profile_id}`,s.human_families,b?`${(b.lower*100).toFixed(1)}% – ${(b.upper*100).toFixed(1)}%`:"无",s.paired_grade_families].forEach(v=>row.append(el("td",v)));table.append(row);}wrap.append(table);target.append(wrap);
    const details=el("details");details.append(el("summary","查看完整统计与失败记录"),el("pre",JSON.stringify(data,null,2),"research-json"));target.append(details);
  });
  load().catch(e=>notice(e.message));
})();
