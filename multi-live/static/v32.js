(()=>{
  const provider=document.getElementById('provider');
  const boxes=[...document.querySelectorAll('.provider-box[data-provider]')];
  if(provider&&boxes.length){
    const sync=()=>boxes.forEach(box=>{
      const active=box.dataset.provider===provider.value;
      box.hidden=!active;
      box.querySelectorAll('input,select,textarea').forEach(el=>el.disabled=!active);
    });
    provider.addEventListener('change',sync);
    sync();
  }
})();
