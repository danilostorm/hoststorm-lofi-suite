(()=>{
  const match=location.pathname.match(/^\/lives\/([^/]+)\/?$/);
  if(!match)return;
  const cid=decodeURIComponent(match[1]);
  const $=(q,r=document)=>r.querySelector(q);
  const $$=(q,r=document)=>[...r.querySelectorAll(q)];

  function formatTime(seconds){
    seconds=Math.max(0,Math.floor(Number(seconds)||0));
    const h=Math.floor(seconds/3600),m=Math.floor((seconds%3600)/60),s=seconds%60;
    return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }

  function parseTime(value){
    const raw=String(value||'').trim();
    if(!raw)return 0;
    const parts=raw.split(':').map(Number);
    if(parts.length>3||parts.some(x=>!Number.isFinite(x)||x<0))return 0;
    let seconds=0;
    if(parts.length===3)seconds=parts[0]*3600+parts[1]*60+parts[2];
    else if(parts.length===2)seconds=parts[0]*60+parts[1];
    else seconds=parts[0]||0;
    return Math.max(0,Math.min(86400,Math.floor(seconds||0)));
  }

  function slugFor(card){
    const toggle=card?.querySelector('input[type="checkbox"][name$="_enabled"]');
    return toggle?toggle.name.slice(0,-8):'';
  }

  function installStyles(){
    if($('#hsOutputStartStyles'))return;
    const style=document.createElement('style');
    style.id='hsOutputStartStyles';
    style.textContent=`
      .hs-output-initial-start{padding:10px;border:1px solid rgba(125,145,185,.16);border-radius:9px;background:rgba(124,77,255,.035)}
      .hs-output-initial-start label{margin:0;font-weight:700}.hs-output-initial-start input{margin-top:6px}
      .hs-output-initial-note{font-size:.72rem;opacity:.7;margin-top:6px;line-height:1.35}.hs-output-initial-note strong{color:#7ee8ae}
      .hs-output-initial-chip{font-size:.68rem;font-weight:600;padding:3px 7px;border:1px solid rgba(125,145,185,.24);border-radius:999px;opacity:.78;white-space:nowrap}
    `;
    document.head.appendChild(style);
  }

  let settings={};
  let loaded=false;

  async function loadSettings(){
    try{
      const response=await fetch(`/api/output-start/${encodeURIComponent(cid)}`,{cache:'no-store'});
      if(response.ok){
        const payload=await response.json();
        settings=payload.outputs||{};
      }
    }catch(_){/* keep defaults */}
    loaded=true;
  }

  async function saveOffset(slug,box){
    const input=$('[data-output-initial-start]',box);
    if(!input)return {ok:true};
    const seconds=parseTime(input.value);
    input.value=formatTime(seconds);
    try{
      const fd=new FormData();fd.set('start_seconds',String(seconds));
      const response=await fetch(`/lives/${encodeURIComponent(cid)}/outputs/${encodeURIComponent(slug)}/start-offset`,{
        method:'POST',body:fd,headers:{'X-Requested-With':'HostStorm'},cache:'no-store'
      });
      const payload=await response.json().catch(()=>({ok:false,message:`HTTP ${response.status}`}));
      if(response.ok&&payload.ok){
        settings[slug]={start_seconds:seconds};
        updateChip(box,seconds);
        input.dataset.savedValue=String(seconds);
        return {ok:true,payload};
      }
      return {ok:false,payload};
    }catch(error){
      return {ok:false,payload:{message:error?.message||String(error)}};
    }
  }

  function updateChip(box,seconds){
    const summary=box.querySelector('summary');if(!summary)return;
    let chip=$('.hs-output-initial-chip',summary);
    if(!chip){chip=document.createElement('span');chip.className='hs-output-initial-chip';summary.appendChild(chip);}
    chip.textContent=`Início ${formatTime(seconds)}`;
  }

  function decorate(box,slug){
    if(box.dataset.outputInitialStartMounted)return;
    box.dataset.outputInitialStartMounted='1';
    const rerun=$('.hs-output-rerun',box);
    const grid=$('.hs-output-source-grid',box);
    if(!grid)return;
    const seconds=Number(settings?.[slug]?.start_seconds||0);
    const panel=document.createElement('div');
    panel.className='hs-output-initial-start';
    panel.innerHTML=`
      <label>Iniciar esta live em
        <input type="text" data-output-initial-start value="${formatTime(seconds)}" placeholder="MM:SS ou HH:MM:SS" inputmode="numeric">
      </label>
      <div class="hs-output-initial-note"><strong>00:00</strong> inicia normalmente. Ex.: <strong>01:00</strong> pula o primeiro minuto somente no início desta live. O rerun continua usando o ponto configurado separadamente abaixo.</div>`;
    if(rerun)grid.insertBefore(panel,rerun);else grid.appendChild(panel);
    const input=$('[data-output-initial-start]',panel);
    input.dataset.savedValue=String(seconds);
    input.addEventListener('change',()=>{const value=parseTime(input.value);input.value=formatTime(value);updateChip(box,value);});
    input.addEventListener('input',()=>updateChip(box,parseTime(input.value)));
    updateChip(box,seconds);
  }

  function mount(){
    if(!loaded)return false;
    installStyles();
    let count=0;
    $$('.destination-card').forEach(card=>{
      const box=$('.hs-output-source-box',card),slug=slugFor(card);
      if(!box||!slug)return;
      decorate(box,slug);count++;
    });
    return count>0;
  }

  // Save the initial offset immediately before the existing save/start pipeline runs. Then
  // replay the click once with a bypass marker, so output_sources.js keeps owning the rest.
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-output-source-save],[data-output-start]');
    if(!button)return;
    if(button.dataset.hsStartOffsetBypass==='1'){
      delete button.dataset.hsStartOffsetBypass;
      return;
    }
    const card=button.closest('.destination-card'),box=card?.querySelector('.hs-output-source-box');
    const slug=slugFor(card);
    if(!box||!slug||!$('[data-output-initial-start]',box))return;
    event.preventDefault();event.stopImmediatePropagation();
    const result=await saveOffset(slug,box);
    if(!result.ok){
      box.open=true;
      alert(result.payload?.message||'Não foi possível salvar o ponto inicial desta saída.');
      return;
    }
    button.dataset.hsStartOffsetBypass='1';
    button.click();
  },true);

  loadSettings().finally(()=>{
    let attempts=0;
    const timer=setInterval(()=>{attempts++;if(mount()||attempts>80)clearInterval(timer);},100);
    const observer=new MutationObserver(()=>mount());
    const grid=$('.destination-grid');if(grid)observer.observe(grid,{childList:true,subtree:true});
  });
})();
