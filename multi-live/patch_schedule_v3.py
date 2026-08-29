from pathlib import Path

APP = Path('/app/app.py')
HTML = Path('/app/templates/index.html')


def replace_once(text, old, new, label):
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f'Patch v3: trecho não encontrado: {label}')
    return text.replace(old, new, 1)


# Backend: aceita HH:MM/HH:MM:SS e normaliza para HH:MM, sempre interpretado no fuso BR.
s = APP.read_text(encoding='utf-8')
if 'def normalize_schedule_time(value):' not in s:
    marker = 'def normalize_schedule_entry(entry, destinations=None):\n'
    helper = '''def normalize_schedule_time(value):\n    """Aceita o valor enviado por <input type=time> e salva sempre como HH:MM."""\n    raw = str(value or '').strip()\n    if not raw:\n        return ''\n\n    for fmt in ('%H:%M', '%H:%M:%S'):\n        try:\n            return datetime.strptime(raw, fmt).strftime('%H:%M')\n        except ValueError:\n            pass\n\n    return ''\n\n\n'''
    if marker not in s:
        raise RuntimeError('Patch v3: normalize_schedule_entry não encontrado')
    s = s.replace(marker, helper + marker, 1)

s = replace_once(
    s,
    "    schedule_time = str(entry.get('time', '') or '').strip()\n",
    "    schedule_time = normalize_schedule_time(entry.get('time', ''))\n",
    'normalização da agenda',
)
s = replace_once(
    s,
    "    if not re.fullmatch(r'(?:[01]\\d|2[0-3]):[0-5]\\d', schedule_time):\n        return None\n",
    "    if not schedule_time:\n        return None\n",
    'validação da agenda',
)
s = replace_once(
    s,
    "    schedule_time = str(request.form.get('time', '') or '').strip()\n    video = os.path.basename(str(request.form.get('video', '') or '').strip())\n",
    "    raw_schedule_time = str(request.form.get('time', '') or '').strip()\n    schedule_time = normalize_schedule_time(raw_schedule_time)\n    video = os.path.basename(str(request.form.get('video', '') or '').strip())\n",
    'horário recebido pelo formulário',
)
s = replace_once(
    s,
    "    if weekday < 0 or weekday > 6 or not re.fullmatch(r'(?:[01]\\d|2[0-3]):[0-5]\\d', schedule_time):\n        log(cid, f'[{now()}] Agendamento inválido: dia/horário incorreto.' + chr(10))\n        return redirect(url_for('index'))\n",
    "    if weekday < 0 or weekday > 6 or not schedule_time:\n        received = raw_schedule_time or '(vazio)'\n        log(cid, f'[{now()}] Agendamento inválido: dia/horário incorreto. Horário recebido: {received}. Fuso do agendador: America/Sao_Paulo.' + chr(10))\n        return redirect(url_for('index'))\n",
    'validação do formulário',
)
APP.write_text(s, encoding='utf-8')

# Frontend: plataformas visíveis dentro do balão e horário BR explícito.
h = HTML.read_text(encoding='utf-8')
h = replace_once(
    h,
    '.schedule-form{display:grid;grid-template-columns:1fr 1fr 1.6fr auto;gap:9px;align-items:end;margin-top:10px}',
    '.schedule-form{display:grid;grid-template-columns:1fr 1fr 1.65fr;gap:10px;align-items:end;margin-top:10px}',
    'grade do formulário',
)

if '.schedule-timezone{' not in h:
    css_marker = '.schedule-form label{margin-top:0}\n'
    extra_css = '''.schedule-timezone{margin:8px 0 12px;padding:8px 10px;border-radius:10px;background:rgba(14,165,233,.10);border:1px solid rgba(56,189,248,.18);font-size:12px;color:#bae6fd}\n.schedule-submit-row{grid-column:1/-1;display:flex;justify-content:flex-end}\n.schedule-submit-row button{min-width:150px;height:42px;white-space:nowrap}\n'''
    if css_marker not in h:
        raise RuntimeError('Patch v3: CSS do agendador não encontrado')
    h = h.replace(css_marker, css_marker + extra_css, 1)

old_desc = '<p class="small">Escolha o dia, horário, vídeo e as plataformas desta agenda. Você pode transmitir, por exemplo, somente na Twitch mesmo que a live manual tenha YouTube, Kick e outros destinos ativos. O sistema detecta a duração com FFprobe e encerra exatamente 1 minuto antes do final do vídeo. O modo manual continua 24/7.</p>'
new_desc = '<p class="small">Escolha o dia, horário, vídeo e marque abaixo somente as plataformas que devem receber esta live. O sistema detecta a duração com FFprobe e encerra exatamente 1 minuto antes do final do vídeo. O modo manual continua 24/7.</p>\n  <div class="schedule-timezone">🕒 <b>Fuso do agendador:</b> Brasil / São Paulo (America/Sao_Paulo) &nbsp;•&nbsp; <b>Hora do servidor:</b> {{ now }}</div>'
h = replace_once(h, old_desc, new_desc, 'texto/fuso do agendador')

h = replace_once(
    h,
    '<div><label>Horário</label><input type="time" name="time" required></div>',
    '<div><label>Horário Brasil (São Paulo)</label><input type="time" name="time" required step="60"></div>',
    'campo de horário',
)

h = replace_once(
    h,
    '    <button type="submit">+ Agendar</button>\n    <div class="schedule-platforms">',
    '    <div class="schedule-platforms">',
    'posição das plataformas',
)

submit_anchor = '      <div class="small" style="margin-top:7px">Marque somente os destinos que devem receber esta agenda. Ex.: deixe apenas <b>Twitch</b> marcada para transmitir só nela.</div>\n    </div>\n  </form>'
submit_repl = '      <div class="small" style="margin-top:7px">Marque somente os destinos que devem receber esta agenda. Ex.: deixe apenas <b>Twitch</b> marcada para transmitir só nela.</div>\n    </div>\n    <div class="schedule-submit-row"><button type="submit">+ Agendar live</button></div>\n  </form>'
h = replace_once(h, submit_anchor, submit_repl, 'botão Agendar após plataformas')

HTML.write_text(h, encoding='utf-8')
print('Patch v3 aplicado.')
