from datetime import datetime
from zoneinfo import ZoneInfo

from hoststorm.scheduler import due_info
from hoststorm.utils import normalize_time, duration_hms

TZ=ZoneInfo('America/Sao_Paulo')

def base_schedule(**kw):
    s={'id':'abc','enabled':True,'kind':'weekly','weekdays':[5],'time':'17:35','run_date':'','start_date':'','end_date':'','conflict_policy':'skip','last_run_key':''}
    s.update(kw); return s

def test_time_normalization():
    assert normalize_time('17:35')=='17:35'
    assert normalize_time('17:35:00')=='17:35'
    assert normalize_time('99:99')==''

def test_duration_format():
    assert duration_hms(17922)=='04:58:42'
    assert duration_hms(17862)=='04:57:42'

def test_weekly_due_in_brazil_timezone():
    now=datetime(2026,8,29,17,35,9,tzinfo=TZ)  # Saturday
    info=due_info(base_schedule(),now)
    assert info is not None
    assert info['lateness_seconds']==9

def test_not_due_wrong_weekday():
    now=datetime(2026,8,30,17,35,9,tzinfo=TZ)  # Sunday
    assert due_info(base_schedule(),now) is None

def test_once_schedule():
    now=datetime(2026,8,29,20,0,20,tzinfo=TZ)
    s=base_schedule(kind='once',run_date='2026-08-29',weekdays=[],time='20:00')
    assert due_info(s,now)
    s['run_date']='2026-08-30'
    assert due_info(s,now) is None
