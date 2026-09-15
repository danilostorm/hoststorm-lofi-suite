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
      .hs-output-filterbar label{margin:0}.hs-output-filter-summary{grid-column:1/-1;font-size:.74rem;opacity:.65;min-height:1em}
      .destination-card.hs-output-filtered{display:none!important}
      @media(max-width:760px){.hs-output-filterbar{grid-template-columns:1fr}}
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

  function ensureFilterbar(panel,grid){
    let bar=$('.hs-output-filterbar',panel);
    if(bar)return bar;
    bar=document.createElement('div');
    bar.className='hs-output-filterbar';
    bar.innerHTML=`
      <label>Buscar saída<input type="search" id="hsOutputFilterSearch" placeholder="Nome, plataforma ou chave/RTMP..."></label>
      <label>Status<select id="hsOutputFilterStatus"><option value="all">Todas</option><option value="live">Só ao vivo</option><option value="stopped">Só paradas</option><option value="reconnecting">Reconectando</option></select></label>
      <label>Plataforma<select id="hsOutputFilterPlatform"><option value="all">Todas as plataformas</option></select></label>
      <div class="hs-output-filter-summary" id="hsOutputFilterSummary"></div>`;
    panel.insertBefore(bar,grid);
    ['input','change'].forEach(evt=>bar.addEventListener(evt,applyFilters));
    return bar;
  }

  function decorateCards(grid){
    const platforms=new Set();
    $$('.destination-card',grid).forEach(card=>{
      const slug=slugFor(card);if(!slug)return;
      const platform=platformFor(slug);platforms.add(platform);
      card.dataset.outputPlatform=platform;
      card.dataset.outputSearch=(card.textContent||'').toLowerCase();
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
    let shown=0,total=0,live=0;
    $$('.destination-card',grid).forEach(card=>{
      total++;
      const state=stateFor(card);if(state==='live')live++;
      const platform=card.dataset.outputPlatform||platformFor(slugFor(card));
      const haystack=((card.dataset.outputSearch||'')+' '+(card.textContent||'')).toLowerCase();
      const visible=(!query||haystack.includes(query))&&(wantedStatus==='all'||state===wantedStatus)&&(wantedPlatform==='all'||platform===wantedPlatform);
      card.classList.toggle('hs-output-filtered',!visible);if(visible)shown++;
    });
    const summary=$('#hsOutputFilterSummary');
    if(summary)summary.textContent=`Mostrando ${shown} de ${total} saída(s) • ${live} ao vivo`;
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
      card?.remove();
      applyFilters();
    }catch(error){
      alert(error?.message||String(error));button.disabled=false;button.textContent=old;
    }
  }

  function mount(){
    const grid=$('.destination-grid');if(!grid)return false;
    const panel=grid.closest('section');if(!panel)return false;
    installStyles();ensureFilterbar(panel,grid);decorateCards(grid);applyFilters();
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
        requestAnimationFrame(()=>{queued=false;decorateCards(grid);applyFilters();});
      }).observe(grid,{childList:true,subtree:true,characterData:true});
    }
    return true;
  }

  let attempts=0;
  const timer=setInterval(()=>{attempts++;if(mount()||attempts>50)clearInterval(timer);},100);
  setInterval(applyFilters,2500);
})();
