from __future__ import annotations

import json
import queue
import threading
from collections import deque
from .utils import now_iso

class EventBus:
    def __init__(self):
        self._subs=[]
        self._lock=threading.Lock()
        self.recent=deque(maxlen=200)

    def publish(self, event_type: str, payload=None):
        event={'type':event_type,'at':now_iso(),'payload':payload or {}}
        self.recent.append(event)
        with self._lock:
            dead=[]
            for q in self._subs:
                try: q.put_nowait(event)
                except Exception: dead.append(q)
            for q in dead:
                if q in self._subs: self._subs.remove(q)

    def subscribe(self):
        q=queue.Queue(maxsize=100)
        with self._lock: self._subs.append(q)
        return q

    def unsubscribe(self,q):
        with self._lock:
            if q in self._subs: self._subs.remove(q)

BUS=EventBus()
