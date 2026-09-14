(()=>{
  const $=(q,root=document)=>root.querySelector(q); const $$=(q,root=document)=>[...root.querySelectorAll(q)];
  const menu=$('#menuButton'); if(menu) menu.addEventListener('click',()=>$('.sidebar')?.classList.toggle('open'));
  const search=$('#liveSearch'); if(search) search.addEventListener('input',()=>{const q=search.value.toLowerCase();$$('.channel-card').forEach(el=>el.hidden=!el.dataset.name.includes(q));});
  function setScheduleFields(){const kind=$('#scheduleKind');if(!kind)return;const weekly=$('#weekdayPicker'),once=$('#runDateField');if(weekly)weekly.style.display=kind.value==='weekly'?'flex':'none';if(once)once.style.display=kind.value==='once'?'grid':'none';}
  $('#scheduleKind')?.addEventListener('change',setScheduleFields); setScheduleFields();

  async function refreshSchedulePlatforms(){
    const form=$('#scheduleForm');
    const channel=form?.querySelector('select[name="channel_id"]');
    const picker=form?.querySelector('.platform-picker');
    if(!form||!channel||!picker||!channel.value)return;
    const token=String(Date.now())+Math.random();
    picker.dataset.loadToken=token;
    picker.setAttribute('aria-busy','true');
    try{
      const r=await fetch(`/schedules/new?channel=${encodeURIComponent(channel.value)}`,{cache:'no-store'});
      if(!r.ok)throw new Error(`HTTP ${r.status}`);
      const html=await r.text();
      const doc=new DOMParser().parseFromString(html,'text/html');
      const next=doc.querySelector('#scheduleForm .platform-picker');
      if(!next)throw new Error('Lista de plataformas não encontrada.');
      if(picker.dataset.loadToken!==token)return;
      picker.innerHTML=next.innerHTML;
    }catch(e){
      console.error('HostStorm: falha atualizando plataformas da agenda',e);
    }finally{
      if(picker.dataset.loadToken===token){
        picker.removeAttribute('aria-busy');
        delete picker.dataset.loadToken;
      }
    }
  }
  $('#scheduleForm select[name="channel_id"]')?.addEventListener('change',refreshSchedulePlatforms);

  $$('[data-preflight]').forEach(btn=>btn.addEventListener('click',async()=>{
    btn.disabled=true;const old=btn.textContent;btn.textContent='Testando...';
    try{
      const r=await fetch('/api/preflight/'+btn.dataset.preflight,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const d=await r.json();const box=$('#preflightResult');
      if(box)box.innerHTML=`<div class="preflight ${d.ok?'good':'bad'}"><strong>${d.ok?'✓ Configuração pronta':'⚠ Há pendências'}</strong><div class="preflight-grid">${(d.checks||[]).map(c=>`<span class="${c.ok?'ok':'fail'}">${c.ok?'✓':'✕'} ${c.name}: ${c.message}</span>`).join('')}</div></div>`;
    }catch(e){alert(e)}finally{btn.disabled=false;btn.textContent=old;}
  }));

  $$('.media-hash').forEach(btn=>btn.addEventListener('click',async()=>{
    const old=btn.textContent;btn.textContent='Calculando...';
    try{
      const r=await fetch(`/library/${encodeURIComponent(btn.dataset.kind)}/${encodeURIComponent(btn.dataset.file)}/meta?hash=1`);
      const d=await r.json();prompt('SHA-256',d.sha256||d.error||'');
    }finally{btn.textContent=old;}
  }));

  const liveMatch=location.pathname.match(/^\/lives\/([^/]+)\/?$/);
  const currentLiveId=liveMatch?decodeURIComponent(liveMatch[1]):'';
  let catalogPayload=null;
  let lastStatusPayload=null;

  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmtTime=seconds=>{seconds=Math.max(0,Math.floor(Number(seconds)||0));const h=Math.floor(seconds/3600),m=Math.floor((seconds%3600)/60),s=seconds%60;return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`};
  const post=async(url,data=null)=>{const opt={method:'POST',headers:{'X-Requested-With':'HostStorm'}};if(data){opt.body=data}const r=await fetch(url,opt);if(!r.ok)throw new Error(`HTTP ${r.status}`);return r;};

  async function loadOutputCatalog(force=false){
    if(catalogPayload&&!force)return catalogPayload;
    try{const r=await fetch('/api/output-labels',{cache:'no-store'});if(r.ok)catalogPayload=await r.json();}catch(_){ }
    return catalogPayload||{channels:{},settings:{}};
  }

  function injectMultiOutputStyles(){
    if($('#hsMultiOutputStyles'))return;
    const style=document.createElement('style');style.id='hsMultiOutputStyles';style.textContent=`
      .hs-output-toolbar{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin:0 0 16px;padding:13px;border:1px dashed var(--border,#253047);border-radius:12px;background:rgba(82,127,255,.035)}
      .hs-output-toolbar label{min-width:180px;flex:1}.hs-output-toolbar .btn{margin-bottom:1px}.hs-output-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px;align-items:center}.hs-output-actions .btn{padding:7px 10px;font-size:.78rem}.hs-output-state{font-size:.72rem;border:1px solid var(--border,#253047);border-radius:999px;padding:4px 8px;opacity:.72}.hs-output-state.live{border-color:#2fbf71;color:#67e39c;opacity:1}.hs-output-state.reconnect{border-color:#e8a53a;color:#f5c76f;opacity:1}
      .hs-rerun-box{margin-top:16px;padding:14px;border:1px solid var(--border,#253047);border-radius:12px;background:rgba(255,255,255,.025)}.hs-rerun-box h3{margin:0 0 5px}.hs-rerun-grid{display:grid;grid-template-columns:minmax(180px,1fr) minmax(180px,1fr);gap:12px;align-items:end}.hs-rerun-help{opacity:.67;font-size:.8rem;margin:6px 0 0}
      .hs-schedule-progress{margin-top:10px}.hs-schedule-progress-head{display:flex;justify-content:space-between;gap:12px;font-size:.76rem;margin-bottom:5px}.hs-schedule-progress-head strong{color:#67e39c}.hs-progress-track{height:7px;border-radius:999px;background:rgba(255,255,255,.09);overflow:hidden}.hs-progress-fill{height:100%;background:linear-gradient(90deg,#527fff,#36d78c);border-radius:inherit;transition:width .8s linear}.hs-progress-extra{font-size:.72rem;opacity:.62;margin-top:4px}
      @media(max-width:650px){.hs-rerun-grid{grid-template-columns:1fr}.hs-output-toolbar label{min-width:100%}}
    `;document.head.appendChild(style);
  }

  async function mountLiveOutputControls(){
    if(!currentLiveId)return;
    injectMultiOutputStyles();
    const grid=$('.destination-grid');if(!grid)return;
    const panel=grid.closest('section');if(panel)panel.id='destinos';
    const catalog=await loadOutputCatalog();
    const channelCatalog=(catalog.channels||{})[currentLiveId]||{};
    const channelSettings=(catalog.settings||{})[currentLiveId]||{};

    if(panel&&!$('.hs-output-toolbar',panel)){
      const bar=document.createElement('div');bar.className='hs-output-toolbar';
      bar.innerHTML=`<label>Adicionar outra saída<select id="hsOutputPlatform"><option value="youtube_shorts">YouTube Shorts 9:16</option><option value="youtube">YouTube normal</option><option value="kick">Kick</option><option value="twitch">Twitch</option><option value="kwai">Kwai</option><option value="custom">Custom RTMP</option></select></label><label>Nome desta saída <small>(opcional)</small><input id="hsOutputLabel" maxlength="80" placeholder="Ex.: Shorts MK2 #3"></label><button class="btn primary" type="button" id="hsAddOutput">+ Adicionar saída</button>`;
      panel.insertBefore(bar,grid);
      $('#hsAddOutput',bar)?.addEventListener('click',async()=>{
        const btn=$('#hsAddOutput',bar),fd=new FormData();fd.set('platform',$('#hsOutputPlatform',bar).value);fd.set('label',$('#hsOutputLabel',bar).value.trim());btn.disabled=true;btn.textContent='Criando...';
        try{await post(`/lives/${encodeURIComponent(currentLiveId)}/outputs/add`,fd);location.reload();}catch(e){alert('Falha ao criar saída: '+e.message);btn.disabled=false;btn.textContent='+ Adicionar saída';}
      });
    }

    const mainForm=$('form.form-layout');
    const sourcePanel=mainForm?$$('section.panel',mainForm).find(s=>s.querySelector('h2')?.textContent.trim()==='Fonte principal'):null;
    if(sourcePanel&&!$('.hs-rerun-box',sourcePanel)){
      const rerun=document.createElement('div');rerun.className='hs-rerun-box';
      const enabled=String(channelSettings.rerun_enabled||'0').toLowerCase();
      const checked=['1','true','on','yes','sim'].includes(enabled);
      rerun.innerHTML=`<h3>Rerun automático</h3><div class="hs-rerun-grid"><label class="check-row"><input type="checkbox" name="rerun_enabled" ${checked?'checked':''}> Esta live é rerun</label><label>Quando repetir, começar em <input type="text" id="hsRerunStartHuman" value="${esc(channelSettings.rerun_start_human||'00:00')}" placeholder="MM:SS ou HH:MM:SS"><input type="hidden" name="rerun_start_seconds" id="hsRerunStartSeconds" value="${Number(channelSettings.rerun_start_seconds||0)}"></label></div><p class="hs-rerun-help">A primeira reprodução começa normalmente. Quando o vídeo terminar, a próxima volta inicia do ponto escolhido — por exemplo <b>01:00</b> em vez de voltar para 00:00.</p>`;
      sourcePanel.appendChild(rerun);
      const human=$('#hsRerunStartHuman',rerun),hidden=$('#hsRerunStartSeconds',rerun);
      const sync=()=>{const parts=human.value.trim().split(':').map(Number);let sec=0;if(parts.every(Number.isFinite)){if(parts.length===3)sec=parts[0]*3600+parts[1]*60+parts[2];else if(parts.length===2)sec=parts[0]*60+parts[1];else if(parts.length===1)sec=parts[0];}hidden.value=String(Math.max(0,Math.floor(sec||0)));};
      human.addEventListener('input',sync);sync();
    }

    $$('.destination-card',grid).forEach(card=>{
      const toggle=card.querySelector('input[type="checkbox"][name$="_enabled"]');if(!toggle)return;
      const slug=toggle.name.slice(0,-8);card.dataset.outputSlug=slug;
      if($('.hs-output-actions',card))return;
      const meta=channelCatalog[slug]||{};
      const actions=document.createElement('div');actions.className='hs-output-actions';
      actions.innerHTML=`<span class="hs-output-state" data-output-state="${esc(slug)}">PARADA</span><button type="button" class="btn success" data-output-start="${esc(slug)}">▶ Iniciar só esta</button><button type="button" class="btn danger" data-output-stop="${esc(slug)}">■ Parar só esta</button>${meta.extra?`<button type="button" class="btn ghost" data-output-delete="${esc(slug)}">Excluir saída</button>`:''}`;
      card.appendChild(actions);
    });
    $$('[data-output-start]').forEach(btn=>btn.onclick=async()=>{btn.disabled=true;try{await post(`/lives/${encodeURIComponent(currentLiveId)}/outputs/${encodeURIComponent(btn.dataset.outputStart)}/start`);setTimeout(refreshStatus,350);}catch(e){alert('Falha ao iniciar: '+e.message);}finally{btn.disabled=false;}});
    $$('[data-output-stop]').forEach(btn=>btn.onclick=async()=>{btn.disabled=true;try{await post(`/lives/${encodeURIComponent(currentLiveId)}/outputs/${encodeURIComponent(btn.dataset.outputStop)}/stop`);setTimeout(refreshStatus,350);}catch(e){alert('Falha ao parar: '+e.message);}finally{btn.disabled=false;}});
    $$('[data-output-delete]').forEach(btn=>btn.onclick=async()=>{if(!confirm('Excluir esta saída RTMP adicional?'))return;btn.disabled=true;try{await post(`/lives/${encodeURIComponent(currentLiveId)}/outputs/${encodeURIComponent(btn.dataset.outputDelete)}/delete`);location.reload();}catch(e){alert('Não foi possível excluir: '+e.message);btn.disabled=false;}});
  }

  function paintLiveOutputs(channelStatus){
    if(!currentLiveId||!channelStatus)return;
    const states=channelStatus.platforms||{};
    $$('[data-output-state]').forEach(el=>{
      const state=states[el.dataset.outputState]||{};
      const running=!!state.running,reconnecting=!running&&state.retries>0;
      el.textContent=running?'● AO VIVO':reconnecting?'↻ RECONECTANDO':'PARADA';
      el.classList.toggle('live',running);el.classList.toggle('reconnect',reconnecting);
      const card=el.closest('.destination-card');
      card?.querySelector('[data-output-start]')?.toggleAttribute('hidden',running);
      card?.querySelector('[data-output-stop]')?.toggleAttribute('hidden',!running&&!reconnecting);
    });
  }

  function activeScheduleRuns(payload){
    const result=new Map();
    Object.entries(payload?.channels||{}).forEach(([cid,status])=>{
      if(status.schedule_id&&status.running){result.set(String(status.schedule_id),{cid,runId:status.run_id||'',startedAt:status.started_at||'',stopAt:status.stop_at||'',platforms:status.platforms||{}});}
      (status.parallel_runs||[]).forEach(run=>{
        if(!run.schedule_id)return;
        const alive=Object.values(run.platforms||{}).some(x=>x.running);if(!alive)return;
        result.set(String(run.schedule_id),{cid,runId:run.run_id||'',startedAt:run.started_at||'',stopAt:run.stop_at||'',platforms:run.platforms||{}});
      });
    });
    return result;
  }

  function paintScheduleProgress(payload){
    const board=$('#agendaBoard');if(!board)return;
    injectMultiOutputStyles();
    $$('.hs-schedule-progress',board).forEach(x=>x.remove());
    const runs=activeScheduleRuns(payload),now=Date.now();
    runs.forEach((run,sid)=>{
      const cards=$$(`.agenda-card[href="/schedules/${CSS.escape(sid)}/edit"],.agenda-card[href$="/schedules/${CSS.escape(sid)}/edit"]`,board).filter(x=>!x.hidden);
      const card=cards[0]||$$('.agenda-card',board).find(x=>x.getAttribute('href')?.includes(`/schedules/${sid}/edit`));if(!card)return;
      const main=$('.agenda-main',card);if(!main)return;
      const start=Date.parse(run.startedAt||''),stop=Date.parse(run.stopAt||'');const elapsed=Number.isFinite(start)?Math.max(0,(now-start)/1000):0;const total=Number.isFinite(start)&&Number.isFinite(stop)&&stop>start?(stop-start)/1000:0;const pct=total?Math.max(0,Math.min(100,elapsed/total*100)):0;
      const platforms=Object.values(run.platforms||{}).filter(x=>x.running).map(x=>x.label).filter(Boolean);
      const div=document.createElement('div');div.className='hs-schedule-progress';div.dataset.scheduleProgress=sid;
      div.innerHTML=`<div class="hs-schedule-progress-head"><strong>● AO VIVO AGORA</strong><span>${total?`${pct.toFixed(1)}% · ${fmtTime(elapsed)} / ${fmtTime(total)}`:`${fmtTime(elapsed)} ao vivo`}</span></div>${total?`<div class="hs-progress-track"><div class="hs-progress-fill" style="width:${pct.toFixed(2)}%"></div></div>`:''}<div class="hs-progress-extra">${platforms.length?esc(platforms.join(' • '))+' · ':''}${Number.isFinite(stop)?`termina ${new Date(stop).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}`:'sem horário final definido'}</div>`;
      main.appendChild(div);
    });
  }

  async function enhanceAgendaLabels(){
    const board=$('#agendaBoard');if(!board)return;
    const catalog=await loadOutputCatalog();
    const all={};Object.values(catalog.channels||{}).forEach(destinations=>Object.entries(destinations||{}).forEach(([slug,meta])=>{all[slug]=meta;}));
    const select=$('#agendaPlatform');
    if(select){Object.entries(all).filter(([,meta])=>meta.extra).forEach(([slug,meta])=>{if(!select.querySelector(`option[value="${CSS.escape(slug)}"]`)){const o=document.createElement('option');o.value=slug;o.textContent=meta.label;select.appendChild(o);}});}
    const relabel=()=>{$$('.agenda-pill',board).forEach(p=>{const raw=p.textContent.trim();if(all[raw])p.textContent=all[raw].label;else if(raw.includes('__')){const base=raw.split('__')[0];const labels={youtube:'YouTube',youtube_shorts:'YouTube Shorts',kick:'Kick',twitch:'Twitch',kwai:'Kwai',custom:'Custom RTMP'};p.textContent=labels[base]||raw;}});};
    relabel();new MutationObserver(()=>{relabel();if(lastStatusPayload)paintScheduleProgress(lastStatusPayload);}).observe(board,{childList:true,subtree:true});
  }

  async function refreshStatus(){
    try{
      const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)return;const d=await r.json();lastStatusPayload=d;
      ['cpu','ram','disk'].forEach(k=>{const el=$(`[data-metric="${k}"]`);if(el)el.textContent=d[k]+'%';});
      Object.entries(d.channels||{}).forEach(([cid,s])=>{
        const badge=$(`[data-status-id="${cid}"]`);
        if(badge){badge.textContent=s.running?'● AO VIVO':'PARADA';badge.classList.toggle('live',!!s.running);badge.classList.toggle('offline',!s.running);}
      });
      if(currentLiveId)paintLiveOutputs((d.channels||{})[currentLiveId]);
      paintScheduleProgress(d);
    }catch(e){}
  }
  setInterval(refreshStatus,5000);
  setInterval(()=>{if(lastStatusPayload)paintScheduleProgress(lastStatusPayload);},1000);

  async function desktopNotify(title,body){
    try{
      if(!('Notification' in window))return;
      if(Notification.permission==='default')return;
      if(Notification.permission==='granted')new Notification(title,{body});
    }catch(e){}
  }

  try{
    const es=new EventSource('/api/events');
    es.onmessage=refreshStatus;
    ['live_started','live_stopped','platform_started','platform_stopped','platform_reconnecting','schedule_triggered'].forEach(name=>es.addEventListener(name,e=>{
      refreshStatus();
      try{
        const d=JSON.parse(e.data||'{}');
        if(name==='platform_reconnecting'||name==='live_stopped')desktopNotify('HostStorm',name.replaceAll('_',' ')+' '+(d.slug||d.channel_id||''));
      }catch(_){}
    }));
  }catch(e){}

  function b64ToBytes(value){
    const padding='='.repeat((4-value.length%4)%4);
    const raw=atob((value+padding).replace(/-/g,'+').replace(/_/g,'/'));
    return Uint8Array.from([...raw].map(c=>c.charCodeAt(0)));
  }

  async function setupPushButton(){
    const btn=$('[data-push-toggle]'), status=$('[data-push-status]');
    if(!btn)return;
    if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){
      btn.disabled=true;btn.textContent='Push indisponível';if(status)status.textContent='Este navegador não oferece Web Push.';return;
    }
    const reg=await navigator.serviceWorker.ready;
    let subscription=await reg.pushManager.getSubscription();

    const paint=()=>{
      btn.textContent=subscription?'Desativar notificações neste dispositivo':'Ativar notificações neste dispositivo';
      if(status)status.textContent=subscription?'Push ativo neste dispositivo.':'Push ainda não ativado neste dispositivo.';
    };
    paint();

    btn.addEventListener('click',async()=>{
      btn.disabled=true;
      try{
        if(subscription){
          const endpoint=subscription.endpoint;
          await fetch('/professional/push/unsubscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint})});
          await subscription.unsubscribe();
          subscription=null;paint();return;
        }
        const permission=await Notification.requestPermission();
        if(permission!=='granted')throw new Error('Permissão de notificações não concedida.');
        const keyResp=await fetch('/professional/push/public-key',{cache:'no-store'});
        const keyData=await keyResp.json();
        if(!keyResp.ok||!keyData.public_key)throw new Error(keyData.error||'Chave Web Push indisponível.');
        subscription=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64ToBytes(keyData.public_key)});
        const save=await fetch('/professional/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(subscription.toJSON())});
        const saved=await save.json();
        if(!save.ok||!saved.ok)throw new Error(saved.error||'Falha registrando assinatura.');
        paint();
      }catch(e){
        if(status)status.textContent='Erro: '+(e?.message||e);
      }finally{btn.disabled=false;}
    });
  }
  setupPushButton().catch(()=>{});

  mountLiveOutputControls().catch(console.error);
  enhanceAgendaLabels().catch(()=>{});
  refreshStatus();
  $$('[data-copy]').forEach(b=>b.addEventListener('click',()=>navigator.clipboard?.writeText(b.dataset.copy||'')));
  setTimeout(()=>$$('.flash').forEach(x=>x.remove()),7000);
})();