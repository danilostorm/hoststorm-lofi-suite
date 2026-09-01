from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from types import MethodType

from .ai_db import get_settings
from .config import TMP_DIR

SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_BYTES = 2
CHUNK_MS = 20
CHUNK_BYTES = int(SAMPLE_RATE * CHANNELS * SAMPLE_BYTES * CHUNK_MS / 1000)
SILENCE = b'\x00' * CHUNK_BYTES


class VoiceInjector:
    def __init__(self, channel_id, slug):
        self.channel_id=channel_id;self.slug=slug;self.path=TMP_DIR/f'ai-voice-{channel_id}-{slug}.pcm';self.queue=queue.Queue(maxsize=20);self.stop_event=threading.Event();self.thread=None
    def start(self):
        TMP_DIR.mkdir(parents=True,exist_ok=True)
        try:
            if self.path.exists() or self.path.is_fifo():self.path.unlink()
        except Exception:pass
        os.mkfifo(self.path,0o600);self.thread=threading.Thread(target=self._run,daemon=True,name=f'ai-voice-{self.channel_id}-{self.slug}');self.thread.start();return self.path
    def enqueue(self,pcm):
        if not pcm:return False
        try:self.queue.put_nowait(bytes(pcm));return True
        except queue.Full:
            try:self.queue.get_nowait()
            except Exception:pass
            try:self.queue.put_nowait(bytes(pcm));return True
            except Exception:return False
    def _write_all(self,fd,data):
        view=memoryview(data);pos=0
        while pos<len(view) and not self.stop_event.is_set():
            n=os.write(fd,view[pos:pos+CHUNK_BYTES]);pos+=n
    def _run(self):
        while not self.stop_event.is_set():
            fd=None
            try:
                # FIFO open bloqueia até o FFmpeg conectar; fica em thread própria para não atrasar o start.
                fd=os.open(self.path,os.O_WRONLY)
                while not self.stop_event.is_set():
                    try:audio=self.queue.get_nowait()
                    except queue.Empty:audio=None
                    if audio:
                        self._write_all(fd,audio)
                    else:
                        os.write(fd,SILENCE);time.sleep(CHUNK_MS/1000)
            except (BrokenPipeError,OSError):
                if self.stop_event.wait(.4):break
            finally:
                if fd is not None:
                    try:os.close(fd)
                    except Exception:pass
    def stop(self):
        self.stop_event.set()
        try:
            # abre o FIFO de forma não bloqueante apenas para soltar um open pendente.
            fd=os.open(self.path,os.O_RDONLY|os.O_NONBLOCK);os.close(fd)
        except Exception:pass
        try:self.path.unlink(missing_ok=True)
        except Exception:pass


class VoiceBus:
    def __init__(self):self.lock=threading.RLock();self.injectors={}
    def ensure(self,channel_id,slug):
        key=(channel_id,slug)
        with self.lock:
            injector=self.injectors.get(key)
            if injector and not injector.stop_event.is_set():return injector
            injector=VoiceInjector(channel_id,slug);injector.start();self.injectors[key]=injector;return injector
    def play(self,channel_id,pcm):
        with self.lock:targets=[inj for (cid,_),inj in self.injectors.items() if cid==channel_id and not inj.stop_event.is_set()]
        sent=0
        for injector in targets:
            if injector.enqueue(pcm):sent+=1
        return sent
    def active(self,channel_id=''):
        with self.lock:return [{'channel_id':cid,'platform':slug,'path':str(inj.path)} for (cid,slug),inj in self.injectors.items() if not inj.stop_event.is_set() and (not channel_id or cid==channel_id)]
    def stop_channel(self,channel_id):
        with self.lock:keys=[k for k in self.injectors if k[0]==channel_id];items=[self.injectors.pop(k) for k in keys]
        for inj in items:inj.stop()
    def stop_all(self):
        with self.lock:items=list(self.injectors.values());self.injectors.clear()
        for inj in items:inj.stop()


VOICE_BUS=VoiceBus()


def _count_inputs(cmd, before):
    return sum(1 for i,x in enumerate(cmd[:before]) if x=='-i')


def _extract_maps(cmd):
    maps=[];out=[];i=0
    while i<len(cmd):
        if cmd[i]=='-map' and i+1<len(cmd):maps.append(str(cmd[i+1]));i+=2;continue
        out.append(cmd[i]);i+=1
    return maps,out


def _voice_filter(base_audio,voice_idx,volume,duck):
    base_audio=base_audio.replace('?','')
    ratio=1.0+max(0.0,min(1.0,float(duck)))*14.0
    vol=max(0.0,min(3.0,float(volume)))
    return (
        f'[{base_audio}]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[hsbasea];'
        f'[{voice_idx}:a:0]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume={vol:.3f}[hsvoice0];'
        f'[hsvoice0]asplit=2[hsside][hsvoice];'
        f'[hsbasea][hsside]sidechaincompress=threshold=0.008:ratio={ratio:.2f}:attack=25:release=650[hsduck];'
        f'[hsduck][hsvoice]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[aout]'
    )


def install_ai_voice(manager,streaming_module=None):
    original_build=manager._build_cmd;original_stop=manager.stop

    def build_cmd(self,session,slug):
        cmd=original_build(session,slug);settings=get_settings()
        if not settings.get('enabled') or not settings.get('tts_enabled'):
            return cmd
        try:
            first_map=cmd.index('-map')
        except ValueError:
            return cmd
        # O input de voz é adicionado antes dos maps/opções de saída. Um FIFO por plataforma evita
        # que dois processos FFmpeg concorram pelos mesmos samples.
        injector=VOICE_BUS.ensure(session.channel_id,slug);voice_idx=_count_inputs(cmd,first_map)
        voice_input=['-thread_queue_size','512','-f','s16le','-ar',str(SAMPLE_RATE),'-ac',str(CHANNELS),'-i',str(injector.path)]
        cmd=cmd[:first_map]+voice_input+cmd[first_map:]
        maps,cmd=_extract_maps(cmd)
        audio_map=next((m for m in maps if ':a' in m),'0:a:0').replace('?','')
        try:insert_at=cmd.index('-vf')
        except ValueError:
            try:insert_at=cmd.index('-c:v')
            except ValueError:insert_at=max(1,len(cmd)-3)
        graph=_voice_filter(audio_map,voice_idx,settings.get('tts_volume',1.0),settings.get('ducking_strength',.55))
        cmd[insert_at:insert_at]=['-map','0:v:0','-filter_complex',graph,'-map','[aout]']
        session.ai_voice_enabled=True
        return cmd

    def stop(self,cid,*args,**kwargs):
        try:return original_stop(cid,*args,**kwargs)
        finally:VOICE_BUS.stop_channel(cid)

    manager._build_cmd=MethodType(build_cmd,manager);manager.stop=MethodType(stop,manager);return manager
