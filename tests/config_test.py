"""Tests for tapest_client.config module.

Test purpose:
    Verify that Config.read() correctly loads and coerces settings
    from an INI config file and environment variables, and that
    get_config() lazily loads and caches.

Test coverage:
    - tapest_client.config: Config.read, get_config
"""

import textwrap

import pytest

from tapest_client.config import Config, get_config
import tapest_client.config as config_module


@pytest.fixture(autouse=True)
def reset_config():
    """Clear cached config so each test starts fresh."""
    config_module._config = None
    yield
    config_module._config = None


def test_read_from_file(tmp_path):
    """read() loads all keys from INI file with correct types."""
    conf = tmp_path / "client.conf"
    conf.write_text(textwrap.dedent("""\
        [tapest-client]
        ice_token = tok123
        ice_host = https://tapest.example.com
        storage_account_name = testaccount
        max_retry_attempts = 5
        default_sleep_duration = 30
        cleanup_on_fail = true
        verify_ssl = false
    """))
    cfg = Config()
    cfg.read(config_file=str(conf))
    assert cfg.ice_token == "tok123"
    assert cfg.ice_host == "https://tapest.example.com"
    assert cfg.storage_account_name == "testaccount"
    assert cfg.max_retry_attempts == 5
    assert cfg.default_sleep_duration == 30
    assert cfg.cleanup_on_fail is True
    assert cfg.verify_ssl is False


def test_env_overrides_file(tmp_path, monkeypatch):
    """Environment variables override file values."""
    conf = tmp_path / "client.conf"
    conf.write_text(textwrap.dedent("""\
        [tapest-client]
        ice_token = file-tok
        ice_host = https://file.example.com
    """))
    monkeypatch.setenv("TAPEST_CLIENT_ICE_TOKEN", "env-tok")
    monkeypatch.setenv("TAPEST_CLIENT_VERIFY_SSL", "false")
    cfg = Config()
    cfg.read(config_file=str(conf))
    assert cfg.ice_token == "env-tok"
    assert cfg.ice_host == "https://file.example.com"
    assert cfg.verify_ssl is False


def test_get_config_caches():
    """get_config() returns the same instance on repeated calls."""
    first = get_config()
    second = get_config()
    assert first is second
