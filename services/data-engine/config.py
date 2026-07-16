"""
TradingFirm — Data Engine Configuration

Loads settings from environment variables with sensible defaults.
Uses pydantic-settings for validation and .env file support.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Data Engine service configuration."""

    # Service identity
    service_name: str = "data-engine"
    service_port: int = 8001

    # Database (asyncpg)
    database_url: str = "postgresql+asyncpg://tf_user:tradingfirm_dev_2026@postgres:5432/tradingfirm"

    # Redis
    redis_url: str = "redis://redis:6379"

    # Data provider (yfinance for dev, fmp for prod)
    data_provider: str = "yfinance"

    # Scanner defaults
    default_price_min: float = 10.0
    default_price_max: float = 40.0

    # Debug mode
    debug: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def asyncpg_url(self) -> str:
        """Strip +asyncpg from SQLAlchemy-style URL for raw asyncpg."""
        return self.database_url.replace("+asyncpg", "")


settings = Settings()
