"""Part 3.1 — config.py: defaults, env overrides, and the SecretStr guard."""

import importlib

import pytest
from pydantic import SecretStr

import config


def _reload(monkeypatch, **env):
    """Re-import config with a patched environment, so module-level
    `settings` is rebuilt from it."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(config)


def test_config_defaults(monkeypatch):
    for var in ("SERVICE_NAME", "SERVICE_PORT", "DATABASE_URL", "REDIS_URL",
                "FRED_API_KEY", "DEBUG"):
        monkeypatch.delenv(var, raising=False)
    mod = importlib.reload(config)
    s = mod.Settings(_env_file=None)
    assert s.service_name == "risk-shield"
    assert s.service_port == 8003
    assert s.redis_url == "redis://redis:6379"
    assert s.debug is False
    assert s.fred_api_key.get_secret_value() == ""
    assert s.fred_configured is False


def test_config_env_override(monkeypatch):
    mod = _reload(
        monkeypatch,
        SERVICE_NAME="risk-shield-dev",
        SERVICE_PORT="8013",
        REDIS_URL="redis://redis:6379/1",
        DEBUG="true",
    )
    assert mod.settings.service_name == "risk-shield-dev"
    assert mod.settings.service_port == 8013
    assert mod.settings.redis_url == "redis://redis:6379/1"
    assert mod.settings.debug is True


def test_asyncpg_url_strips_prefix():
    s = config.Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://tf_user:pw@postgres:5432/tradingfirm_dev",
    )
    assert s.asyncpg_url == "postgresql://tf_user:pw@postgres:5432/tradingfirm_dev"
    assert "+asyncpg" not in s.asyncpg_url


def test_config_secretstr_masks_key():
    """A leak must need a deliberate .get_secret_value(), not a forgotten
    f-string (decision 13)."""
    secret = "FRED-KEY-e7c1c0de"
    s = config.Settings(_env_file=None, fred_api_key=secret)
    assert isinstance(s.fred_api_key, SecretStr)
    assert secret not in str(s.fred_api_key)
    assert secret not in repr(s.fred_api_key)
    assert secret not in repr(s)
    assert secret not in str(s.model_dump())
    assert s.fred_api_key.get_secret_value() == secret
    assert s.fred_configured is True


def test_startup_timeout_constant():
    assert config.STARTUP_TIMEOUT == 5.0
