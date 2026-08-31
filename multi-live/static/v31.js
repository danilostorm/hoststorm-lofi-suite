(()=>{
  const $=(q,r=document)=>r.querySelector(q); const $$=(q,r=document)=>[...r.querySelectorAll(q)];
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const hms=value=>{let s=Math.max(0,Math.round(Number(value)||0));const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60);const sec=s%60;return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`};

  // Agendamento: alternância Biblioteca / URL.
  const sourcePicker=$('#sourceModePicker');
  const libraryFields=$('#librarySourceFields');
  const urlFields=$('#urlSourceFields');
  const sourceStatus=$('#sourceStatus');
  function selectedSourceMode(){return $('[name="source_mode"]:checked')?.value||'library'}
  function refreshSourceMode(){const remote=selectedSourceMode()==='url';if(libraryFields)libraryFields.hidden=remote;if(urlFields)urlFields.hidden=!remote;if(sourceStatus){sourceStatus.textContent=remote?'URL':'BIBLIOTECA';sourceStatus.classList.toggle('running',remote)}}
  sourcePicker?.addEventListener('change',refreshSourceMode); refreshSourceMode();

  const sourceUrl=$('#scheduleSourceUrl');
  const probeBtn=$('#probeSourceUrl');
  const analysis=$('#urlAnalysis');
  const preview=$('#urlPreview');
  const title=$('#urlSourceTitle');
  const durationLabel=$('#urlDurationLabel');
  const extractorLabel=$('#urlExtractorLabel');
  const probeUrl=$('#sourceProbeUrl');
  const titleInput=$('#sourceTitle');
  const durationInput=$('#sourceDuration');
  const extractorInput=$('#sourceExtractor');
  const previewInput=$('#sourcePreviewUrl');
  const maxDurationInput=$('[name="max_duration_minutes"]');
  const stopBeforeInput=$('[name="stop_before_seconds"]');

  function renderPreview(url,kind){
    if(!preview)return;
    const safe=esc(url||'');
    if(!url){preview.innerHTML='<div class="preview-placeholder">Preview indisponível para esta fonte, mas a transmissão ainda pode funcionar.</div>';return}
    if(kind==='iframe'||url.includes('youtube-nocookie.com/embed/')) preview.innerHTML=`<iframe src="${safe}" title="Preview" loading="lazy" allow="encrypted-media; picture-in-picture" allowfullscreen></iframe>`;
    else preview.innerHTML=`<video src="${safe}" controls muted playsinline preload="metadata"></video>`;
  }
  function autoDurationLimit(seconds){
    const duration=Math.max(0,Number(seconds)||0);if(!duration||!maxDurationInput)return;
    const current=Number(maxDurationInput.value)||0;
    if(current===0||maxDurationInput.dataset.hoststormAuto==='1'){
      maxDurationInput.value=String(Math.ceil(duration/60));
      maxDurationInput.dataset.hoststormAuto='1';
    }
    const stop=Math.max(0,Number(stopBeforeInput?.value)||0);
    const useful=Math.max(0,duration-stop);
    maxDurationInput.title=`Duração detectada ${hms(duration)} • live prevista ${hms(useful)} após parar ${Math.round(stop)}s antes`;
  }
  maxDurationInput?.addEventListener('input',()=>{maxDurationInput.dataset.hoststormAuto='0'});
  if(Number(durationInput?.value)>0)autoDurationLimit(durationInput.value);

  function clearProbe(){if(!sourceUrl)return;if(probeUrl&&probeUrl.value===sourceUrl.value.trim())return;if(probeUrl)probeUrl.value='';if(titleInput)titleInput.value='';if(durationInput)durationInput.value='0';if(extractorInput)extractorInput.value='';if(previewInput)previewInput.value='';if(analysis)analysis.classList.remove('ready')}
  sourceUrl?.addEventListener('input',clearProbe);
  if(previewInput?.value)renderPreview(previewInput.value,previewInput.value.includes('youtube-nocookie.com/embed/')?'iframe':'video');

  probeBtn?.addEventListener('click',async()=>{
    const url=sourceUrl?.value.trim();if(!url){sourceUrl?.focus();return}
    const old=probeBtn.textContent;probeBtn.disabled=true;probeBtn.textContent='Analisando...';analysis?.classList.add('loading');
    try{
      const r=await fetch('/api/media/probe-url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
      const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'Não foi possível analisar a URL.');
      if(probeUrl)probeUrl.value=url;if(titleInput)titleInput.value=d.title||'';if(durationInput)durationInput.value=d.duration_seconds||0;if(extractorInput)extractorInput.value=d.extractor||'';if(previewInput)previewInput.value=d.preview_url||'';
      if(d.duration_seconds)autoDurationLimit(d.duration_seconds);
      if(title)title.textContent=d.title||'Fonte remota';if(durationLabel)durationLabel.textContent=d.duration_seconds?`⏱ ${hms(d.duration_seconds)}`:'⏱ duração não informada';if(extractorLabel)extractorLabel.textContent=d.extractor||'URL direta / ffprobe';
      renderPreview(d.preview_url,d.preview_kind);analysis?.classList.add('ready');
    }catch(e){if(title)title.textContent='Falha ao analisar';if(durationLabel)durationLabel.textContent='⏱ duração pendente';if(extractorLabel)extractorLabel.textContent=String(e.message||e);renderPreview('','');analysis?.classList.remove('ready');alert(e.message||e)}
    finally{analysis?.classList.remove('loading');probeBtn.disabled=false;probeBtn.textContent=old}
  });

  // Busca rápida na seleção de mídia do agendamento.
  const scheduleSearch=$('#scheduleMediaSearch');
  scheduleSearch?.addEventListener('input',()=>{const q=scheduleSearch.value.trim().toLowerCase();$$('[data-media-name]',$('#scheduleMediaSelector')||document).forEach(el=>el.hidden=!!q&&!el.dataset.mediaName.includes(q))});

  // Input de arquivos sem o visual nativo grande do navegador.
  const files=$('#libraryFiles'); const fileLabel=$('#libraryFileLabel');
  files?.addEventListener('change',()=>{const n=files.files?.length||0;if(!fileLabel)return;fileLabel.textContent=n===0?'Nenhum arquivo selecionado':n===1?files.files[0].name:`${n} arquivos selecionados`});

  // Biblioteca: pesquisa e filtro local instantâneo.
  const libSearch=$('#librarySearch'); const libFilter=$('#libraryFilter'); let kind='all';
  function filterLibrary(){const q=(libSearch?.value||'').trim().toLowerCase();let visible=0;$$('[data-library-kind]').forEach(card=>{const matchKind=kind==='all'||card.dataset.libraryKind===kind;const matchText=!q||(card.dataset.librarySearch||'').includes(q);card.hidden=!(matchKind&&matchText);if(!card.hidden)visible++});const none=$('#libraryNoResults');if(none)none.hidden=visible!==0}
  libSearch?.addEventListener('input',filterLibrary);
  libFilter?.addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;kind=b.dataset.filter||'all';$$('[data-filter]',libFilter).forEach(x=>x.classList.toggle('active',x===b));filterLibrary()});
})();
