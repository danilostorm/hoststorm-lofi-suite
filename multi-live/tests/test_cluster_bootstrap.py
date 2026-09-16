import pytest

from hoststorm.cluster_bootstrap import BootstrapError, _safe_host, _safe_user, _validate_base_url


def test_safe_host_accepts_ip_and_dns():
    assert _safe_host('192.168.30.50') == '192.168.30.50'
    assert _safe_host('node.example.com') == 'node.example.com'


def test_safe_host_rejects_shell_injection():
    with pytest.raises(BootstrapError):
        _safe_host('1.2.3.4; rm -rf /')


def test_safe_user_rejects_invalid_characters():
    with pytest.raises(BootstrapError):
        _safe_user('root;whoami')


def test_base_url_defaults_to_agent_port():
    assert _validate_base_url('', '10.0.0.5', 3040) == 'http://10.0.0.5:3040'


def test_base_url_requires_http_protocol():
    with pytest.raises(BootstrapError):
        _validate_base_url('ssh://10.0.0.5', '10.0.0.5', 3040)
