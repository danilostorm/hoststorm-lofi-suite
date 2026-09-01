from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .ai_db import (
    get_binding, ingest_message, list_bindings, record_sent, set_binding_status,
    upsert_event_subscription,
)
from .integrations import get_integration, list_integrations
from .kick_integration_v33 import ensure_kick_token

TWITCH_WS_URL = 'wss://eventsub.wss.twitch.tv/ws'
TWITCH_API = 'https://api.twitch.tv/helix'
KICK_API = 'https://api.kick.com'
YOUTUBE_API = 'https://www.googleapis.com/youtube/v3'
_KICK_PUBLIC_KEY = {'at': 0.0, 'value': None}


def _request(url, headers=None, method='GET', payload=None, timeout=20):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    hdr = {'Accept': 'application/json', **(headers or {})}
    if data is not None: hdr.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace').strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace') if hasattr(exc, 'read') else ''
        raise RuntimeError(f'HTTP {exc.code}: {(raw or str(exc))[-1600:]}') from exc


def _integration(iid, provider=None):
    item = get_integration(iid)
    if not item or not item.get('enabled'):
        raise RuntimeError('Integração desativada ou inexistente.')
    if provider and item.get('provider') != provider:
        raise RuntimeError(f'Integração não pertence a {provider}.')
    return item


def _twitch_headers(config):
    cid = str(config.get('client_id') or '').strip(); token = str(config.get('access_token') or '').strip()
    if not cid or not token: raise RuntimeError('Twitch exige Client ID e User Access Token.')
    return {'Client-Id': cid, 'Authorization': 'Bearer ' + token}


def _twitch_identity(config):
    headers = _twitch_headers(config)
    me = _request(TWITCH_API + '/users', headers).get('data') or []
    if not me: raise RuntimeError('Twitch não retornou a identidade do token.')
    sender_id = str(me[0].get('id') or '')
    login = str(config.get('channel_login') or me[0].get('login') or '').strip()
    broadcaster = _request(TWITCH_API + '/users?login=' + urllib.parse.quote(login), headers).get('data') or []
    if not broadcaster: raise RuntimeError('Twitch: broadcaster não encontrado.')
    return sender_id, str(broadcaster[0].get('id') or ''), login


def _twitch_subscribe(iid, config, session_id, sub_type, version, condition):
    payload = {'type': sub_type, 'version': version, 'condition': condition, 'transport': {'method': 'websocket', 'session_id': session_id}}
    try:
        data = _request(TWITCH_API + '/eventsub/subscriptions', _twitch_headers(config), 'POST', payload)
        rows = data.get('data') or []; remote = str((rows[0] if rows else {}).get('id') or '')
        upsert_event_subscription('twitch', iid, sub_type, remote, 'enabled', '')
        return True
    except Exception as exc:
        upsert_event_subscription('twitch', iid, sub_type, '', 'error', str(exc)); return False


def _twitch_event_text(sub_type, event):
    if sub_type == 'channel.follow':
        return f"{event.get('user_name') or 'Alguém'} seguiu o canal!", 'follow'
    if sub_type == 'channel.chat.notification':
        notice = str(event.get('notice_type') or 'notification')
        name = event.get('chatter_user_name') or event.get('chatter_user_login') or 'viewer'
        if notice in {'sub','resub'}: return f'{name} se inscreveu/renovou a inscrição!', 'subscription'
        if 'gift' in notice: return f'{name} presenteou inscrição(ões) no canal!', 'gift'
        msg = ((event.get('message') or {}).get('text') or '').strip()
        return msg or f'Evento do chat: {notice}', notice
    return '', sub_type


class TwitchWorker:
    def __init__(self, hub, integration_id):
        self.hub=hub; self.iid=integration_id; self.stop_event=threading.Event(); self.ws=None; self.sender_id=''; self.broadcaster_id=''

    def stop(self):
        self.stop_event.set()
        try:
            if self.ws:self.ws.close()
        except Exception:pass

    def run(self):
        try:
            import websocket
        except Exception as exc:
            set_binding_status(self.iid, 'error', 'websocket-client ausente: '+str(exc)); return
        backoff=2
        while not self.stop_event.is_set():
            try:
                item=_integration(self.iid,'twitch'); config=item.get('config') or {}
                self.sender_id,self.broadcaster_id,_=_twitch_identity(config)
                def on_message(ws, raw):
                    try:self._message(config,json.loads(raw))
                    except Exception as exc:set_binding_status(self.iid,'warning',str(exc))
                def on_error(ws, err):set_binding_status(self.iid,'reconnecting',str(err))
                def on_open(ws):set_binding_status(self.iid,'connecting','')
                self.ws=websocket.WebSocketApp(TWITCH_WS_URL,on_message=on_message,on_error=on_error,on_open=on_open)
                self.ws.run_forever(ping_interval=20,ping_timeout=10)
            except Exception as exc:set_binding_status(self.iid,'error',str(exc))
            if self.stop_event.wait(backoff):break
            backoff=min(60,backoff*2)

    def _message(self, config, envelope):
        meta=envelope.get('metadata') or {}; payload=envelope.get('payload') or {}; mtype=meta.get('message_type')
        if mtype=='session_welcome':
            sid=str(((payload.get('session') or {}).get('id') or ''))
            if not sid:return
            cond={'broadcaster_user_id':self.broadcaster_id,'user_id':self.sender_id}
            ok=_twitch_subscribe(self.iid,config,sid,'channel.chat.message','1',cond)
            _twitch_subscribe(self.iid,config,sid,'channel.chat.notification','1',cond)
            # Follow exige moderator:read:followers. Se o token não tiver o scope, apenas esse evento fica indisponível.
            _twitch_subscribe(self.iid,config,sid,'channel.follow','2',{'broadcaster_user_id':self.broadcaster_id,'moderator_user_id':self.sender_id})
            set_binding_status(self.iid,'online' if ok else 'partial',''); return
        if mtype=='session_reconnect':
            url=str(((payload.get('session') or {}).get('reconnect_url') or ''))
            if url:
                try:self.ws.url=url;self.ws.close()
                except Exception:pass
            return
        if mtype!='notification':return
        sub=(payload.get('subscription') or {}); event=payload.get('event') or {}; stype=str(sub.get('type') or '')
        binding=get_binding(self.iid) or {}; channel_id=binding.get('channel_id','')
        if stype=='channel.chat.message':
            msg=event.get('message') or {}; text=str(msg.get('text') or '').strip(); uid=str(event.get('chatter_user_id') or '')
            ingest_message('twitch',self.iid,str(event.get('message_id') or meta.get('message_id') or ''),channel_id,uid,event.get('chatter_user_name') or event.get('chatter_user_login') or '',text,'chat',{
                'display_name':event.get('chatter_user_name') or '', 'color':event.get('color') or '', 'badges':event.get('badges') or [],
                'subscriber':any(str(x.get('set_id') or '').lower() in {'subscriber','founder'} for x in event.get('badges') or []),
                'moderator':any(str(x.get('set_id') or '').lower() in {'moderator','broadcaster'} for x in event.get('badges') or []),
            },self_message=(uid==self.sender_id));return
        text,kind=_twitch_event_text(stype,event)
        if text:
            uid=str(event.get('chatter_user_id') or event.get('user_id') or '')
            ingest_message('twitch',self.iid,str(meta.get('message_id') or ''),channel_id,uid,event.get('chatter_user_name') or event.get('user_name') or '',text,kind,{'event':event},self_message=(uid==self.sender_id))


def _youtube_headers(config):
    token=str(config.get('access_token') or '').strip()
    if not token:raise RuntimeError('YouTube AI Chat exige OAuth Access Token.')
    return {'Authorization':'Bearer '+token,'Accept':'application/json'}


def _youtube_live_chat(config):
    headers=_youtube_headers(config);base=YOUTUBE_API+'/liveBroadcasts?'
    for status in ('active','upcoming'):
        params={'part':'id,snippet,status','broadcastStatus':status,'mine':'true','maxResults':10}
        rows=(_request(base+urllib.parse.urlencode(params),headers).get('items') or [])
        for row in rows:
            chat=str((row.get('snippet') or {}).get('liveChatId') or '')
            if chat:return chat,str(row.get('id') or ''),str((row.get('snippet') or {}).get('title') or '')
    return '','',''


class YouTubeWorker:
    def __init__(self,hub,integration_id):self.hub=hub;self.iid=integration_id;self.stop_event=threading.Event();self.page='';self.chat_id='';self.seen=set();self.initial=True
    def stop(self):self.stop_event.set()
    def run(self):
        delay=8
        while not self.stop_event.is_set():
            try:
                item=_integration(self.iid,'youtube');cfg=item.get('config') or {};binding=get_binding(self.iid) or {};self.chat_id,_,_=_youtube_live_chat(cfg)
                if not self.chat_id:set_binding_status(self.iid,'waiting','Nenhum liveChat ativo.');self.stop_event.wait(30);continue
                params={'liveChatId':self.chat_id,'part':'id,snippet,authorDetails','maxResults':200}
                if self.page:params['pageToken']=self.page
                data=_request(YOUTUBE_API+'/liveChat/messages?'+urllib.parse.urlencode(params),_youtube_headers(cfg));items=data.get('items') or [];self.page=str(data.get('nextPageToken') or self.page);delay=max(2,min(15,int(data.get('pollingIntervalMillis') or 5000)/1000))
                for row in items:
                    rid=str(row.get('id') or '');
                    if not rid or rid in self.seen:continue
                    self.seen.add(rid);snippet=row.get('snippet') or {};author=row.get('authorDetails') or {};kind=str(snippet.get('type') or 'textMessageEvent')
                    text=str(snippet.get('displayMessage') or '').strip();published=str(snippet.get('publishedAt') or '')
                    if self.initial and published:
                        try:
                            dt=datetime.fromisoformat(published.replace('Z','+00:00'))
                            if (datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()>90:continue
                        except Exception:pass
                    meta={'display_name':author.get('displayName') or '', 'subscriber':bool(author.get('isChatSponsor')), 'moderator':bool(author.get('isChatModerator')), 'owner':bool(author.get('isChatOwner')), 'published_at':published}
                    if kind!='textMessageEvent':meta['event_type']=kind
                    ingest_message('youtube',self.iid,rid,binding.get('channel_id',''),str(author.get('channelId') or ''),author.get('displayName') or '',text or _youtube_event_text(kind,author,snippet), 'chat' if kind=='textMessageEvent' else kind,meta,self_message=bool(author.get('isChatOwner')))
                self.initial=False;set_binding_status(self.iid,'online','')
                if len(self.seen)>4000:self.seen=set(list(self.seen)[-2000:])
            except Exception as exc:set_binding_status(self.iid,'error',str(exc));delay=20
            self.stop_event.wait(delay)


def _youtube_event_text(kind,author,snippet):
    name=author.get('displayName') or 'viewer'
    low=kind.lower()
    if 'superchat' in low:return f'{name} enviou um Super Chat!'
    if 'supersticker' in low:return f'{name} enviou um Super Sticker!'
    if 'membership' in low or 'member' in low:return f'{name} virou/renovou membro do canal!'
    return f'Evento do YouTube: {kind}'


def _kick_public_key():
    now=time.time()
    if _KICK_PUBLIC_KEY['value'] is not None and now-_KICK_PUBLIC_KEY['at']<21600:return _KICK_PUBLIC_KEY['value']
    data=_request(KICK_API+'/public/v1/public-key');value=data.get('data') if isinstance(data,dict) else data
    if isinstance(value,dict):pem=value.get('public_key') or value.get('publicKey') or value.get('key') or ''
    elif isinstance(value,list) and value:pem=(value[0] or {}).get('public_key') or (value[0] or {}).get('key') or ''
    else:pem=str(value or '')
    if not pem:raise RuntimeError('Kick não retornou a chave pública dos webhooks.')
    key=serialization.load_pem_public_key(pem.encode('utf-8'));_KICK_PUBLIC_KEY.update({'at':now,'value':key});return key


def verify_kick_webhook(headers,raw_body):
    mid=str(headers.get('Kick-Event-Message-Id') or headers.get('kick-event-message-id') or '')
    ts=str(headers.get('Kick-Event-Message-Timestamp') or headers.get('kick-event-message-timestamp') or '')
    sig=str(headers.get('Kick-Event-Signature') or headers.get('kick-event-signature') or '')
    if not mid or not ts or not sig:return False
    signed=(mid+'.'+ts+'.').encode('utf-8')+raw_body
    try:
        _kick_public_key().verify(base64.b64decode(sig),signed,padding.PKCS1v15(),hashes.SHA256());return True
    except Exception:return False


def _kick_find_integration(payload,headers):
    ids=[]
    for value in (
        headers.get('Kick-Event-Broadcaster-User-Id'),payload.get('broadcaster_user_id'),
        (payload.get('broadcaster') or {}).get('user_id'),(payload.get('broadcaster') or {}).get('id'),
    ):
        if value not in (None,''):ids.append(str(value))
    kicks=[]
    for item in list_integrations(mask=False):
        if item.get('enabled') and item.get('provider')=='kick':
            cfg=item.get('config') or {};known={str(cfg.get('broadcaster_user_id') or ''),str(cfg.get('kick_user_id') or '')}
            if any(x and x in known for x in ids):return item
            kicks.append(item)
    return kicks[0] if len(kicks)==1 else None


def ingest_kick_webhook(headers,raw_body):
    if not verify_kick_webhook(headers,raw_body):raise PermissionError('Assinatura do webhook Kick inválida.')
    payload=json.loads(raw_body.decode('utf-8','replace') or '{}');event_type=str(headers.get('Kick-Event-Type') or headers.get('kick-event-type') or payload.get('event_type') or '')
    item=_kick_find_integration(payload,headers)
    if not item:raise RuntimeError('Webhook Kick recebido, mas nenhuma integração correspondente foi encontrada.')
    iid=item['id'];binding=get_binding(iid) or {};cfg=item.get('config') or {};sender=payload.get('sender') or payload.get('user') or payload.get('follower') or {};uid=str(sender.get('user_id') or sender.get('id') or '')
    name=str(sender.get('username') or sender.get('name') or sender.get('display_name') or '')
    message=payload.get('message') or {};text=str(message.get('content') or message.get('text') or payload.get('content') or '').strip();kind='chat'
    if event_type!='chat.message.sent':
        kind=event_type or 'event';text=_kick_event_text(event_type,payload,name)
    own=str(cfg.get('kick_user_id') or cfg.get('broadcaster_user_id') or '')
    return ingest_message('kick',iid,str(headers.get('Kick-Event-Message-Id') or payload.get('message_id') or ''),binding.get('channel_id',''),uid,name,text,kind,{'event_type':event_type,'payload':payload,'display_name':name},self_message=bool(uid and own and uid==own))


def _kick_event_text(event_type,payload,name=''):
    name=name or str((payload.get('user') or {}).get('username') or (payload.get('subscriber') or {}).get('username') or 'Alguém')
    if event_type=='channel.followed':return f'{name} seguiu o canal!'
    if event_type in {'channel.subscription.new','channel.subscription.renewal'}:return f'{name} se inscreveu/renovou a inscrição!'
    if event_type=='channel.subscription.gifts':return f'{name} presenteou inscrição(ões) no canal!'
    if event_type=='kicks.gifted':return f'{name} enviou KICKs de presente!'
    return f'Evento Kick: {event_type}'


def sync_kick_subscriptions(iid):
    item=ensure_kick_token(iid);cfg=item.get('config') or {};token=str(cfg.get('access_token') or '').strip()
    if not token:raise RuntimeError('Kick sem access token.')
    events=['chat.message.sent','channel.followed','channel.subscription.new','channel.subscription.renewal','channel.subscription.gifts','kicks.gifted']
    payload={'events':[{'name':name,'version':1} for name in events],'method':'webhook'}
    try:
        data=_request(KICK_API+'/public/v1/events/subscriptions',{'Authorization':'Bearer '+token,'Accept':'application/json'},'POST',payload)
        for name in events:upsert_event_subscription('kick',iid,name,'','requested','')
        set_binding_status(iid,'subscribed','');return data
    except Exception as exc:
        for name in events:upsert_event_subscription('kick',iid,name,'','error',str(exc))
        raise


def _kick_send(iid,text):
    item=ensure_kick_token(iid);cfg=item.get('config') or {};token=str(cfg.get('access_token') or '').strip()
    body={'content':text[:500],'type':'user'}
    bid=str(cfg.get('broadcaster_user_id') or '').strip()
    if bid:body['broadcaster_user_id']=int(bid) if bid.isdigit() else bid
    return _request(KICK_API+'/public/v1/chat',{'Authorization':'Bearer '+token,'Accept':'application/json'},'POST',body)


def _twitch_send(iid,text):
    item=_integration(iid,'twitch');cfg=item.get('config') or {};sender,broadcaster,_=_twitch_identity(cfg)
    return _request(TWITCH_API+'/chat/messages',_twitch_headers(cfg),'POST',{'broadcaster_id':broadcaster,'sender_id':sender,'message':text[:500]})


def _youtube_send(iid,text):
    item=_integration(iid,'youtube');cfg=item.get('config') or {};chat,_,_=_youtube_live_chat(cfg)
    if not chat:raise RuntimeError('YouTube sem liveChat ativo.')
    payload={'snippet':{'liveChatId':chat,'type':'textMessageEvent','textMessageDetails':{'messageText':text[:200]}}}
    return _request(YOUTUBE_API+'/liveChat/messages?part=snippet',_youtube_headers(cfg),'POST',payload)


def send_chat(integration_id,platform,text):
    binding=get_binding(integration_id)
    if binding and not binding.get('write_chat'):raise RuntimeError('Envio de chat desativado nesta conexão do AI Host.')
    if platform=='kick':data=_kick_send(integration_id,text)
    elif platform=='twitch':data=_twitch_send(integration_id,text)
    elif platform=='youtube':data=_youtube_send(integration_id,text)
    else:raise RuntimeError('Plataforma de chat não suportada: '+str(platform))
    external=''
    if isinstance(data,dict):
        rows=data.get('data') or []
        if rows and isinstance(rows[0],dict):external=str(rows[0].get('message_id') or rows[0].get('id') or '')
        external=external or str(data.get('id') or '')
    record_sent(platform,integration_id,text,external);return {'ok':True,'external_id':external,'response':data}


class ChatHub:
    def __init__(self):self.lock=threading.RLock();self.workers={};self.started=False;self.stop_event=threading.Event()
    def start(self):
        if self.started:return
        self.started=True;threading.Thread(target=self._supervisor,daemon=True,name='ai-chat-supervisor').start()
    def stop(self):
        self.stop_event.set()
        with self.lock:
            for worker in self.workers.values():worker.stop()
    def _supervisor(self):
        while not self.stop_event.is_set():
            try:self.sync_workers()
            except Exception:pass
            self.stop_event.wait(15)
    def sync_workers(self):
        wanted={}
        for b in list_bindings():
            if not b.get('enabled') or not b.get('read_chat'):continue
            item=get_integration(b['integration_id'])
            if item and item.get('enabled') and item.get('provider') in {'twitch','youtube'}:wanted[b['integration_id']]=item.get('provider')
        with self.lock:
            for iid in list(self.workers):
                if iid not in wanted:self.workers.pop(iid).stop()
            for iid,provider in wanted.items():
                if iid in self.workers:continue
                worker=TwitchWorker(self,iid) if provider=='twitch' else YouTubeWorker(self,iid);self.workers[iid]=worker;threading.Thread(target=worker.run,daemon=True,name=f'ai-chat-{provider}-{iid}').start()
    def status(self):
        bindings={b['integration_id']:b for b in list_bindings()};out=[]
        for item in list_integrations(mask=True):
            if item.get('provider') not in {'kick','twitch','youtube'}:continue
            b=bindings.get(item['id']) or {};out.append({'id':item['id'],'provider':item['provider'],'name':item.get('name'),'enabled':bool(b.get('enabled')),'channel_id':b.get('channel_id',''),'read_chat':bool(b.get('read_chat')),'write_chat':bool(b.get('write_chat')),'read_events':bool(b.get('read_events')),'status':b.get('last_status',''),'error':b.get('last_error','')})
        return out


CHAT_HUB=ChatHub()
