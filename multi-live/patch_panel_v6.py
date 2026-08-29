from pathlib import Path

APP = Path('/app/app.py')
HTML = Path('/app/templates/index.html')

# -----------------------------
# Backend: formatação de data BR
# -----------------------------
s = APP.read_text(encoding='utf-8')

if 'def datetime_br_display(value):' not in s:
    marker = 'def now():\n'
    helper = '''def datetime_br_display(value):
    """Formata ISO datetime para dd/mm/aaaa às HH:MM:SS em America/Sao_Paulo."""
    if not value:
        return '-'

    raw = str(value).strip()
    if not raw:
        return '-'

    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BR_TZ)
        else:
            dt = dt.astimezone(BR_TZ)
        return dt.strftime('%d/%m/%Y às %H:%M:%S')
    except Exception:
        return raw


app.add_template_filter(datetime_br_display, 'datetime_br')


'''
    if marker not in s:
        raise RuntimeError('Patch panel v6: marcador def now() não encontrado')
    s = s.replace(marker, helper + marker, 1)

APP.write_text(s, encoding='utf-8')

# -----------------------------
# Frontend: reorganização visual
# -----------------------------
h = HTML.read_text(encoding='utf-8')

# Formata todos os locais conhecidos onde o encerramento automático era exibido em ISO.
h = h.replace(
    "{{ ch.scheduled_stop_at or '-' }}",
    "{{ ch.scheduled_stop_at|datetime_br }}"
)

# Classes auxiliares sem depender do conteúdo interno gerado pelas versões anteriores.
h = h.replace(
    '<div class="schedule-box">',
    '<div class="schedule-box schedule-box-v6">',
    1
)
h = h.replace(
    '<div class="schedule-live">',
    '<div class="schedule-live schedule-live-v6">',
    1
)

# Título do formulário.
form_marker = '  <form class="schedule-form" action="/schedule/add/{{ cid }}" method="post">'
if '<div class="schedule-form-title">' not in h and form_marker in h:
    h = h.replace(
        form_marker,
        '  <div class="schedule-form-title"><span>➕</span><div><b>Novo agendamento</b><small>Defina quando, qual vídeo e onde transmitir.</small></div></div>\n' + form_marker,
        1
    )

# Cabeçalho da lista de agendas existentes.
list_marker = '  <div class="schedule-list">'
if '<div class="schedule-list-head">' not in h and list_marker in h:
    h = h.replace(
        list_marker,
        '  <div class="schedule-list-head"><div><b>Agendas cadastradas</b><small>{{ ch.schedules|length }} agendamento(s)</small></div><span class="schedule-list-count">{{ ch.schedules|length }}</span></div>\n' + list_marker,
        1
    )

# Melhora o texto do cartão da live atual sem depender de plataformas específicas.
h = h.replace(
    '<b>Transmissão agendada em andamento:</b>',
    '<span class="schedule-live-kicker">● AO VIVO AGENDADA</span><br><b>Vídeo:</b>'
)

# CSS v6 é anexado por último para ter precedência sobre estilos antigos.
if '/* schedule-panel-v6 */' not in h:
    css = r'''
/* schedule-panel-v6 */
.schedule-box-v6{
  padding:18px;
  border-radius:16px;
  background:linear-gradient(180deg,rgba(30,41,67,.92),rgba(20,30,52,.96));
  border:1px solid rgba(96,165,250,.28);
}
.schedule-box-v6>h3{
  font-size:20px;
  margin-bottom:6px;
}
.schedule-box-v6>.small{
  line-height:1.45;
  margin-top:0;
}
.schedule-timezone{
  display:flex;
  align-items:center;
  flex-wrap:wrap;
  gap:4px;
  margin:10px 0 14px!important;
  padding:10px 12px!important;
  border-radius:12px!important;
  background:rgba(14,116,144,.16)!important;
  border:1px solid rgba(56,189,248,.26)!important;
}
.schedule-live-v6{
  margin:0 0 16px!important;
  padding:13px 14px!important;
  border-radius:13px!important;
  background:linear-gradient(135deg,rgba(5,150,105,.22),rgba(6,95,70,.18))!important;
  border:1px solid rgba(52,211,153,.34)!important;
  color:#dcfce7!important;
  line-height:1.55!important;
  font-size:13px!important;
}
.schedule-live-kicker{
  display:inline-block;
  margin-bottom:5px;
  padding:3px 8px;
  border-radius:999px;
  background:rgba(34,197,94,.20);
  border:1px solid rgba(74,222,128,.28);
  color:#bbf7d0;
  font-size:11px;
  font-weight:900;
  letter-spacing:.25px;
}
.schedule-form-title{
  display:flex;
  align-items:center;
  gap:10px;
  margin:4px 0 10px;
  padding-top:2px;
}
.schedule-form-title>span{
  width:34px;
  height:34px;
  display:grid;
  place-items:center;
  border-radius:10px;
  background:rgba(139,92,246,.18);
  border:1px solid rgba(168,85,247,.28);
}
.schedule-form-title b{
  display:block;
  font-size:15px;
  color:#f8fafc;
}
.schedule-form-title small{
  display:block;
  margin-top:2px;
  color:#94a3b8;
  font-size:11px;
}
.schedule-box-v6 .schedule-form{
  padding:13px;
  border-radius:13px;
  background:rgba(2,6,23,.18);
  border:1px solid rgba(148,163,184,.13);
}
.schedule-box-v6 .schedule-form label{
  font-size:12px;
  color:#cbd5e1;
  margin-bottom:6px;
}
.schedule-box-v6 .schedule-form select,
.schedule-box-v6 .schedule-form input{
  min-height:42px;
}
.schedule-box-v6 .schedule-platforms{
  margin-top:4px;
  padding:12px!important;
  border-radius:12px!important;
  background:rgba(15,23,42,.55)!important;
  border:1px solid rgba(148,163,184,.18)!important;
}
.schedule-box-v6 .schedule-platforms>strong,
.schedule-box-v6 .schedule-platforms>h4{
  font-size:14px;
}
.schedule-box-v6 .schedule-submit-row{
  margin-top:2px;
}
.schedule-box-v6 .schedule-submit-row button{
  min-width:170px!important;
  border-radius:11px;
}
.schedule-list-head{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin:16px 0 8px;
  padding-top:14px;
  border-top:1px solid rgba(148,163,184,.14);
}
.schedule-list-head b{
  display:block;
  font-size:15px;
  color:#f8fafc;
}
.schedule-list-head small{
  display:block;
  margin-top:2px;
  font-size:11px;
  color:#94a3b8;
}
.schedule-list-count{
  min-width:28px;
  height:28px;
  padding:0 8px;
  display:grid;
  place-items:center;
  border-radius:999px;
  background:rgba(59,130,246,.18);
  border:1px solid rgba(96,165,250,.26);
  color:#bfdbfe;
  font-size:12px;
  font-weight:800;
}
.schedule-box-v6 .schedule-list{
  gap:10px;
  margin-top:0;
}
.schedule-box-v6 .schedule-item{
  align-items:flex-start;
  padding:13px 14px;
  border-radius:13px;
  background:rgba(8,15,30,.52);
  border:1px solid rgba(148,163,184,.15);
  box-shadow:0 8px 24px rgba(0,0,0,.12);
}
.schedule-box-v6 .schedule-main{
  flex:1;
}
.schedule-box-v6 .schedule-main>strong{
  font-size:16px;
}
.schedule-box-v6 .schedule-meta{
  margin-top:8px;
  padding-top:8px;
  border-top:1px solid rgba(148,163,184,.12);
  line-height:1.6;
}
.schedule-box-v6 .schedule-actions{
  align-self:center;
}
.schedule-box-v6 .schedule-actions button{
  border-radius:10px;
  padding:8px 11px;
}
@media(max-width:700px){
  .schedule-box-v6{
    padding:14px;
  }
  .schedule-box-v6 .schedule-form{
    grid-template-columns:1fr!important;
  }
  .schedule-box-v6 .schedule-form>*{
    grid-column:1/-1!important;
  }
  .schedule-box-v6 .schedule-submit-row{
    justify-content:stretch!important;
  }
  .schedule-box-v6 .schedule-submit-row button{
    width:100%;
  }
  .schedule-list-head{
    align-items:flex-end;
  }
  .schedule-box-v6 .schedule-actions{
    width:100%;
  }
  .schedule-box-v6 .schedule-actions form{
    flex:1;
  }
  .schedule-box-v6 .schedule-actions button{
    width:100%;
  }
}
'''
    if '</style>' not in h:
        raise RuntimeError('Patch panel v6: </style> não encontrado')
    h = h.replace('</style>', css + '\n</style>', 1)

HTML.write_text(h, encoding='utf-8')
print('Patch panel v6 aplicado: datas BR + reorganização do agendador.')
