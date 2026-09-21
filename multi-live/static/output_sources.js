(()=>{
  const match=location.pathname.match(/^\/lives\/([^/]+)\/?$/);
  if(!match)return;
  const cid=decodeURIComponent(match[1]);
  const $=(q,r=document)=>r.querySelector(q);
  const $$=(q,r=document)=>[...r.querySelectorAll(q)];
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const mbps=k=>`${(Number(k||0)/1000).toFixed(Number(k||0)%1000?1:0)} Mbps`;

  function formatTime(seconds){
    seconds=Math.max(0,Math.floor(Number(seconds)||0));
    const h=Math.floor(seconds/3600),m=Math.floor((seconds%3600)/60),s=seconds%60;
    return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }

  function parseTime(value){
    const raw=String(value||'').trim();
    if(!raw)return 0;
    const parts=raw.split(':').map(Number);
    if(parts.some(x=>!Number.isFinite(x)||x<0))return 0;
    let seconds=0;
    if(parts.length===3)seconds=parts[0]*3600+parts[1]*60+parts[2];
    else if(parts.length===2)seconds=parts[0]*60+parts[1];
    else if(parts.length===1)seconds=parts[0];
    return Math.max(0,Math.min(86400,Math.floor(seconds||0)));
  }

  function installStyles(){
    if($('#hsOutputSourceStyles'))return;
    const style=document.createElement('style');
    style.id='hsOutputSourceStyles';
    style.textContent=`
      .hs-output-source-box{margin-top:12px;border:1px solid rgba(125,145,185,.22);border-radius:11px;background:rgba(255,255,255,.018);overflow:hidden}
      .hs-output-source-box>summary{list-style:none;cursor:pointer;padding:11px 12px;display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:.79rem;font-weight:700}
      .hs-output-source-box>summary::-webkit-details-marker{display:none}.hs-output-source-box>summary:after{content:'▾';opacity:.55;transition:transform .15s ease}.hs-output-source-box[open]>summary:after{transform:rotate(180deg)}
      .hs-output-summary-text{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.hs-output-summary-chip{font-size:.68rem;font-weight:600;padding:3px 7px;border:1px solid rgba(125,145,185,.24);border-radius:999px;opacity:.78}
      .hs-output-source-inner{padding:0 12px 12px;border-top:1px solid rgba(125,145,185,.14)}
      .hs-output-source-help{margin:10px 0;font-size:.72rem;opacity:.62;line-height:1.4}
      .hs-output-source-grid{display:grid;grid-template-columns:1fr;gap:9px}.hs-output-source-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:end;margin-top:10px}
      .hs-output-source-status{font-size:.72rem;min-height:1em;opacity:.72}.hs-output-source-status.ok{color:#67e39c;opacity:1}.hs-output-source-status.error{color:#ff8585;opacity:1}.hs-output-source-status.busy{color:#f5c76f;opacity:1}
      .hs-output-bitrate-note,.hs-output-rerun-note,.hs-output-audio-note{font-size:.72rem;opacity:.7;margin-top:-2px}.hs-output-bitrate-note strong,.hs-output-rerun-note strong{color:#7ee8ae}
      .hs-output-audio{padding:10px;border:1px solid rgba(125,145,185,.16);border-radius:9px;background:rgba(54,215,140,.025);display:grid;gap:9px}.hs-output-audio label{margin:0}.hs-output-audio-mix{padding:9px;border:1px dashed rgba(82,127,255,.26);border-radius:8px;display:grid;gap:8px}.hs-output-audio-gains{display:grid;grid-template-columns:1fr 1fr;gap:8px}
      .hs-output-rerun{padding:10px;border:1px solid rgba(125,145,185,.16);border-radius:9px;background:rgba(82,127,255,.035)}
      .hs-output-rerun-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.hs-output-rerun-head label{margin:0;display:flex;align-items:center;gap:8px;font-weight:700}.hs-output-rerun-fields{margin-top:9px}
      .hs-output-source-box [hidden]{display:none!important}
      @media(max-width:650px){.hs-output-source-row{grid-template-columns:1fr}.hs-output-source-box>summary{align-items:flex-start;flex-direction:column}.hs-output-audio-gains{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function formDataFor(box,card=null,includeTransport=false){
    const fd=new FormData();
    fd.set('mode',$('[data-output-source-mode]',box)?.value||'channel');
    fd.set('video',$('[data-output-source-video]',box)?.value||'');
    fd.set('url',$('[data-output-source-url]',box)?.value.trim()||'');
    fd.set('audio_mode',$('[data-output-audio-mode]',box)?.value||'inherit');
    fd.set('audio_file',$('[data-output-audio-file]',box)?.value||'');
    fd.set('audio_url',$('[data-output-audio-url]',box)?.value.trim()||'');
    fd.set('audio_mix_profile',$('[data-output-audio-mix-profile]',box)?.value||'podcast');
    fd.set('audio_original_gain_db',$('[data-output-audio-original-gain]',box)?.value||'-10');
    fd.set('audio_external_gain_db',$('[data-output-audio-external-gain]',box)?.value||'0');
    fd.set('bitrate_mode',$('[data-output-bitrate-mode]',box)?.value||'inherit');
    fd.set('bitrate_k',$('[data-output-bitrate-k]',box)?.value||'0');
    if(includeTransport&&card){
      fd.set('rtmp_url',card.querySelector('input[name$="_rtmp_url"]')?.value.trim()||'');
      fd.set('stream_key',card.querySelector('input[name$="_stream_key"]')?.value.trim()||'');
    }
    return fd;
  }

  function rerunDataFor(box){
    const fd=new FormData();
    const enabled=!!$('[data-output-rerun-enabled]',box)?.checked;
    const human=$('[data-output-rerun-human]',box)?.value||'00:00';
    fd.set('enabled',enabled?'1':'0');
    fd.set('start_seconds',String(parseTime(human)));
    return fd;
  }

  async function sendRerun(slug,box){
    try{
      const response=await fetch(`/lives/${encodeURIComponent(cid)}/outputs/${encodeURIComponent(slug)}/rerun`,{
        method:'POST',body:rerunDataFor(box),headers:{'X-Requested-With':'HostStorm'},cache:'no-store'
      });
      const payload=await response.json().catch(()=>({ok:false,message:`HTTP ${response.status}`}));
      return {ok:response.ok&&payload.ok,payload,status:response.status};
    }catch(error){
      return {ok:false,payload:{message:error?.message||String(error)},status:0};
    }
  }

  async function sendSource(slug,box,card,{start=false}={}){
    const status=$('[data-output-source-status]',box);
    status.className='hs-output-source-status busy';
    status.textContent=start?'Salvando e iniciando esta saída...':'Salvando...';
    const rerun=await sendRerun(slug,box);
    if(!rerun.ok){
      status.className='hs-output-source-status error';
      status.textContent='Falha salvando rerun: '+(rerun.payload?.message||'erro desconhecido');
      box.open=true;
      return rerun;
    }
    const path=start?'source/start':'source';
    try{
      const response=await fetch(`/lives/${encodeURIComponent(cid)}/outputs/${encodeURIComponent(slug)}/${path}`,{
        method:'POST',body:formDataFor(box,card,start),headers:{'X-Requested-With':'HostStorm'},cache:'no-store'
      });
      const payload=await response.json().catch(()=>({ok:false,message:`HTTP ${response.status}`}));
      const ok=response.ok&&payload.ok;
      status.className='hs-output-source-status '+(ok?'ok':'error');
      status.textContent=payload.message||(ok?(start?'Live iniciada.':'Configuração salva.'):'Falha na operação.');
      if(!ok)box.open=true;
      return {ok,payload,status:response.status};
    }catch(error){
      status.className='hs-output-source-status error';
      status.textContent='Falha: '+(error?.message||error);
      box.open=true;
      return {ok:false,payload:{message:status.textContent},status:0};
    }
  }

  function sourceBox(slug,settings,rerunSettings,videos,audios){
    const mode=settings?.mode||'channel';
    const currentVideo=settings?.video||'';
    const audioMode=settings?.audio_mode||'inherit';
    const currentAudio=settings?.audio_file||'';
    const mixProfile=settings?.audio_mix_profile||'podcast';
    const originalGain=Number(settings?.audio_original_gain_db??-10);
    const externalGain=Number(settings?.audio_external_gain_db??0);
    const rateMode=settings?.bitrate_mode||'inherit';
    const customK=Number(settings?.bitrate_k||0)||Number(settings?.effective_bitrate_k||4000);
    const inherited=Number(settings?.inherited_bitrate_k||0);
    const recommended=Number(settings?.recommended_bitrate_k||0);
    const platform=String(settings?.platform||'');
    const youtube=platform==='youtube'||platform==='youtube_shorts';
    const rerunEnabled=!!rerunSettings?.enabled;
    const rerunStart=Number(rerunSettings?.start_seconds||0);
    const options=['<option value="">Escolha um vídeo...</option>'].concat(videos.map(v=>`<option value="${esc(v)}" ${v===currentVideo?'selected':''}>${esc(v)}</option>`)).join('');
    const audioOptions=['<option value="">Escolha um áudio...</option>'].concat(audios.map(a=>`<option value="${esc(a)}" ${a===currentAudio?'selected':''}>${esc(a)}</option>`)).join('');
    const box=document.createElement('details');
    box.className='hs-output-source-box';
    box.dataset.outputSource=slug;
    box.innerHTML=`
      <summary><span>Conteúdo, áudio, qualidade e rerun</span><span class="hs-output-summary-text" data-output-summary></span></summary>
      <div class="hs-output-source-inner">
        <p class="hs-output-source-help">Configuração exclusiva desta live. “Usar fonte do canal” herda os padrões do topo; qualquer opção abaixo sobrescreve somente este destino.</p>
        <div class="hs-output-source-grid">
          <label>Origem
            <select data-output-source-mode>
              <option value="channel" ${mode==='channel'?'selected':''}>Usar fonte padrão do canal</option>
              <option value="local" ${mode==='local'?'selected':''}>Arquivo local da Biblioteca</option>
              <option value="url" ${mode==='url'?'selected':''}>URL externa</option>
            </select>
          </label>
          <label data-output-local>Vídeo da Biblioteca<select data-output-source-video>${options}</select></label>
          <label data-output-url>URL externa<input data-output-source-url value="${esc(settings?.url||'')}" placeholder="YouTube, HLS, MP4, RTMP..."></label>
          <div class="hs-output-audio">
            <label>Áudio desta saída
              <select data-output-audio-mode>
                <option value="inherit" ${audioMode==='inherit'?'selected':''}>Herdar áudio padrão do canal</option>
                <option value="original" ${audioMode==='original'?'selected':''}>Áudio original do vídeo</option>
                <option value="library" ${audioMode==='library'?'selected':''}>Áudio da Biblioteca</option>
                <option value="url" ${audioMode==='url'?'selected':''}>Áudio de URL externa</option>
                <option value="mix_library" ${audioMode==='mix_library'?'selected':''}>Áudio original + Biblioteca</option>
                <option value="mix_url" ${audioMode==='mix_url'?'selected':''}>Áudio original + URL externa</option>
              </select>
            </label>
            <label data-output-audio-library>Áudio da Biblioteca<select data-output-audio-file>${audioOptions}</select></label>
            <label data-output-audio-url-field>URL do áudio<input data-output-audio-url value="${esc(settings?.audio_url||'')}" placeholder="MP3, M4A, HLS ou vídeo do YouTube"></label>
            <div data-output-audio-mix class="hs-output-audio-mix">
              <label>Mixagem
                <select data-output-audio-mix-profile>
                  <option value="podcast" ${mixProfile==='podcast'?'selected':''}>Automático — podcast em destaque</option>
                  <option value="balanced" ${mixProfile==='balanced'?'selected':''}>Balanceado — gameplay e externo</option>
                  <option value="manual" ${mixProfile==='manual'?'selected':''}>Manual — definir níveis</option>
                </select>
              </label>
              <div class="hs-output-audio-gains" data-output-audio-gains>
                <label>Áudio original (dB)<input type="number" min="-30" max="12" step="1" data-output-audio-original-gain value="${esc(originalGain)}"></label>
                <label>Áudio externo (dB)<input type="number" min="-30" max="12" step="1" data-output-audio-external-gain value="${esc(externalGain)}"></label>
              </div>
              <div class="hs-output-audio-note" data-output-audio-mix-note></div>
            </div>
            <div class="hs-output-audio-note">Biblioteca/URL externa substitui completamente o áudio do vídeo. Nas opções “original + ...”, os dois são misturados. Link do YouTube fornece apenas a faixa de áudio.</div>
          </div>
          <label>Bitrate desta saída
            <select data-output-bitrate-mode>
              ${youtube?`<option value="auto" ${rateMode==='auto'?'selected':''}>Automático YouTube recomendado (${mbps(recommended)})</option>`:`<option value="auto" ${rateMode==='auto'?'selected':''}>Automático</option>`}
              <option value="inherit" ${rateMode==='inherit'?'selected':''}>Herdar do canal (${mbps(inherited)})</option>
              <option value="custom" ${rateMode==='custom'?'selected':''}>Personalizado</option>
            </select>
          </label>
          <label data-output-bitrate-custom>Bitrate personalizado (kbps)<input type="number" min="500" max="50000" step="100" data-output-bitrate-k value="${Math.round(customK)}"></label>
          <div class="hs-output-bitrate-note" data-output-bitrate-note></div>
          <div class="hs-output-rerun">
            <div class="hs-output-rerun-head"><label><input type="checkbox" data-output-rerun-enabled ${rerunEnabled?'checked':''}> Rerun personalizado desta saída</label></div>
            <div class="hs-output-rerun-fields" data-output-rerun-fields>
              <label>Nas repetições, começar em<input type="text" data-output-rerun-human value="${esc(formatTime(rerunStart))}" placeholder="MM:SS ou HH:MM:SS"></label>
              <div class="hs-output-rerun-note">A primeira reprodução começa em <strong>00:00</strong>. A partir da segunda, somente esta saída volta do ponto escolhido.</div>
            </div>
          </div>
        </div>
        <div class="hs-output-source-row">
          <span class="hs-output-source-status" data-output-source-status></span>
          <button type="button" class="btn ghost" data-output-source-save>Salvar esta saída</button>
        </div>
      </div>`;

    const modeSelect=$('[data-output-source-mode]',box), localField=$('[data-output-local]',box), urlField=$('[data-output-url]',box);
    const videoSelect=$('[data-output-source-video]',box),urlInput=$('[data-output-source-url]',box);
    const audioModeSelect=$('[data-output-audio-mode]',box),audioLibraryField=$('[data-output-audio-library]',box),audioUrlField=$('[data-output-audio-url-field]',box),audioFileSelect=$('[data-output-audio-file]',box),audioUrlInput=$('[data-output-audio-url]',box);
    const mixBox=$('[data-output-audio-mix]',box),mixProfileSelect=$('[data-output-audio-mix-profile]',box),mixGains=$('[data-output-audio-gains]',box),originalGainInput=$('[data-output-audio-original-gain]',box),externalGainInput=$('[data-output-audio-external-gain]',box),mixNote=$('[data-output-audio-mix-note]',box);
    const rateSelect=$('[data-output-bitrate-mode]',box), customField=$('[data-output-bitrate-custom]',box), customInput=$('[data-output-bitrate-k]',box), note=$('[data-output-bitrate-note]',box);
    const rerunCheck=$('[data-output-rerun-enabled]',box),rerunFields=$('[data-output-rerun-fields]',box),rerunHuman=$('[data-output-rerun-human]',box);
    const summary=$('[data-output-summary]',box),button=$('[data-output-source-save]',box);

    const paintSource=()=>{const value=modeSelect.value;localField.hidden=value!=='local';urlField.hidden=value!=='url';};
    const paintAudio=()=>{
      const value=audioModeSelect.value;
      const mixed=value==='mix_library'||value==='mix_url';
      audioLibraryField.hidden=!(value==='library'||value==='mix_library');
      audioUrlField.hidden=!(value==='url'||value==='mix_url');
      mixBox.hidden=!mixed;
      if(mixed){
        const profile=mixProfileSelect.value;
        mixGains.hidden=profile!=='manual';
        mixNote.textContent=profile==='podcast'
          ?'O podcast é normalizado e fica em primeiro plano; o gameplay abaixa automaticamente quando o áudio externo está presente.'
          :profile==='balanced'
            ?'Os dois áudios ficam em nível semelhante, com limitador para evitar clip.'
            :'Use dB negativos para reduzir cada fonte. O limitador final continua ativo para evitar estouro.';
      }
    };
    const paintRate=()=>{
      const value=rateSelect.value;customField.hidden=value!=='custom';
      const effective=value==='custom'?Number(customInput.value||0):(value==='auto'?(youtube?recommended:inherited):inherited);
      note.innerHTML=`Efetivo ao reiniciar: <strong>${mbps(effective)}</strong> · CBR estável${youtube?' · recomendado para ingestão H.264 do YouTube':''}.`;
    };
    const paintRerun=()=>{rerunFields.hidden=!rerunCheck.checked;};
    const paintSummary=()=>{
      const source=modeSelect.value==='local'?(videoSelect.value?`Biblioteca: ${videoSelect.value}`:'Biblioteca'):modeSelect.value==='url'?'URL externa':'Fonte do canal';
      const rate=rateSelect.value==='custom'?mbps(Number(customInput.value||0)):rateSelect.value==='auto'?(youtube?`Auto ${mbps(recommended)}`:'Auto'):`Canal ${mbps(inherited)}`;
      const audio=audioModeSelect.value==='original'?'Áudio original':audioModeSelect.value==='library'?(audioFileSelect.value?`Áudio: ${audioFileSelect.value}`:'Áudio: Biblioteca'):audioModeSelect.value==='url'?'Áudio: URL externa':audioModeSelect.value==='mix_library'?'Mix original + Biblioteca':audioModeSelect.value==='mix_url'?'Mix original + URL':'Áudio: canal';
      const rerun=rerunCheck.checked?`Rerun ${formatTime(parseTime(rerunHuman.value))}`:'Rerun off';
      summary.innerHTML=`<span class="hs-output-summary-chip">${esc(source)}</span><span class="hs-output-summary-chip">${esc(audio)}</span><span class="hs-output-summary-chip">${esc(rate)}</span><span class="hs-output-summary-chip">${esc(rerun)}</span>`;
    };
    const repaint=()=>{paintSource();paintAudio();paintRate();paintRerun();paintSummary();};
    [modeSelect,videoSelect,audioModeSelect,audioFileSelect,audioUrlInput,mixProfileSelect,originalGainInput,externalGainInput,rateSelect,customInput,rerunCheck,rerunHuman,urlInput].forEach(el=>{
      if(!el)return;el.addEventListener(el.tagName==='SELECT'||el.type==='checkbox'?'change':'input',repaint);
    });
    repaint();
    button.addEventListener('click',async()=>{button.disabled=true;try{await sendSource(slug,box,box.closest('.destination-card'));paintSummary();}finally{button.disabled=false;}});
    return box;
  }

  function clarifyChannelDefaults(){
    const form=$('form.form-layout');if(!form)return;
    const panels=$$('section.panel',form);
    const source=panels.find(p=>p.querySelector('h2')?.textContent.trim()==='Fonte principal');
    const encoder=panels.find(p=>p.querySelector('h2')?.textContent.trim()==='Encoder & Perfil');
    if(source){
      const h=source.querySelector('h2'),p=source.querySelector('.panel-head p');
      if(h)h.textContent='Fonte padrão do canal';
      if(p)p.textContent='Fallback para saídas configuradas como “Usar fonte padrão do canal”. Fontes individuais ficam nos Destinos RTMP.';
      source.querySelector('.hs-rerun-box')?.remove();
    }
    if(encoder){
      const h=encoder.querySelector('h2'),p=encoder.querySelector('.panel-head p');
      if(h)h.textContent='Encoder padrão do canal';
      if(p)p.textContent='Resolução, FPS, áudio e preset usados como base. O bitrate pode ser definido individualmente em cada destino.';
    }
  }

  async function mount(){
    const grid=$('.destination-grid');if(!grid)return;installStyles();clarifyChannelDefaults();
    let sourcePayload={},rerunPayload={};
    try{
      const [sourceResponse,rerunResponse]=await Promise.all([
        fetch(`/api/output-sources/${encodeURIComponent(cid)}`,{cache:'no-store'}),
        fetch(`/api/output-rerun/${encodeURIComponent(cid)}`,{cache:'no-store'})
      ]);
      if(sourceResponse.ok)sourcePayload=await sourceResponse.json();
      if(rerunResponse.ok)rerunPayload=await rerunResponse.json();
    }catch(_){return;}
    const videos=Array.isArray(sourcePayload.videos)?sourcePayload.videos:[];
    const audios=Array.isArray(sourcePayload.audios)?sourcePayload.audios:[];
    const outputs=sourcePayload.outputs||{},reruns=rerunPayload.outputs||{};
    $$('.destination-card',grid).forEach(card=>{
      if($('.hs-output-source-box',card))return;
      const toggle=card.querySelector('input[type="checkbox"][name$="_enabled"]');if(!toggle)return;
      const slug=toggle.name.slice(0,-8);const box=sourceBox(slug,outputs[slug]||{},reruns[slug]||{},videos,audios);const actions=$('.hs-output-actions',card);
      if(actions)card.insertBefore(box,actions);else card.appendChild(box);
    });
    if(!grid.dataset.outputSourceStartBound){
      grid.dataset.outputSourceStartBound='1';
      grid.addEventListener('click',async event=>{
        const button=event.target.closest('[data-output-start]');if(!button||!grid.contains(button))return;
        const card=button.closest('.destination-card'),box=card?.querySelector('.hs-output-source-box');if(!card||!box)return;
        event.preventDefault();event.stopImmediatePropagation();const slug=button.dataset.outputStart||box.dataset.outputSource||'';if(!slug)return;
        const old=button.textContent;button.disabled=true;button.textContent='Iniciando...';
        try{
          const result=await sendSource(slug,box,card,{start:true});
          if(!result.ok){alert(result.payload?.message||'Não foi possível iniciar esta saída.');return;}
          button.textContent='✓ Iniciada';setTimeout(()=>{button.textContent=old;button.disabled=false;},1800);
        }finally{if(button.textContent==='Iniciando...'){button.textContent=old;button.disabled=false;}}
      },true);
    }
  }
  mount().catch(console.error);
})();
