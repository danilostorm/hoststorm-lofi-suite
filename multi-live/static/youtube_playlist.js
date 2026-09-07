(()=>{
  const form=document.getElementById('scheduleForm');
  const picker=document.getElementById('sourceModePicker');
  if(!form||!picker)return;
  const $=(q,r=document)=>r.querySelector(q);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const hms=value=>{let s=Math.max(0,Math.round(Number(value)||0));const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60);const sec=s%60;return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`};
  const originalAction=form.getAttribute('action')||'/schedules/save-v31';
  const library=document.getElementById('librarySourceFields');
  const singleUrl=document.getElementById('urlSourceFields');
  const status=document.getElementById('sourceStatus');

  const label=document.createElement('label');
  label.innerHTML='<input type="radio" name="source_mode" value="youtube_playlist"><span>▶ Playlist YouTube</span>';
  picker.appendChild(label);

  const box=document.createElement('div');
  box.id='youtubePlaylistSourceFields';
  box.className='source-fields url-source-box';
  box.hidden=true;
  box.innerHTML=`
    <div class="source-toolbar"><div><strong>Playlist real do YouTube</strong><small>O HostStorm lê os vídeos da playlist e transmite um após o outro na ordem do YouTube.</small></div></div>
    <label>URL da playlist
      <div class="input-action-row">
        <input id="youtubePlaylistUrl" name="playlist_url" type="url" placeholder="https://www.youtube.com/playlist?list=...">
        <button class="btn primary" type="button" id="probeYoutubePlaylist">Analisar playlist</button>
      </div>
    </label>
    <input type="hidden" name="playlist_probe_url" id="youtubePlaylistProbeUrl">
    <input type="hidden" name="playlist_title" id="youtubePlaylistTitleInput">
    <input type="hidden" name="playlist_duration_seconds" id="youtubePlaylistDurationInput" value="0">
    <div class="url-analysis" id="youtubePlaylistAnalysis">
      <div class="url-analysis-info" style="grid-column:1/-1">
        <span class="eyebrow">YOUTUBE PLAYLIST</span>
        <h3 id="youtubePlaylistTitle">Ainda não analisada</h3>
        <div class="url-meta"><span id="youtubePlaylistCount">0 vídeos</span><span id="youtubePlaylistDuration">⏱ duração pendente</span></div>
        <p>A lista é atualizada novamente quando a agenda inicia. Vídeos novos adicionados à playlist entram automaticamente na próxima execução.</p>
      </div>
    </div>
    <div class="form-grid two">
      <label class="check-row"><input type="checkbox" name="playlist_shuffle" id="youtubePlaylistShuffle"> Embaralhar vídeos</label>
      <label class="check-row"><input type="checkbox" name="playlist_repeat" id="youtubePlaylistRepeat"> Repetir playlist / 24h</label>
    </div>
    <div class="source-toolbar"><div><strong>Vídeos encontrados</strong><small id="youtubePlaylistPreviewNote">Analise a playlist para visualizar os itens.</small></div></div>
    <div class="media-selector" id="youtubePlaylistItems"><div class="empty-state">Nenhuma playlist analisada.</div></div>`;
  if(singleUrl&&singleUrl.parentNode)singleUrl.parentNode.insertBefore(box,singleUrl.nextSibling);

  const playlistUrl=$('#youtubePlaylistUrl');
  const probeBtn=$('#probeYoutubePlaylist');
  const analysis=$('#youtubePlaylistAnalysis');
  const title=$('#youtubePlaylistTitle');
  const titleInput=$('#youtubePlaylistTitleInput');
  const probeUrl=$('#youtubePlaylistProbeUrl');
  const durationInput=$('#youtubePlaylistDurationInput');
  const count=$('#youtubePlaylistCount');
  const duration=$('#youtubePlaylistDuration');
  const itemsBox=$('#youtubePlaylistItems');
  const previewNote=$('#youtubePlaylistPreviewNote');
  const shuffle=$('#youtubePlaylistShuffle');
  const repeat=$('#youtubePlaylistRepeat');

  function mode(){return form.querySelector('input[name="source_mode"]:checked')?.value||'library'}
  function refresh(){
    const m=mode();
    if(library)library.hidden=m!=='library';
    if(singleUrl)singleUrl.hidden=m!=='url';
    box.hidden=m!=='youtube_playlist';
    form.action=m==='youtube_playlist'?'/schedules/save-youtube-playlist':originalAction;
    if(status){
      status.textContent=m==='youtube_playlist'?'PLAYLIST YOUTUBE':m==='url'?'URL':'BIBLIOTECA';
      status.classList.toggle('running',m!=='library');
    }
  }
  picker.addEventListener('change',()=>setTimeout(refresh,0));

  function renderItems(items,totalCount){
    const list=Array.isArray(items)?items:[];
    if(!list.length){itemsBox.innerHTML='<div class="empty-state">Nenhum vídeo encontrado.</div>';return}
    itemsBox.innerHTML=list.slice(0,100).map((item,i)=>`<div class="playlist-youtube-item" style="padding:10px 12px;border-bottom:1px solid var(--border,#263244)"><strong>${i+1}. ${esc(item.title||item.id||'Vídeo')}</strong>${item.duration_seconds?`<small style="display:block;opacity:.7">${hms(item.duration_seconds)}</small>`:''}</div>`).join('');
    const total=Number(totalCount)||list.length;
    if(previewNote)previewNote.textContent=total>100?`Mostrando os primeiros 100 de ${total} vídeos. Todos serão transmitidos.`:`${total} vídeo(s) na playlist.`;
  }

  function applyMeta(data){
    if(title)title.textContent=data.title||'Playlist do YouTube';
    if(titleInput)titleInput.value=data.title||'';
    if(probeUrl)probeUrl.value=playlistUrl.value.trim();
    if(durationInput)durationInput.value=Number(data.duration_seconds)||0;
    if(count)count.textContent=`${Number(data.count)||0} vídeo(s)`;
    if(duration)duration.textContent=data.duration_seconds?`⏱ ${hms(data.duration_seconds)}${data.all_durations_known===false?' + itens sem duração':''}`:'⏱ duração não informada';
    analysis?.classList.add('ready');
    renderItems(data.items||[],data.count);
  }

  playlistUrl.addEventListener('input',()=>{
    if(probeUrl.value!==playlistUrl.value.trim()){
      probeUrl.value='';titleInput.value='';durationInput.value='0';analysis?.classList.remove('ready');
    }
  });

  probeBtn.addEventListener('click',async()=>{
    const url=playlistUrl.value.trim();
    if(!url){playlistUrl.focus();return}
    const old=probeBtn.textContent;probeBtn.disabled=true;probeBtn.textContent='Analisando...';analysis?.classList.add('loading');
    try{
      const r=await fetch('/api/media/probe-youtube-playlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
      const data=await r.json();
      if(!r.ok||!data.ok)throw new Error(data.error||'Não foi possível analisar a playlist.');
      applyMeta(data);
    }catch(e){
      analysis?.classList.remove('ready');
      if(title)title.textContent='Falha ao analisar';
      itemsBox.innerHTML=`<div class="empty-state">${esc(e.message||e)}</div>`;
      alert(e.message||e);
    }finally{
      analysis?.classList.remove('loading');probeBtn.disabled=false;probeBtn.textContent=old;
    }
  });

  form.addEventListener('submit',event=>{
    refresh();
    if(mode()==='youtube_playlist'&&!playlistUrl.value.trim()){
      event.preventDefault();playlistUrl.focus();alert('Cole a URL da playlist do YouTube.');
    }
  });

  async function loadExisting(){
    const sid=form.querySelector('input[name="id"]')?.value||'';
    if(!sid){refresh();return}
    try{
      const r=await fetch('/api/v1/schedules',{cache:'no-store'});
      if(!r.ok)return;
      const data=await r.json();
      const row=(data.schedules||[]).find(x=>String(x.id)===String(sid));
      if(!row||row.source_mode!=='youtube_playlist')return;
      const radio=form.querySelector('input[name="source_mode"][value="youtube_playlist"]');
      if(radio)radio.checked=true;
      playlistUrl.value=row.source_url||'';
      probeUrl.value=row.source_url||'';
      titleInput.value=row.source_title||'';
      durationInput.value=Number(row.source_duration_seconds)||0;
      shuffle.checked=!!row.shuffle;
      repeat.checked=!!row.repeat_playlist;
      applyMeta({title:row.source_title||'Playlist do YouTube',count:row.youtube_playlist_count||0,duration_seconds:row.source_duration_seconds||0,items:row.youtube_playlist_items||[],all_durations_known:true});
      refresh();
    }catch(_){refresh()}
  }

  refresh();
  loadExisting();
})();
