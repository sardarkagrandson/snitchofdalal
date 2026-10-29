"""Application settings loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Later files override earlier ones: .env.example = operator defaults, .env = local overrides.
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env.example", PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "SnitchofDalal"
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    offline_mode: bool = False
    # Market shown by default (IN = India, ZA = South Africa); users can switch in the header.
    default_market: str = "IN"
    market_holidays: str = ""
    market_holidays_za: str = ""

    # Device licensing: empty = open access. Set to the public key from tools/license_admin.py keygen.
    license_public_key: str = ""
    # Quote source: auto (NSE live, Yahoo fallback) | yfinance
    quote_source: str = "auto"

    # Public grey-market-premium page to parse (unofficial, best effort). Empty disables.
    ipo_gmp_url: str = "https://www.investorgain.com/report/live-ipo-gmp/331/,https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"

    anthropic_api_key: str = ""
    sentiment_model: str = "claude-opus-5-5"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from: str = "alerts@example.com"
    smtp_prefer_ipv4: bool = True
    smtp_timeout_seconds: float = 25.0

    quote_cache_seconds: int = 60
    # Refresh cadence of the live price on an open stock page during market hours
    live_quote_seconds: int = 5
    history_cache_seconds: int = 900
    news_cache_seconds: int = 1800

    @property
    def resolved_data_dir(self) -> Path:
        d = self.data_dir if self.data_dir.is_absolute() else PROJECT_ROOT / self.data_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.resolved_data_dir / 'snitchofdalal.db'}"

    @property
    def jobstore_url(self) -> str:
        return f"sqlite:///{self.resolved_data_dir / 'jobs.db'}"

    @property
    def holiday_set(self) -> set[str]:
        return {h.strip() for h in self.market_holidays.split(",") if h.strip()}

    @property
    def holiday_set_za(self) -> set[str]:
        return {h.strip() for h in self.market_holidays_za.split(",") if h.strip()}

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key) and not self.offline_mode

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host)


@lru_cache
def get_settings() -> Settings:
    return Settings()
