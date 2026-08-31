from __future__ import annotations

import json
import threading
import urllib.request

from .db import audit, get_setting


def _post_json(url: str, payload: dict, headers=None, timeout=8):
    if not url:
        return
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json', **(headers or {})},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read(64)


def _send(message: str):
    errors = []
    discord = get_setting('notify_discord_webhook', '').strip()
    generic = get_setting('notify_webhook', '').strip()
    tg_token = get_setting('notify_telegram_token', '').strip()
    tg_chat = get_setting('notify_telegram_chat_id', '').strip()

    try:
        if discord:
            _post_json(discord, {'content': message})
    except Exception as exc:
        errors.append('Discord: ' + str(exc))
    try:
        if generic:
            _post_json(generic, {'event': 'hoststorm', 'message': message})
    except Exception as exc:
        errors.append('Webhook: ' + str(exc))
    try:
        if tg_token and tg_chat:
            _post_json(
                f'https://api.telegram.org/bot{tg_token}/sendMessage',
                {'chat_id': tg_chat, 'text': message},
            )
    except Exception as exc:
        errors.append('Telegram: ' + str(exc))

    try:
        from .push import send_push
        send_push(message)
    except Exception as exc:
        errors.append('Web Push: ' + str(exc))

    if errors:
        audit('warning', 'notification_error', '', ' | '.join(errors))


def notify(message: str):
    if get_setting('notifications_enabled', '0') != '1':
        return
    threading.Thread(target=_send, args=(message,), daemon=True).start()
