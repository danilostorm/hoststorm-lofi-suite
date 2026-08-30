import json
from datetime import datetime

import hoststorm.db as db
import hoststorm.pro_db as pro_db
from hoststorm.broadcast import block_active, save_block, list_blocks
from hoststorm.professional import quality_label
from hoststorm.security import encrypt_secret, decrypt_secret, hash_password, verify_password, new_api_token


def setup_db(tmp_path):
    db.DB_PATH=tmp_path/'hoststorm.db';db.LEGACY_CHANNELS_PATH=tmp_path/'channels.json';pro_db.DB_PATH=db.DB_PATH
    db.init_db();pro_db.init_pro_db('admin','secret')


def test_secret_roundtrip(monkeypatch):
    monkeypatch.setenv('HOSTSTORM_SECRET_KEY','unit-test-key')
    enc=encrypt_secret('abc123')
    assert enc.startswith('enc:v1:')
    assert decrypt_secret(enc)=='abc123'
    assert decrypt_secret('legacy-text')=='legacy-text'


def test_password_hash():
    h=hash_password('senha-forte')
    assert verify_password(h,'senha-forte')
    assert not verify_password(h,'errada')


def test_pro_users_profiles_and_tokens(tmp_path):
    setup_db(tmp_path)
    admin=pro_db.get_user_by_username('admin')
    assert admin and admin['role']=='admin'
    assert len(pro_db.list_profiles())>=5
    token=new_api_token();pro_db.create_token(admin['id'],'teste',token,['read'])
    auth=pro_db.authenticate_token(token)
    assert auth and 'read' in auth['scopes']


def test_broadcast_block_and_antirepeat_schema(tmp_path):
    setup_db(tmp_path);cid=db.create_channel('TV')
    bid=save_block({'channel_id':cid,'name':'Noite','start_time':'18:00','end_time':'23:00','weekdays':[5],'media':['a.mp4','b.mp4'],'enabled':True})
    b=[x for x in list_blocks(cid) if x['id']==bid][0]
    assert block_active(b,datetime(2026,8,29,20,0))
    assert not block_active(b,datetime(2026,8,30,20,0))


def test_quality_label():
    assert quality_label(60,60,6000,6000,1.0,0)=='excellent'
    assert quality_label(20,60,6000,6000,1.0,0)=='critical'
    assert quality_label(60,60,2000,6000,1.0,0)=='warning'
