from __future__ import annotations

import random
import re
from datetime import datetime, timezone

INJECTION_PATTERNS = [
    r'ignore\s+(all|as|todas?)\s+(previous|anteriores?|instru[cç][oõ]es)',
    r'(system|developer)\s+prompt',
    r'reveal\s+(your|the)\s+(prompt|secret)',
    r'mostre\s+(a|o)\s+(senha|token|prompt|segredo)',
    r'jailbreak',
    r'disregard\s+instructions',
    r'fa[cç]a\s+de\s+conta\s+que\s+voc[eê]\s+n[aã]o\s+tem\s+regras',
]
INJECTION_RE = re.compile('|'.join(f'(?:{x})' for x in INJECTION_PATTERNS), re.I)
URL_RE = re.compile(r'https?://\S+|www\.\S+', re.I)
QUESTION_RE = re.compile(r'\?|\b(qual|como|quando|onde|por que|porque|quem|quanto|qnt|oq|o que|ser[aá]|tem como)\b', re.I)
GREETING_RE = re.compile(r'\b(oi|ol[aá]|salve|boa noite|bom dia|boa tarde|eae|e a[ií]|fala|opa)\b', re.I)
LAUGH_RE = re.compile(r'\b(k{2,}|ha{2,}|rsrs|kkkk|lol|lmao)\b|😂|🤣|😅', re.I)
COMMAND_RE = re.compile(r'^\s*[!/][a-z0-9_-]+', re.I)
SPAM_RE = re.compile(r'(.)\1{9,}|(?:\b\w+\b)(?:\s+\1){5,}', re.I)


def classify_message(message: dict, settings: dict) -> dict:
    text = str(message.get('text') or '').strip()
    low = text.casefold()
    meta = message.get('metadata') or {}
    kind = str(message.get('kind') or 'chat')
    flags = {
        'question': bool(QUESTION_RE.search(text)),
        'greeting': bool(GREETING_RE.search(text)),
        'joke': bool(LAUGH_RE.search(text)),
        'mention': bool(meta.get('mentions_host')) or '@hoststorm' in low or '@danilo' in low,
        'subscriber': bool(meta.get('subscriber') or meta.get('member')),
        'moderator': bool(meta.get('moderator')),
        'event': kind != 'chat',
        'command': bool(COMMAND_RE.search(text)),
        'link': bool(URL_RE.search(text)),
        'prompt_injection': bool(INJECTION_RE.search(text)),
        'spam': len(text) > 800 or bool(SPAM_RE.search(text)),
    }
    blocked = flags['spam'] or flags['command']
    if settings.get('links_filter', True) and flags['link']:
        blocked = True
    if settings.get('prompt_injection_filter', True) and flags['prompt_injection']:
        blocked = True
    flags['blocked'] = blocked
    return flags


def score_message(message: dict, settings: dict, viewer: dict | None = None) -> tuple[float, dict]:
    flags = classify_message(message, settings)
    if flags['blocked'] or message.get('self_message'):
        return -1000.0, flags
    score = 5.0
    if flags['event']:
        score += 85
    if flags['question']:
        score += 42
    if flags['mention']:
        score += 32
    if flags['subscriber']:
        score += 16
    if flags['moderator']:
        score += 8
    if flags['joke']:
        score += 14
    if flags['greeting']:
        score += 5
    text = str(message.get('text') or '')
    if 8 <= len(text) <= 220:
        score += 8
    if len(text) <= 3:
        score -= 15
    interactions = int((viewer or {}).get('interactions') or 0)
    if interactions == 1:
        score += 10
    elif interactions >= 3:
        score += min(12, interactions * 1.5)
    if (viewer or {}).get('last_replied_at'):
        score -= 5
    return score, flags


def probability_for(flags: dict, settings: dict) -> float:
    if flags.get('blocked'):
        return 0.0
    if flags.get('event'):
        return float(settings.get('event_probability', .92)) if settings.get('reply_events', True) else 0.0
    values = []
    if flags.get('mention') and settings.get('reply_mentions', True):
        values.append(float(settings.get('mention_probability', .85)))
    if flags.get('question') and settings.get('reply_questions', True):
        values.append(float(settings.get('question_probability', .72)))
    if flags.get('joke') and settings.get('reply_jokes', True):
        values.append(float(settings.get('joke_probability', .48)))
    if flags.get('greeting') and settings.get('reply_greetings', True):
        values.append(float(settings.get('greeting_probability', .18)))
    return max(values or [0.12])


def weighted_pick(items: list[tuple[dict, float, dict]], rng=None):
    rng = rng or random
    candidates = [(m, s, f) for m, s, f in items if s > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    pool = candidates[:6]
    weights = [max(1.0, x[1]) ** 1.35 for x in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def humanizer_directive(settings: dict, rng=None) -> dict:
    rng = rng or random
    lengths = [('curtissima', .24), ('curta', .48), ('normal', .24), ('pergunta_de_volta', .04)]
    names, weights = zip(*lengths)
    style = rng.choices(names, weights=weights, k=1)[0]
    emoji_level = settings.get('emoji_level', 'moderate')
    emoji = rng.random() < {'none': 0.0, 'low': .18, 'moderate': .42, 'high': .72}.get(emoji_level, .42)
    return {
        'length_style': style,
        'use_emoji': emoji,
        'ask_back': settings.get('reply_questions', True) and rng.random() < (.18 if style != 'pergunta_de_volta' else .85),
        'avoid_perfect_grammar': rng.random() < .22,
    }


def sanitize_untrusted_text(text: str, max_chars=1200) -> str:
    text = str(text or '').replace('\x00', ' ').strip()
    text = re.sub(r'[\r\n\t]+', ' ', text)
    return text[:max_chars]


def safe_output(text: str, max_chars=240, signature=' 🤖') -> str:
    text = sanitize_untrusted_text(text, max_chars * 3)
    text = re.sub(r'(?i)\b(api[_ -]?key|stream[_ -]?key|client[_ -]?secret|password|senha|token)\s*[:=]\s*\S+', '[credencial protegida]', text)
    text = text.strip(' \"\'`')
    if len(text) > max_chars:
        text = text[:max_chars - 1].rstrip() + '…'
    signature = str(signature or '')[:16]
    if signature and signature.strip() not in text[-20:]:
        text = text.rstrip() + signature
    return text


def iso_age_seconds(value: str) -> float:
    if not value:
        return 10**9
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return 10**9
