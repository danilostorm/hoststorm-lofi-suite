const CACHE='hoststorm-pro-v3';
const CORE=['/static/app.css','/static/app.js'];

self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(CORE)).then(()=>self.skipWaiting()));
});

self.addEventListener('activate',event=>{
  event.waitUntil(
    caches.keys()
      .then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key))))
      .then(()=>self.clients.claim())
  );
});

self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET'||new URL(event.request.url).origin!==location.origin)return;
  event.respondWith(
    fetch(event.request)
      .then(response=>{
        const copy=response.clone();
        caches.open(CACHE).then(cache=>cache.put(event.request,copy));
        return response;
      })
      .catch(()=>caches.match(event.request))
  );
});

self.addEventListener('push',event=>{
  let payload={title:'HostStorm',body:'Novo alerta operacional.',url:'/professional/alerts'};
  try{payload={...payload,...event.data.json()};}
  catch(_){try{payload.body=event.data.text()||payload.body;}catch(__){}}
  event.waitUntil(
    self.registration.showNotification(payload.title||'HostStorm',{
      body:payload.body||'',
      icon:'/static/icon.svg',
      badge:'/static/icon.svg',
      data:{url:payload.url||'/professional/alerts'},
      tag:'hoststorm-alert',
      renotify:true
    })
  );
});

self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const target=(event.notification.data&&event.notification.data.url)||'/professional';
  event.waitUntil(
    clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>{
      for(const client of list){
        if('focus' in client){
          client.navigate(target);
          return client.focus();
        }
      }
      return clients.openWindow(target);
    })
  );
});
