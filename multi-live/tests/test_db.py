import json
from pathlib import Path

import hoststorm.db as db


def reset_paths(tmp_path):
    db.DB_PATH=tmp_path/'hoststorm.db'
    db.LEGACY_CHANNELS_PATH=tmp_path/'channels.json'


def test_create_channel_and_destinations(tmp_path):
    reset_paths(tmp_path); db.init_db()
    cid=db.create_channel('Teste')
    ch=db.get_channel(cid)
    assert ch['name']=='Teste'
    assert 'youtube' in ch['destinations']
    assert 'twitch' in ch['destinations']


def test_legacy_migration_preserves_schedule_and_platforms(tmp_path):
    reset_paths(tmp_path)
    legacy={
      'kick1':{
        'name':'Kick','video':'mk2.webm','desired_running':False,
        'destinations':{
          'kick':{'label':'Kick','enabled':True,'rtmp_url':'rtmps://example/app','stream_key':'secret','dedicated':True},
          'youtube':{'label':'YouTube','enabled':False,'rtmp_url':'rtmp://a.rtmp.youtube.com/live2','stream_key':''}
        },
        'schedules':[{'id':'s1','weekday':5,'time':'17:35','video':'mk2.webm','enabled':True,'platforms':['kick']}]
      }
    }
    db.LEGACY_CHANNELS_PATH.write_text(json.dumps(legacy),encoding='utf-8')
    db.init_db()
    ch=db.get_channel('kick1')
    assert ch['destinations']['kick']['stream_key']=='secret'
    assert ch['schedules'][0]['platforms']==['kick']
    assert ch['schedules'][0]['media']==['mk2.webm']
    assert (tmp_path/'channels.json.pre-v2-backup').exists()


def test_schedule_requires_media_and_platform(tmp_path):
    reset_paths(tmp_path); db.init_db(); cid=db.create_channel('X')
    try:
        db.save_schedule({'channel_id':cid,'time':'12:00','weekdays':[0],'platforms':[],'media':['a.mp4']})
        assert False
    except ValueError:
        pass
    try:
        db.save_schedule({'channel_id':cid,'time':'12:00','weekdays':[0],'platforms':['kick'],'media':[]})
        assert False
    except ValueError:
        pass
