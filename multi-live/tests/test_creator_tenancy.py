from flask import Flask, g

from hoststorm.security import role_allows
from hoststorm.tenant_channels import channel_visible_to_current_user, current_creator_id


def test_creator_rank_is_read_capable_but_not_operator():
    assert role_allows('creator', 'viewer') is True
    assert role_allows('creator', 'operator') is False
    assert role_allows('creator', 'admin') is False


def test_creator_only_sees_owned_channel():
    app = Flask(__name__)
    with app.test_request_context('/'):
        g.user = {'id': 'creator-a', 'role': 'creator'}
        assert current_creator_id() == 'creator-a'
        assert channel_visible_to_current_user({'id': 'one', 'owner_user_id': 'creator-a'}) is True
        assert channel_visible_to_current_user({'id': 'two', 'owner_user_id': 'creator-b'}) is False
        assert channel_visible_to_current_user({'id': 'legacy', 'owner_user_id': ''}) is False


def test_admin_and_operator_are_not_tenant_scoped():
    app = Flask(__name__)
    for role in ('admin', 'operator', 'viewer'):
        with app.test_request_context('/'):
            g.user = {'id': role, 'role': role}
            assert current_creator_id() == ''
            assert channel_visible_to_current_user({'id': 'any', 'owner_user_id': 'someone-else'}) is True
