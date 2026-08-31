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

  // Última execução: progresso em tempo real e encerramento automático previsto.
  const scheduleId=$('input[name="id"]')?.value||'';
  const executionPanel=$$('.panel h2').find(h=>h.textContent.trim()==='Última execução')?.closest('.panel');
  const executionDetails=executionPanel?.querySelector('.detail-list');
  let executionState=null;
  let progressTimer=null;
  let refreshTimer=null;

  function detailRow(label){return executionDetails?[...executionDetails.children].find(row=>row.querySelector('span')?.textContent.trim()===label):null}
  function detailValue(label){return detailRow(label)?.querySelector('strong')?.textContent.trim()||''}
  function parseBrazilDate(value){
    const m=String(value||'').match(/(\d{2})\/(\d{2})\/(\d{4})\s+(?:às\s+)?(\d{2}):(\d{2}):(\d{2})/i);
    return m?Date.parse(`${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}:${m[6]}-03:00`):NaN;
  }
  function formatBrazilDate(ms){
    if(!Number.isFinite(ms))return '-';
    const p=new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).formatToParts(new Date(ms));
    const o={};p.forEach(x=>o[x.type]=x.value);return `${o.day}/${o.month}/${o.year} às ${o.hour}:${o.minute}:${o.second}`;
  }
  function plannedSeconds(){
    const detected=Math.max(0,Number(durationInput?.value)||0);
    const stop=Math.max(0,Number(stopBeforeInput?.value)||0);
    const limit=Math.max(0,Number(maxDurationInput?.value)||0)*60;
    let total=detected>stop?detected-stop:limit;
    if(total>0&&limit>0)total=Math.min(total,limit);
    return Math.max(0,total);
  }
  function ensureProgressCard(){
    if(!executionPanel||!executionDetails)return null;
    let card=$('#executionProgress',executionPanel);if(card)return card;
    card=document.createElement('div');card.id='executionProgress';card.className='execution-progress-card';
    card.innerHTML=`<div class="execution-progress-head"><div><span class="eyebrow">PROGRESSO DA TRANSMISSÃO</span><strong id="executionProgressPercent">0%</strong></div><span class="execution-progress-state" id="executionProgressState">calculando...</span></div><div class="live-progress-track" id="executionProgressTrack" role="progressbar" aria-label="Progresso da live" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="live-progress-fill" id="executionProgressFill"></div></div><div class="execution-timing-grid"><div><span>Tempo decorrido</span><strong id="executionElapsed">00:00</strong></div><div><span>Tempo restante</span><strong id="executionRemaining">--:--:--</strong></div><div><span>Encerramento previsto</span><strong id="executionExpectedEnd">-</strong></div></div><p class="execution-progress-note">O progresso usa a duração detectada da fonte e respeita o tempo configurado para encerrar antes do final.</p>`;
    executionPanel.insertBefore(card,executionDetails);return card;
  }
  function renderExecutionProgress(){
    if(!executionState)return;
    const card=ensureProgressCard();if(!card)return;
    const start=Date.parse(executionState.last_started_at||'')||parseBrazilDate(detailValue('Início'));
    const finish=executionState.last_finished_at?Date.parse(executionState.last_finished_at):NaN;
    const total=plannedSeconds();if(!Number.isFinite(start)||total<=0){card.hidden=true;return}
    card.hidden=false;
    const plannedEnd=start+total*1000;
    const clock=Number.isFinite(finish)?finish:Date.now();
    const elapsed=Math.max(0,(clock-start)/1000);
    const remaining=Math.max(0,total-elapsed);
    const pct=Math.max(0,Math.min(100,(elapsed/total)*100));
    const fill=$('#executionProgressFill',card), percent=$('#executionProgressPercent',card), state=$('#executionProgressState',card), track=$('#executionProgressTrack',card);
    if(fill)fill.style.width=`${pct}%`;if(percent)percent.textContent=`${pct.toFixed(pct>=10?1:2)}%`;if(track)track.setAttribute('aria-valuenow',String(Math.round(pct)));
    const elapsedEl=$('#executionElapsed',card),remainingEl=$('#executionRemaining',card),endEl=$('#executionExpectedEnd',card);
    if(elapsedEl)elapsedEl.textContent=hms(elapsed);if(remainingEl)remainingEl.textContent=hms(remaining);if(endEl)endEl.textContent=formatBrazilDate(plannedEnd);
    card.classList.toggle('is-finished',Number.isFinite(finish));card.classList.toggle('is-ending',!Number.isFinite(finish)&&remaining<=60);
    if(state){
      if(Number.isFinite(finish))state.textContent=elapsed+2>=total?'Finalizada':'Encerrada antes do previsto';
      else if(remaining<=0)state.textContent='Encerrando automaticamente…';
      else if(remaining<=60)state.textContent='Encerramento iminente';
      else state.textContent='● AO VIVO • encerramento automático ativo';
    }
  }
  async function refreshExecutionState(){
    if(!scheduleId||!executionPanel)return;
    try{
      const r=await fetch('/api/v1/schedules',{cache:'no-store'});if(!r.ok)throw new Error('status indisponível');
      const j=await r.json();const rows=Array.isArray(j.schedules)?j.schedules:[];const row=rows.find(x=>String(x.id)===String(scheduleId));
      if(row){
        executionState=row;
        const statusRow=detailRow('Status')?.querySelector('strong');if(statusRow&&row.last_status)statusRow.textContent=row.last_status;
        const finishRow=detailRow('Fim')?.querySelector('strong');if(finishRow&&row.last_finished_at)finishRow.textContent=formatBrazilDate(Date.parse(row.last_finished_at));
        renderExecutionProgress();
      }
    }catch(_){
      if(!executionState){
        const startText=detailValue('Início');
        if(startText&&startText!=='-')executionState={last_started_at:new Date(parseBrazilDate(startText)).toISOString(),last_finished_at:''};
      }
      renderExecutionProgress();
    }
  }
  if(scheduleId&&executionPanel&&detailValue('Início')!=='-'){
    refreshExecutionState();progressTimer=setInterval(renderExecutionProgress,1000);refreshTimer=setInterval(refreshExecutionState,12000);
    window.addEventListener('beforeunload',()=>{clearInterval(progressTimer);clearInterval(refreshTimer)},{once:true});
  }
})();
