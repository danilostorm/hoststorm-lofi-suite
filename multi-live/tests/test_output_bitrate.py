from hoststorm.output_bitrate import enforce_cbr, inherited_k, target_bitrate_k, youtube_recommended_k


def test_youtube_shorts_recommendation_uses_vertical_1080p30():
    channel = {'fps': '30', 'resolution': '1280x720', 'shorts_video_bitrate': '3000k'}
    destination = {'platform': 'youtube_shorts', 'is_extra': True}
    assert youtube_recommended_k(channel, 'youtube_shorts__abc', destination) == 10000
    assert target_bitrate_k(channel, 'youtube_shorts__abc', destination) == 10000


def test_output_can_inherit_or_override_bitrate():
    channel = {'fps': '30', 'shorts_video_bitrate': '3000k', 'video_bitrate': '4500k'}
    inherited = {'platform': 'youtube_shorts', 'output_bitrate_mode': 'inherit'}
    custom = {'platform': 'youtube_shorts', 'output_bitrate_mode': 'custom', 'output_video_bitrate_k': 6500}
    assert inherited_k(channel, 'youtube_shorts__a', inherited) == 3000
    assert target_bitrate_k(channel, 'youtube_shorts__a', inherited) == 3000
    assert target_bitrate_k(channel, 'youtube_shorts__b', custom) == 6500


def test_cbr_rewrites_rate_and_keeps_options_on_output_side():
    cmd = [
        'ffmpeg', '-re', '-f', 'concat', '-safe', '0', '-i', '/tmp/list.txt',
        '-c:v', 'libx264', '-b:v', '3000k', '-maxrate', '3000k', '-bufsize', '6000k',
        '-f', 'flv', 'rtmp://example/live',
    ]
    out = enforce_cbr(cmd, 10000)
    assert out[out.index('-b:v') + 1] == '10000k'
    assert out[out.index('-minrate') + 1] == '10000k'
    assert out[out.index('-maxrate') + 1] == '10000k'
    assert out[out.index('-bufsize') + 1] == '20000k'
    assert out[out.index('-x264-params') + 1] == 'nal-hrd=cbr:force-cfr=1'
    assert out.index('-minrate') > out.index('/tmp/list.txt')
    assert out.index('-minrate') < max(i for i, token in enumerate(out) if token == '-f')
