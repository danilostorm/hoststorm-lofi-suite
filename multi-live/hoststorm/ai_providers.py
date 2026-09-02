from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .ai_db import get_provider


def _json_request(url, payload=None, headers=None, method='POST', timeout=45):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    hdr = {'Accept': 'application/json', **(headers or {})}
    if body is not None:
        hdr.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace')
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace') if hasattr(exc, 'read') else ''
        raise RuntimeError(f'HTTP {exc.code}: {(raw or str(exc))[-1800:]}') from exc


def _binary_request(url, payload, headers=None, timeout=90):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={'Accept': '*/*', 'Content-Type': 'application/json', **(headers or {})}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(), response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace') if hasattr(exc, 'read') else ''
        raise RuntimeError(f'TTS HTTP {exc.code}: {(raw or str(exc))[-1800:]}') from exc


def _auth_headers(config):
    key = str(config.get('api_key') or '').strip()
    headers = {}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    extra = config.get('headers') or {}
    if isinstance(extra, dict):
        headers.update({str(k): str(v) for k, v in extra.items() if k and v not in (None, '')})
    return headers


def _join(base, suffix):
    return str(base or '').rstrip('/') + '/' + str(suffix or '').lstrip('/')


_REPLY_ALIASES = (
    'reply', 'mensagem', 'message', 'text', 'response', 'answer', 'content',
    'output', 'result', 'resposta',
)


def _normalize_result(value, fallback_text=''):
    """Normalize heterogeneous LLM JSON into the AI Live Host contract.

    OpenAI-compatible routers may select providers that honor JSON mode but do
    not preserve our preferred field names. The engine should tolerate common
    aliases while always exposing reply/voice/memory_facts/reason internally.
    """
    if not isinstance(value, dict):
        value = {'reply': '' if value is None else str(value)}
    result = dict(value)
    reply = result.get('reply')
    if not isinstance(reply, str) or not reply.strip():
        reply = ''
        for key in _REPLY_ALIASES[1:]:
            candidate = result.get(key)
            if isinstance(candidate, str) and candidate.strip():
                reply = candidate.strip()
                break
            if isinstance(candidate, dict):
                nested = _normalize_result(candidate)
                if nested.get('reply'):
                    reply = nested['reply']
                    break
    if not reply and fallback_text:
        reply = str(fallback_text).strip()
    result['reply'] = str(reply or '').strip()

    voice = result.get('voice')
    if not isinstance(voice, str) or not voice.strip():
        result['voice'] = result['reply']
    else:
        result['voice'] = voice.strip()

    memory = result.get('memory_facts')
    if not isinstance(memory, list):
        memory = []
    result['memory_facts'] = [str(x).strip() for x in memory if str(x).strip()][:4]

    reason = result.get('reason')
    result['reason'] = str(reason).strip() if reason not in (None, '') else 'provider compatibility normalization'
    return result


def _strip_json(text):
    text = str(text or '').strip()
    if text.startswith('```'):
        lines = text.splitlines()
        if lines and lines[0].startswith('```'): lines = lines[1:]
        if lines and lines[-1].strip() == '```': lines = lines[:-1]
        text = '\n'.join(lines).strip()
    start = text.find('{'); end = text.rfind('}')
    json_text = text
    if start >= 0 and end > start:
        json_text = text[start:end+1]
    try:
        value = json.loads(json_text)
        return _normalize_result(value)
    except Exception:
        return _normalize_result({'reply': text})


def _responses_output_text(data):
    if isinstance(data.get('output_text'), str):
        return data['output_text']
    chunks = []
    for item in data.get('output') or []:
        for part in item.get('content') or []:
            if part.get('type') in {'output_text', 'text'} and part.get('text'):
                chunks.append(str(part['text']))
    return '\n'.join(chunks).strip()


REPLY_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string'},
        'voice': {'type': 'string'},
        'memory_facts': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4},
        'reason': {'type': 'string'},
    },
    'required': ['reply', 'voice', 'memory_facts', 'reason'],
    'additionalProperties': False,
}


def _openai_responses(config, system_text, user_text, image_bytes=None, image_mime='image/jpeg'):
    base = config.get('base_url') or 'https://api.openai.com/v1'
    model = config.get('model') or 'gpt-5-mini'
    user_content = [{'type': 'input_text', 'text': user_text}]
    if image_bytes:
        user_content.append({'type': 'input_image', 'image_url': f'data:{image_mime};base64,' + base64.b64encode(image_bytes).decode('ascii')})
    payload = {
        'model': model,
        'input': [
            {'role': 'system', 'content': [{'type': 'input_text', 'text': system_text}]},
            {'role': 'user', 'content': user_content},
        ],
        'text': {'format': {'type': 'json_schema', 'name': 'live_host_reply', 'schema': REPLY_SCHEMA, 'strict': True}},
    }
    if config.get('temperature') not in (None, ''):
        payload['temperature'] = float(config['temperature'])
    data = _json_request(_join(base, 'responses'), payload, _auth_headers(config), timeout=int(config.get('timeout') or 55))
    result = _strip_json(_responses_output_text(data))
    result['_raw_id'] = data.get('id', '')
    return result


def _openai_chat(config, system_text, user_text, image_bytes=None, image_mime='image/jpeg'):
    base = config.get('base_url') or 'https://api.openai.com/v1'
    model = config.get('model') or 'gpt-4o-mini'
    content = user_text
    if image_bytes:
        content = [
            {'type': 'text', 'text': user_text},
            {'type': 'image_url', 'image_url': {'url': f'data:{image_mime};base64,' + base64.b64encode(image_bytes).decode('ascii')}},
        ]
    json_contract = (
        '\n\nFORMATO OBRIGATÓRIO: retorne somente um objeto JSON com as chaves '
        'reply (string), voice (string), memory_facts (array de strings) e reason (string).'
    )
    payload = {
        'model': model,
        'messages': [{'role': 'system', 'content': system_text + json_contract}, {'role': 'user', 'content': content}],
        'response_format': {'type': 'json_object'},
        'temperature': float(config.get('temperature') or .75),
    }
    data = _json_request(_join(base, 'chat/completions'), payload, _auth_headers(config), timeout=int(config.get('timeout') or 55))
    choices = data.get('choices') or []
    if not choices:
        raise RuntimeError('Provider não retornou nenhuma resposta.')
    text = ((choices[0].get('message') or {}).get('content') or '')
    result = _strip_json(text)
    result['_raw_id'] = data.get('id', '')
    result['_routed_model'] = str((data.get('_routed_via') or {}).get('model') or data.get('model') or '')
    return result


def _ollama(config, system_text, user_text, image_bytes=None, image_mime='image/jpeg'):
    base = config.get('base_url') or 'http://127.0.0.1:11434'
    model = config.get('model') or 'llama3.2'
    user = {'role': 'user', 'content': user_text}
    if image_bytes:
        user['images'] = [base64.b64encode(image_bytes).decode('ascii')]
    payload = {'model': model, 'stream': False, 'format': 'json', 'messages': [{'role': 'system', 'content': system_text}, user]}
    data = _json_request(_join(base, 'api/chat'), payload, timeout=int(config.get('timeout') or 90))
    return _strip_json(((data.get('message') or {}).get('content') or ''))


def _webhook(config, system_text, user_text, image_bytes=None, image_mime='image/jpeg'):
    endpoint = str(config.get('endpoint_url') or '').strip()
    if not endpoint.startswith(('http://', 'https://')):
        raise RuntimeError('Provider webhook sem endpoint_url válido.')
    payload = {'system': system_text, 'prompt': user_text}
    if image_bytes:
        payload['image_base64'] = base64.b64encode(image_bytes).decode('ascii'); payload['image_mime'] = image_mime
    data = _json_request(endpoint, payload, _auth_headers(config), timeout=int(config.get('timeout') or 60))
    if isinstance(data.get('result'), dict): return _normalize_result(data['result'])
    if isinstance(data.get('reply'), str): return _normalize_result(data)
    return _strip_json(data.get('text') or json.dumps(data, ensure_ascii=False))


def _builtin(config, system_text, user_text, image_bytes=None, image_mime='image/jpeg'):
    # Fallback operacional para testar a fila sem gastar API. Não tenta fingir inteligência completa.
    line = user_text.split('MENSAGEM ESCOLHIDA:', 1)[-1].strip().splitlines()[0][:120] if 'MENSAGEM ESCOLHIDA:' in user_text else ''
    if '?' in line:
        reply = 'boa pergunta 😅 vou ficar de olho nisso aqui na live'
    else:
        reply = 'kkkk boa 😄 tô acompanhando o chat por aqui'
    return {'reply': reply, 'voice': reply, 'memory_facts': [], 'reason': 'fallback local de teste'}


def complete(provider_id, system_text, user_text, image_bytes=None, image_mime='image/jpeg'):
    provider = get_provider(provider_id) if provider_id else None
    if not provider or not provider.get('enabled'):
        return _builtin({}, system_text, user_text, image_bytes, image_mime)
    config = provider.get('config') or {}; name = provider.get('provider')
    if name == 'openai_responses': return _openai_responses(config, system_text, user_text, image_bytes, image_mime)
    if name in {'openai_compatible', 'openai_chat'}: return _openai_chat(config, system_text, user_text, image_bytes, image_mime)
    if name == 'ollama': return _ollama(config, system_text, user_text, image_bytes, image_mime)
    if name == 'webhook': return _webhook(config, system_text, user_text, image_bytes, image_mime)
    if name == 'builtin': return _builtin(config, system_text, user_text, image_bytes, image_mime)
    raise RuntimeError('Provider LLM não suportado: ' + str(name))


def vision(provider_id, prompt, image_bytes, image_mime='image/jpeg'):
    result = complete(provider_id, 'Você resume frames de uma transmissão ao vivo sem inventar detalhes. Retorne JSON.', prompt, image_bytes, image_mime)
    return str(result.get('reply') or result.get('description') or '').strip()[:1000]


def _decode_to_pcm(audio_bytes, input_format=''):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error']
    if input_format in {'wav','mp3','aac','opus','ogg'}:
        cmd += ['-f', input_format]
    cmd += ['-i', 'pipe:0', '-f', 's16le', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2', 'pipe:1']
    proc = subprocess.run(cmd, input=audio_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError('FFmpeg não conseguiu decodificar o áudio TTS: ' + proc.stderr.decode('utf-8','replace')[-1000:])
    return proc.stdout


def _openai_tts(config, text):
    base = config.get('base_url') or 'https://api.openai.com/v1'
    payload = {
        'model': config.get('model') or 'gpt-4o-mini-tts',
        'voice': config.get('voice') or 'alloy',
        'input': text,
    }
    if config.get('instructions'):
        payload['instructions'] = str(config['instructions'])[:2000]
    fmt = str(config.get('response_format') or 'wav').strip()
    if fmt:
        payload['response_format'] = fmt
    audio, ctype = _binary_request(_join(base, 'audio/speech'), payload, _auth_headers(config), timeout=int(config.get('timeout') or 90))
    return _decode_to_pcm(audio, fmt if fmt in {'wav','mp3','aac','opus'} else '')


def _piper_tts(config, text):
    model = str(config.get('model_path') or '').strip()
    if not model or not Path(model).exists():
        raise RuntimeError('Piper: model_path não encontrado.')
    cmd = [str(config.get('binary') or 'piper'), '--model', model, '--output-raw']
    if config.get('speaker') not in (None, ''):
        cmd += ['--speaker', str(config['speaker'])]
    proc = subprocess.run(cmd, input=text.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=int(config.get('timeout') or 90))
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError('Piper falhou: ' + proc.stderr.decode('utf-8','replace')[-1000:])
    # Piper raw normalmente usa a taxa do modelo; converte com taxa configurável.
    rate = int(config.get('sample_rate') or 22050)
    cmd2 = ['ffmpeg','-hide_banner','-loglevel','error','-f','s16le','-ar',str(rate),'-ac','1','-i','pipe:0','-f','s16le','-ar','44100','-ac','2','pipe:1']
    conv = subprocess.run(cmd2,input=proc.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    if conv.returncode != 0:return proc.stdout
    return conv.stdout


def _webhook_tts(config, text):
    endpoint = str(config.get('endpoint_url') or '').strip()
    if not endpoint.startswith(('http://','https://')):
        raise RuntimeError('TTS webhook sem endpoint_url válido.')
    audio, ctype = _binary_request(endpoint, {'text': text, 'voice': config.get('voice','')}, _auth_headers(config), timeout=int(config.get('timeout') or 90))
    fmt = 'wav' if 'wav' in ctype else 'mp3' if 'mpeg' in ctype else ''
    return _decode_to_pcm(audio, fmt)


def synthesize(provider_id, text):
    provider = get_provider(provider_id) if provider_id else None
    if not provider or not provider.get('enabled'):
        raise RuntimeError('Configure um provider TTS ativo antes de usar voz.')
    config = provider.get('config') or {}; name = provider.get('provider')
    if name in {'openai_tts','openai'}: return _openai_tts(config, text)
    if name == 'piper': return _piper_tts(config, text)
    if name == 'webhook_tts': return _webhook_tts(config, text)
    raise RuntimeError('Provider TTS não suportado: ' + str(name))


def test_provider(pid):
    p = get_provider(pid)
    if not p: return {'ok': False, 'message': 'Provider não encontrado.'}
    try:
        if p.get('kind') == 'tts':
            pcm = synthesize(pid, 'Teste de voz do HostStorm AI Live Host.')
            return {'ok': bool(pcm), 'message': f'TTS respondeu com {len(pcm)} bytes PCM.'}
        r = complete(
            pid,
            'Retorne somente JSON com reply, voice, memory_facts e reason.',
            'MENSAGEM ESCOLHIDA: teste do HostStorm',
        )
        reply = str(r.get('reply') or '').strip()
        routed = str(r.get('_routed_model') or '').strip()
        suffix = f' · modelo: {routed}' if routed else ''
        return {'ok': bool(reply), 'message': 'LLM respondeu: ' + reply[:180] + suffix}
    except Exception as exc:
        return {'ok': False, 'message': str(exc)}
