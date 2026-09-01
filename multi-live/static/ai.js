(()=>{
  const $$=(q,r=document)=>[...r.querySelectorAll(q)];
  $$('input[type="range"]').forEach(input=>{const out=input.parentElement?.querySelector('output');const update=()=>{if(out)out.textContent=input.value+'%'};input.addEventListener('input',update)});
  const live=document.querySelector('[data-ai-live]');
  if(!live)return;
  async function refresh(){
    if(document.hidden)return;
    try{
      const r=await fetch('/api/ai/feed',{headers:{'Accept':'application/json'},cache:'no-store'});if(!r.ok)return;const d=await r.json();
      Object.entries(d.stats||{}).forEach(([k,v])=>{const el=document.querySelector(`[data-ai-stat="${k}"]`);if(el)el.textContent=v});
    }catch(e){}
  }
  setInterval(refresh,15000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
})();
