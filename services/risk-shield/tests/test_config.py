"""Part 3.1 — config.py: defaults, env overrides, and the SecretStr guard."""

import importlib
import json
from pathlib import Path

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
                "FRED_API_KEY", "DEBUG", "SCHEDULER_ENABLED", "HEALTH_CHANNEL"):
        monkeypatch.delenv(var, raising=False)
    mod = importlib.reload(config)
    s = mod.Settings(_env_file=None)
    assert s.service_name == "risk-shield"
    assert s.service_port == 8003
    assert s.redis_url == "redis://redis:6379"
    assert s.debug is False
    assert s.fred_api_key.get_secret_value() == ""
    assert s.fred_configured is False
    assert s.health_channel == "tf:risk:health"


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
    assert config.SCHEDULER_SHUTDOWN_TIMEOUT == 5.0     # Part 3.4 decision 8


def test_scheduler_disabled_by_default(monkeypatch):
    """Part 3.4: only an explicit SCHEDULER_ENABLED turns the scheduler on."""
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    assert config.Settings(_env_file=None).scheduler_enabled is False
    assert _reload(monkeypatch, SCHEDULER_ENABLED="true").settings.scheduler_enabled is True
    assert _reload(monkeypatch, SCHEDULER_ENABLED="false").settings.scheduler_enabled is False


def test_dockerfile_pins_single_worker():
    """Part 3.4 decision 8: two workers would be two schedulers. uvicorn's
    --workers default reads $WEB_CONCURRENCY, so every CMD pins 1."""
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    cmds = [json.loads(line[len("CMD"):].strip())
            for line in dockerfile.read_text().splitlines()
            if line.startswith("CMD [")]
    uvicorn = [c for c in cmds if c and c[0] == "uvicorn"]
    assert len(uvicorn) == 2                    # dev and prod stages
    for cmd in uvicorn:
        i = cmd.index("--workers")
        assert cmd[i + 1] == "1"


def test_news_poll_settings_defaults(monkeypatch):
    """Part 3.5: the poller is off unless the environment turns it on, it
    targets prod's data-engine service name by default, and the Finnhub key
    masks like FRED's. The env is cleared first: the twin's compose env sets
    all three (3.4's lesson — a default test must not read its container)."""
    for var in ("NEWS_POLL_ENABLED", "DATA_ENGINE_URL", "FINNHUB_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = config.Settings(_env_file=None)
    assert s.news_poll_enabled is False
    assert s.data_engine_url == "http://data-engine:8001"
    assert s.finnhub_api_key.get_secret_value() == ""
    assert s.finnhub_configured is False

    secret = "FINNHUB-KEY-3f9a7c21"
    s = config.Settings(_env_file=None, finnhub_api_key=secret)
    assert isinstance(s.finnhub_api_key, SecretStr)
    assert secret not in repr(s)
    assert secret not in str(s.model_dump())
    assert s.finnhub_configured is True

    mod = _reload(monkeypatch, NEWS_POLL_ENABLED="true", DATA_ENGINE_URL="http://data-engine-dev:8001")
    assert mod.settings.news_poll_enabled is True
    assert mod.settings.data_engine_url == "http://data-engine-dev:8001"
