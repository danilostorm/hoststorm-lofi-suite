from hoststorm.streaming import StreamManager, Session


def sample_channel():
    return {
      'resolution':'1920x1080','fps':'30','video_bitrate':'4500k','shorts_video_bitrate':'3500k','audio_bitrate':'160k','preset':'veryfast','shorts_fit':'contain','audio':'',
      'destinations':{
        'kick':{'label':'Kick','rtmp_url':'rtmps://example/app','stream_key':'KEY','mode':'horizontal'},
        'youtube_shorts':{'label':'Shorts','rtmp_url':'rtmp://example/live','stream_key':'SKEY','mode':'vertical'},
      }
    }


def test_platform_isolation_builds_one_target_per_process(tmp_path, monkeypatch):
    import hoststorm.streaming as st
    video=tmp_path/'x.mp4'; video.write_bytes(b'x')
    monkeypatch.setattr(st,'VIDEOS_DIR',tmp_path)
    m=StreamManager(); ch=sample_channel(); ch['video']='x.mp4'; ch['source_mode']='local'
    s=Session(channel_id='c',run_id='r',trigger='manual',schedule_id=None,platforms=['kick'],media=['x.mp4'],started_at='',work_channel=ch)
    cmd=m._build_cmd(s,'kick')
    joined=' '.join(cmd)
    assert 'rtmps://example/app/KEY' in joined
    assert 'SKEY' not in joined

def test_vertical_filter():
    m=StreamManager(); ch=sample_channel()
    assert '1080:1920' in m._video_filter(ch,True)
