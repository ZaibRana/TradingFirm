"""
TradingFirm — Risk Shield Configuration

Loads settings from environment variables with sensible defaults.
Mirrors services/data-engine/config.py in shape; the two services share no
Python package, so this is a copied pattern, not imported code (Part 3.1).
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings

# Hard bound on any single dependency connection attempt at startup, in
# seconds (Part 3.1 decision 6). Fail-open is only true if the attempt
# *returns*: asyncpg's default connect timeout is 60 s and redis-py's is
# unbounded, so a Postgres that is restarting rather than refusing would
# stall this service's boot with /health unreachable.
#
# Read it as `config.STARTUP_TIMEOUT` at call time, never
# `from config import STARTUP_TIMEOUT` — a from-import copies the value and
# the lifespan tests monkeypatch this down to keep the slow paths fast.
STARTUP_TIMEOUT = 5.0

# How long shutdown waits for the cancelled scheduler task before closing
# the pool and Redis anyway (Part 3.4 decision 8). A check inside the
# yfinance thread cannot be cancelled. Read at call time, like the above.
SCHEDULER_SHUTDOWN_TIMEOUT = 5.0


class Settings(BaseSettings):
    """Risk Shield service configuration."""

    # Service identity
    service_name: str = "risk-shield"
    service_port: int = 8003

    # Database (asyncpg)
    database_url: str = "postgresql+asyncpg://tf_user:tradingfirm_dev_2026@postgres:5432/tradingfirm"

    # Redis
    redis_url: str = "redis://redis:6379"

    # FRED (Part 3.2 macro series). SecretStr so repr()/str() mask it by
    # construction — a leak needs a deliberate .get_secret_value(), not a
    # forgotten f-string (G14). Empty = the fetcher raises before any HTTP;
    # the dev twin is always empty. Nothing in 3.1 reads it beyond the
    # `fredConfigured` boolean on /health.
    fred_api_key: SecretStr = SecretStr("")

    # Regime scheduler (Part 3.4). Off unless the environment turns it on:
    # only the prod compose service does. The dev twin has no quotes fixture,
    # so a scheduler there would download from yfinance every 5 minutes.
    scheduler_enabled: bool = False

    # Pub/sub channel for health changes. Redis pub/sub ignores the DB index,
    # so the dev twin (Redis DB 1) overrides this to stay off prod's channel.
    # Default = shared/constants.py REDIS_CHANNELS["health_update"].
    health_channel: str = "tf:risk:health"

    # Debug mode
    debug: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def asyncpg_url(self) -> str:
        """Strip +asyncpg from SQLAlchemy-style URL for raw asyncpg."""
        return self.database_url.replace("+asyncpg", "")

    @property
    def fred_configured(self) -> bool:
        """Whether a FRED key is present. The only thing 3.1 asks of the
        key — the value itself never reaches a response or a log."""
        return bool(self.fred_api_key.get_secret_value())


settings = Settings()
