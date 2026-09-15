(()=>{
  const match=location.pathname.match(/^\/lives\/([^/]+)\/?$/);
  if(!match)return;
  const cid=decodeURIComponent(match[1]);
  const $=(q,r=document)=>r.querySelector(q);
  const $$=(q,r=document)=>[...r.querySelectorAll(q)];
  const labels={youtube:'YouTube',youtube_shorts:'YouTube Shorts',kick:'Kick',twitch:'Twitch',kwai:'Kwai',custom:'Custom RTMP'};

  function installStyles(){
    if($('#hsOutputManagerStyles'))return;
    const style=document.createElement('style');
    style.id='hsOutputManagerStyles';
    style.textContent=`
      .hs-output-filterbar{display:grid;grid-template-columns:minmax(220px,2fr) minmax(150px,1fr) minmax(150px,1fr);gap:10px;margin:0 0 16px;padding:12px;border:1px solid rgba(125,145,185,.2);border-radius:12px;background:rgba(255,255,255,.018)}
      .hs-output-filterbar label{margin:0}.hs-output-filter-footer{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.hs-output-filter-summary{font-size:.74rem;opacity:.65;min-height:1em}.hs-output-filter-actions{display:flex;gap:7px;flex-wrap:wrap}.hs-output-filter-actions .btn{padding:6px 9px;font-size:.72rem}
      .destination-grid{align-items:start}.destination-card{align-self:start}.destination-card.hs-output-filtered{display:none!important}
      .hs-output-connection{margin-top:10px;border:1px solid rgba(125,145,185,.18);border-radius:10px;background:rgba(255,255,255,.012);overflow:hidden}.hs-output-connection>summary{cursor:pointer;list-style:none;padding:10px 11px;font-size:.76rem;font-weight:700;display:flex;justify-content:space-between;gap:8px;align-items:center}.hs-output-connection>summary::-webkit-details-marker{display:none}.hs-output-connection>summary:after{content:'▾';opacity:.55}.hs-output-connection[open]>summary:after{transform:rotate(180deg)}.hs-output-connection-fields{padding:0 11px 11px;border-top:1px solid rgba(125,145,185,.12)}.hs-output-connection-fields>label:first-child{margin-top:10px}
      .hs-channel-default-note{font-size:.73rem;line-height:1.4;padding:9px 10px;margin:10px 0 0;border:1px solid rgba(125,145,185,.16);border-radius:9px;background:rgba(82,127,255,.035);opacity:.78}.hs-channel-default-note strong{color:#b8c8ff}
      .hs-output-legacy-rerun-hidden{display:none!important}
      @media(max-width:760px){.hs-output-filterbar{grid-template-columns:1fr}.hs-output-filter-footer{align-items:flex-start;flex-direction:column}}
    `;
    document.head.appendChild(style);
  }

  function slugFor(card){
    const toggle=card?.querySelector('input[type="checkbox"][name$="_enabled"]');
    return toggle?toggle.name.slice(0,-8):'';
  }

  function platformFor(slug){
    return String(slug||'').split('__',1)[0];
  }

  function stateFor(card){
    const el=card.querySelector('[data-output-state]');
    const text=(el?.textContent||'').toLowerCase();
    if(text.includes('ao vivo'))return 'live';
    if(text.includes('reconect'))return 'reconnecting';
    return 'stopped';
  }

  function organizeDefaults(){
    const form=$('form.form-layout');if(!form)return;
    const panels=$$('section.panel',form);
    const source=panels.find(p=>['Fonte principal','Fonte padrão do canal'].includes(p.querySelector('h2')?.textContent.trim()));
    const encoder=panels.find(p=>['Encoder & Perfil','Encoder padrão do canal'].includes(p.querySelector('h2')?.textContent.trim()));
    if(source){
      const h=source.querySelector('h2'),p=source.querySelector('.panel-head p');
      if(h)h.textContent='Fonte padrão do canal';
      if(p)p.textContent='Fallback herdado somente pelas saídas que escolherem “Usar fonte padrão do canal”.';
      $$('.hs-rerun-box',source).forEach(box=>{box.classList.add('hs-output-legacy-rerun-hidden');box.remove();});
      if(!source.querySelector('.hs-channel-default-note')){
        const note=document.createElement('div');note.className='hs-channel-default-note';
        note.innerHTML='<strong>Padrão / fallback:</strong> com fontes individuais, você não precisa preencher esta área. Fonte, bitrate e rerun de cada transmissão ficam no próprio Destino RTMP.';
        source.appendChild(note);
      }
    }
    if(encoder){
      const h=encoder.querySelector('h2'),p=encoder.querySelector('.panel-head p');
      if(h)h.textContent='Encoder padrão do canal';
      if(p)p.textContent='Base herdada pelas saídas: resolução, FPS, áudio e preset. O bitrate é configurável individualmente em cada destino.';
      if(!encoder.querySelector('.hs-channel-default-note')){
        const note=document.createElement('div');note.className='hs-channel-default-note';
        note.innerHTML='<strong>Herança:</strong> estas opções continuam úteis como padrão para evitar repetir configurações. Ajustes específicos da live ficam dentro de cada card RTMP.';
        encoder.appendChild(note);
      }
    }
  }

  function ensureFilterbar(panel,grid){
    let bar=$('.hs-output-filterbar',panel);
    if(bar)return bar;
    bar=document.createElement('div');
    bar.className='hs-output-filterbar';
    bar.innerHTML=`
      <label>Buscar saída<input type="search" id="hsOutputFilterSearch" placeholder="Nome, plataforma ou RTMP..."></label>
      <label>Status<select id="hsOutputFilterStatus"><option value="all">Todas</option><option value="live">Só ao vivo</option><option value="stopped">Só paradas</option><option value="reconnecting">Reconectando</option></select></label>
      <label>Plataforma<select id="hsOutputFilterPlatform"><option value="all">Todas as plataformas</option></select></label>
      <div class="hs-output-filter-footer"><div class="hs-output-filter-summary" id="hsOutputFilterSummary"></div><div class="hs-output-filter-actions"><button type="button" class="btn ghost" id="hsOutputCollapseAll">Recolher detalhes</button><button type="button" class="btn ghost" id="hsOutputExpandLive">Abrir ao vivo</button></div></div>`;
    panel.insertBefore(bar,grid);
    ['input','change'].forEach(evt=>bar.addEventListener(evt,applyFilters));
    $('#hsOutputCollapseAll',bar)?.addEventListener('click',()=>$$('.destination-card details',grid).forEach(d=>d.open=false));
    $('#hsOutputExpandLive',bar)?.addEventListener('click',()=>{
      $$('.destination-card',grid).forEach(card=>{
        if(card.classList.contains('hs-output-filtered'))return;
        const open=stateFor(card)==='live';
        $$('details',card).forEach(d=>d.open=open);
      });
    });
    return bar;
  }

  function organizeConnection(card){
    if(card.querySelector('.hs-output-connection'))return;
    const rtmp=card.querySelector(':scope > label input[name$="_rtmp_url"]')?.closest('label');
    const key=card.querySelector(':scope > label input[name$="_stream_key"]')?.closest('label');
    if(!rtmp&&!key)return;
    const format=$(':scope > label select[name="kwai_mode"]',card)?.closest('label');
    const details=document.createElement('details');details.className='hs-output-connection';
    const summary=document.createElement('summary');summary.textContent='Conexão RTMP e chave';
    const fields=document.createElement('div');fields.className='hs-output-connection-fields';
    details.append(summary,fields);
    const anchor=rtmp||key;
    card.insertBefore(details,anchor);
    [rtmp,key,format].filter(Boolean).forEach(node=>fields.appendChild(node));
  }

  function decorateCards(grid){
    const platforms=new Set();
    $$('.destination-card',grid).forEach(card=>{
      const slug=slugFor(card);if(!slug)return;
      const platform=platformFor(slug);platforms.add(platform);
      card.dataset.outputPlatform=platform;
      organizeConnection(card);
      const rtmp=card.querySelector('input[name$="_rtmp_url"]')?.value||'';
      card.dataset.outputSearch=((card.textContent||'')+' '+rtmp+' '+(labels[platform]||platform)).toLowerCase();
      const actions=$('.hs-output-actions',card);
      if(actions&&!actions.querySelector('[data-output-delete]')){
        const button=document.createElement('button');
        button.type='button';button.className='btn ghost';button.dataset.outputDelete=slug;button.textContent='Excluir saída';
        actions.appendChild(button);
      }
    });
    const select=$('#hsOutputFilterPlatform');
    if(select){
      const current=select.value;
      [...platforms].sort().forEach(platform=>{
        if(select.querySelector(`option[value="${CSS.escape(platform)}"]`))return;
        const option=document.createElement('option');option.value=platform;option.textContent=labels[platform]||platform;select.appendChild(option);
      });
      if([...select.options].some(o=>o.value===current))select.value=current;
    }
  }

  function applyFilters(){
    const grid=$('.destination-grid');if(!grid)return;
    const query=($('#hsOutputFilterSearch')?.value||'').trim().toLowerCase();
    const wantedStatus=$('#hsOutputFilterStatus')?.value||'all';
    const wantedPlatform=$('#hsOutputFilterPlatform')?.value||'all';
    let shown=0,total=0,live=0,reconnecting=0;
    $$('.destination-card',grid).forEach(card=>{
      total++;
      const state=stateFor(card);if(state==='live')live++;if(state==='reconnecting')reconnecting++;
      const platform=card.dataset.outputPlatform||platformFor(slugFor(card));
      const rtmp=card.querySelector('input[name$="_rtmp_url"]')?.value||'';
      const haystack=((card.dataset.outputSearch||'')+' '+(card.textContent||'')+' '+rtmp).toLowerCase();
      const visible=(!query||haystack.includes(query))&&(wantedStatus==='all'||state===wantedStatus)&&(wantedPlatform==='all'||platform===wantedPlatform);
      card.classList.toggle('hs-output-filtered',!visible);if(visible)shown++;
    });
    const summary=$('#hsOutputFilterSummary');
    if(summary)summary.textContent=`Mostrando ${shown} de ${total} saída(s) • ${live} ao vivo${reconnecting?` • ${reconnecting} reconectando`:''}`;
  }

  async function removeOutput(button){
    const card=button.closest('.destination-card');
    const slug=button.dataset.outputDelete||slugFor(card);if(!slug)return;
    const name=card?.querySelector('.toggle-title strong')?.textContent?.trim()||slug;
    if(!confirm(`Excluir a saída “${name}”?\n\nEla será removida desta tela. Se precisar depois, você pode criar outra saída da mesma plataforma.`))return;
    button.disabled=true;const old=button.textContent;button.textContent='Excluindo...';
    try{
      const response=await fetch(`/lives/${encodeURIComponent(cid)}/outputs/${encodeURIComponent(slug)}/remove`,{method:'POST',headers:{'X-Requested-With':'HostStorm'},cache:'no-store'});
      const payload=await response.json().catch(()=>({ok:false,message:`HTTP ${response.status}`}));
      if(!response.ok||!payload.ok)throw new Error(payload.message||`HTTP ${response.status}`);
      card?.remove();applyFilters();
    }catch(error){
      alert(error?.message||String(error));button.disabled=false;button.textContent=old;
    }
  }

  function mount(){
    const grid=$('.destination-grid');if(!grid)return false;
    const panel=grid.closest('section');if(!panel)return false;
    installStyles();organizeDefaults();ensureFilterbar(panel,grid);decorateCards(grid);applyFilters();
    if(!grid.dataset.outputManagerBound){
      grid.dataset.outputManagerBound='1';
      grid.addEventListener('click',event=>{
        const button=event.target.closest('[data-output-delete]');
        if(!button||!grid.contains(button))return;
        event.preventDefault();event.stopImmediatePropagation();removeOutput(button);
      },true);
      let queued=false;
      new MutationObserver(()=>{
        if(queued)return;queued=true;
        requestAnimationFrame(()=>{queued=false;organizeDefaults();decorateCards(grid);applyFilters();});
      }).observe(grid,{childList:true,subtree:true,characterData:true});
    }
    return true;
  }

  let attempts=0;
  const timer=setInterval(()=>{attempts++;organizeDefaults();if(mount()||attempts>50)clearInterval(timer);},100);
  setInterval(()=>{organizeDefaults();applyFilters();},2500);
})();
