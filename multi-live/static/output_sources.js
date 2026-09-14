(()=>{
  const match=location.pathname.match(/^\/lives\/([^/]+)\/?$/);
  if(!match)return;
  const cid=decodeURIComponent(match[1]);
  const $=(q,r=document)=>r.querySelector(q);
  const $$=(q,r=document)=>[...r.querySelectorAll(q)];
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function installStyles(){
    if($('#hsOutputSourceStyles'))return;
    const style=document.createElement('style');
    style.id='hsOutputSourceStyles';
    style.textContent=`
      .hs-output-source-box{margin-top:14px;padding:12px;border:1px solid rgba(125,145,185,.22);border-radius:11px;background:rgba(255,255,255,.018)}
      .hs-output-source-box h4{margin:0 0 4px;font-size:.82rem}.hs-output-source-help{margin:0 0 10px;font-size:.72rem;opacity:.62;line-height:1.35}
      .hs-output-source-grid{display:grid;grid-template-columns:1fr;gap:9px}.hs-output-source-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:end;margin-top:9px}
      .hs-output-source-status{font-size:.72rem;min-height:1em;opacity:.72}.hs-output-source-status.ok{color:#67e39c;opacity:1}.hs-output-source-status.error{color:#ff8585;opacity:1}
      .hs-output-source-box [hidden]{display:none!important}
      @media(max-width:650px){.hs-output-source-row{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function sourceBox(slug,settings,videos){
    const mode=settings?.mode||'channel';
    const currentVideo=settings?.video||'';
    const options=['<option value="">Escolha um vídeo...</option>'].concat(
      videos.map(v=>`<option value="${esc(v)}" ${v===currentVideo?'selected':''}>${esc(v)}</option>`)
    ).join('');
    const box=document.createElement('div');
    box.className='hs-output-source-box';
    box.dataset.outputSource=slug;
    box.innerHTML=`
      <h4>Fonte desta saída</h4>
      <p class="hs-output-source-help">Escolha conteúdo exclusivo para esta live. “Fonte do canal” continua usando a fonte principal/vertical configurada acima.</p>
      <div class="hs-output-source-grid">
        <label>Origem
          <select data-output-source-mode>
            <option value="channel" ${mode==='channel'?'selected':''}>Usar fonte do canal</option>
            <option value="local" ${mode==='local'?'selected':''}>Arquivo local da Biblioteca</option>
            <option value="url" ${mode==='url'?'selected':''}>URL externa</option>
          </select>
        </label>
        <label data-output-local>Vídeo da Biblioteca
          <select data-output-source-video>${options}</select>
        </label>
        <label data-output-url>URL externa
          <input data-output-source-url value="${esc(settings?.url||'')}" placeholder="YouTube, HLS, MP4, RTMP...">
        </label>
      </div>
      <div class="hs-output-source-row">
        <span class="hs-output-source-status" data-output-source-status></span>
        <button type="button" class="btn ghost" data-output-source-save>Salvar fonte desta saída</button>
      </div>`;

    const modeSelect=$('[data-output-source-mode]',box);
    const localField=$('[data-output-local]',box);
    const urlField=$('[data-output-url]',box);
    const status=$('[data-output-source-status]',box);
    const button=$('[data-output-source-save]',box);
    const paint=()=>{
      const value=modeSelect.value;
      localField.hidden=value!=='local';
      urlField.hidden=value!=='url';
    };
    modeSelect.addEventListener('change',paint);
    paint();

    button.addEventListener('click',async()=>{
      const fd=new FormData();
      fd.set('mode',modeSelect.value);
      fd.set('video',$('[data-output-source-video]',box).value||'');
      fd.set('url',$('[data-output-source-url]',box).value.trim());
      button.disabled=true;
      status.className='hs-output-source-status';
      status.textContent='Salvando...';
      try{
        const response=await fetch(`/lives/${encodeURIComponent(cid)}/outputs/${encodeURIComponent(slug)}/source`,{
          method:'POST',body:fd,headers:{'X-Requested-With':'HostStorm'},cache:'no-store'
        });
        const payload=await response.json().catch(()=>({ok:false,message:`HTTP ${response.status}`}));
        status.classList.add(response.ok&&payload.ok?'ok':'error');
        status.textContent=payload.message||(response.ok?'Fonte salva.':'Falha ao salvar fonte.');
      }catch(error){
        status.classList.add('error');
        status.textContent='Falha ao salvar: '+(error?.message||error);
      }finally{
        button.disabled=false;
      }
    });
    return box;
  }

  async function mount(){
    const grid=$('.destination-grid');
    if(!grid)return;
    installStyles();
    let payload;
    try{
      const response=await fetch(`/api/output-sources/${encodeURIComponent(cid)}`,{cache:'no-store'});
      if(!response.ok)return;
      payload=await response.json();
    }catch(_){return;}
    const videos=Array.isArray(payload.videos)?payload.videos:[];
    const outputs=payload.outputs||{};
    $$('.destination-card',grid).forEach(card=>{
      if($('.hs-output-source-box',card))return;
      const toggle=card.querySelector('input[type="checkbox"][name$="_enabled"]');
      if(!toggle)return;
      const slug=toggle.name.slice(0,-8);
      const box=sourceBox(slug,outputs[slug]||{},videos);
      const actions=$('.hs-output-actions',card);
      if(actions)card.insertBefore(box,actions);else card.appendChild(box);
    });
  }

  mount().catch(console.error);
})();
