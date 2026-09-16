(()=>{
  const match=location.pathname.match(/^\/lives\/([^/]+)\/?$/);
  if(!match)return;
  const cid=decodeURIComponent(match[1]);
  const $=(q,r=document)=>r.querySelector(q);
  const $$=(q,r=document)=>[...r.querySelectorAll(q)];
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  let payload={nodes:[],outputs:{}};

  function installStyles(){
    if($('#hsClusterOutputStyles'))return;
    const style=document.createElement('style');
    style.id='hsClusterOutputStyles';
    style.textContent=`
      .hs-output-node{padding:10px;border:1px solid rgba(69,209,255,.18);border-radius:9px;background:rgba(69,209,255,.035)}
      .hs-output-node-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:7px}.hs-output-node-head strong{font-size:.78rem}
      .hs-output-node small{display:block;margin-top:6px;opacity:.65;line-height:1.35}.hs-output-node-status{font-size:.7rem;margin-top:6px;min-height:1em}.hs-output-node-status.ok{color:#67e39c}.hs-output-node-status.error{color:#ff8585}.hs-output-node-status.busy{color:#f5c76f}
      .hs-output-node-chip{font-size:.68rem;font-weight:600;padding:3px 7px;border:1px solid rgba(69,209,255,.25);border-radius:999px;opacity:.82;white-space:nowrap}
    `;
    document.head.appendChild(style);
  }

  function slugFor(card){
    const toggle=card?.querySelector('input[type="checkbox"][name$="_enabled"]');
    return toggle?toggle.name.slice(0,-8):'';
  }

  function displayNode(value){
    if(value==='inherit')return 'Servidor: canal';
    if(value==='local')return 'Servidor: local';
    if(value==='auto')return 'Servidor: auto';
    if(value.startsWith('specific:')){
      const id=value.slice(9);const node=(payload.nodes||[]).find(n=>String(n.id)===id);
      return `Servidor: ${node?.name||id}`;
    }
    return 'Servidor: canal';
  }

  function updateChip(box,value){
    const summary=box.querySelector('summary');if(!summary)return;
    let chip=$('.hs-output-node-chip',summary);
    if(!chip){chip=document.createElement('span');chip.className='hs-output-node-chip';summary.appendChild(chip);}
    chip.textContent=displayNode(value);
  }

  async function savePlacement(slug,select,panel){
    const status=$('.hs-output-node-status',panel);
    let mode=select.value,nodeId='';
    if(mode.startsWith('specific:')){nodeId=mode.slice(9);mode='specific';}
    status.className='hs-output-node-status busy';status.textContent='Salvando servidor…';
    try{
      const fd=new FormData();fd.set('mode',mode);fd.set('node_id',nodeId);
      const response=await fetch(`/lives/${encodeURIComponent(cid)}/outputs/${encodeURIComponent(slug)}/placement`,{
        method:'POST',body:fd,headers:{'X-Requested-With':'HostStorm'},cache:'no-store'
      });
      const data=await response.json().catch(()=>({ok:false,message:`HTTP ${response.status}`}));
      status.className='hs-output-node-status '+(response.ok&&data.ok?'ok':'error');
      status.textContent=data.message||(response.ok?'Salvo.':'Falha ao salvar.');
      if(response.ok&&data.ok)updateChip(panel.closest('.hs-output-source-box'),select.value);
    }catch(error){
      status.className='hs-output-node-status error';status.textContent=String(error?.message||error);
    }
  }

  function decorate(card){
    const box=$('.hs-output-source-box',card),slug=slugFor(card);if(!box||!slug||box.dataset.clusterNodeMounted)return false;
    box.dataset.clusterNodeMounted='1';
    const settings=payload.outputs?.[slug]||{};
    const mode=String(settings.node_mode||'inherit'),nodeId=String(settings.node_id||'');
    const current=mode==='specific'&&nodeId?`specific:${nodeId}`:mode;
    const nodes=(payload.nodes||[]);
    const options=[
      ['inherit','Herdar servidor do canal'],
      ['auto','Automático — melhor nó disponível'],
      ['local','Controlador local'],
    ];
    nodes.forEach(node=>options.push([`specific:${node.id}`,`${node.name} · ${node.status==='online'?'online':'offline'} · CPU ${Math.round(Number(node.cpu||0))}% · ${node.active_streams||0} live(s)`]));
    const panel=document.createElement('div');panel.className='hs-output-node';
    panel.innerHTML=`
      <div class="hs-output-node-head"><strong>Servidor de transmissão</strong><span>${nodes.filter(n=>n.status==='online').length} nó(s) online</span></div>
      <select data-output-node>${options.map(([v,l])=>`<option value="${esc(v)}" ${v===current?'selected':''}>${esc(l)}</option>`).join('')}</select>
      <small>Automático considera prioridade, CPU, RAM, GPU e quantidade de lives. Servidor específico mantém esta saída fixa naquele Agent.</small>
      <div class="hs-output-node-status"></div>`;
    const grid=$('.hs-output-source-grid',box),rerun=$('.hs-output-rerun',box);
    if(grid){if(rerun)grid.insertBefore(panel,rerun);else grid.appendChild(panel);}else box.appendChild(panel);
    const select=$('[data-output-node]',panel);select.addEventListener('change',()=>{updateChip(box,select.value);savePlacement(slug,select,panel);});
    updateChip(box,current);
    return true;
  }

  async function load(){
    try{
      const response=await fetch(`/api/output-sources/${encodeURIComponent(cid)}`,{cache:'no-store'});
      if(response.ok)payload=await response.json();
    }catch(_){payload={nodes:[],outputs:{}};}
  }

  async function mount(){
    installStyles();await load();
    let attempts=0;
    const timer=setInterval(()=>{
      attempts++;let count=0;$$('.destination-card').forEach(card=>{if(decorate(card))count++;});
      if(attempts>100||($$('.destination-card').length&&$$('.destination-card .hs-output-node').length===$$('.destination-card').length))clearInterval(timer);
    },100);
    const grid=$('.destination-grid');if(grid)new MutationObserver(()=>$$('.destination-card').forEach(decorate)).observe(grid,{childList:true,subtree:true});
  }

  mount().catch(console.error);
})();
