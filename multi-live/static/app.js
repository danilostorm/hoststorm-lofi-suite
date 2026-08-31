(()=>{
  const $=(q,root=document)=>root.querySelector(q); const $$=(q,root=document)=>[...root.querySelectorAll(q)];
  const menu=$('#menuButton'); if(menu) menu.addEventListener('click',()=>$('.sidebar')?.classList.toggle('open'));
  const search=$('#liveSearch'); if(search) search.addEventListener('input',()=>{const q=search.value.toLowerCase();$$('.channel-card').forEach(el=>el.hidden=!el.dataset.name.includes(q));});
  function setScheduleFields(){const kind=$('#scheduleKind');if(!kind)return;const weekly=$('#weekdayPicker'),once=$('#runDateField');if(weekly)weekly.style.display=kind.value==='weekly'?'flex':'none';if(once)once.style.display=kind.value==='once'?'grid':'none';}
  $('#scheduleKind')?.addEventListener('change',setScheduleFields); setScheduleFields();

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

  async function refreshStatus(){
    try{
      const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)return;const d=await r.json();
      ['cpu','ram','disk'].forEach(k=>{const el=$(`[data-metric="${k}"]`);if(el)el.textContent=d[k]+'%';});
      Object.entries(d.channels||{}).forEach(([cid,s])=>{
        const badge=$(`[data-status-id="${cid}"]`);
        if(badge){badge.textContent=s.running?'● AO VIVO':'PARADA';badge.classList.toggle('live',!!s.running);badge.classList.toggle('offline',!s.running);}
      });
    }catch(e){}
  }
  setInterval(refreshStatus,5000);

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
    ['live_started','live_stopped','platform_started','platform_reconnecting','schedule_triggered'].forEach(name=>es.addEventListener(name,e=>{
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

  $$('[data-copy]').forEach(b=>b.addEventListener('click',()=>navigator.clipboard?.writeText(b.dataset.copy||'')));
  setTimeout(()=>$$('.flash').forEach(x=>x.remove()),7000);
})();
