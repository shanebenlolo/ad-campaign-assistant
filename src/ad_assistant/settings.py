from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from dotenv import load_dotenv


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _int_env(name: str, default: int) -> int:
    raw = _clean(os.getenv(name))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    raw = _clean(os.getenv(name))
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _csv_env(name: str) -> list[str]:
    raw = _clean(os.getenv(name))
    if raw is None:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def first_non_empty(values: Iterable[str | None]) -> str | None:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return None


@dataclass(frozen=True)
class Settings:
    google_ads_developer_token: str | None
    google_ads_client_id: str | None
    google_ads_client_secret: str | None
    google_ads_refresh_token: str | None
    google_ads_login_customer_id: str | None
    google_ads_customer_id: str | None
    google_ads_api_version: str | None
    google_ads_use_proto_plus: bool

    anthropic_api_key: str | None
    anthropic_model: str
    anthropic_max_tokens: int
    anthropic_temperature: float
    anthropic_max_input_chars: int

    serpapi_api_key: str | None
    serpapi_max_queries: int
    serpapi_location: str | None
    serpapi_gl: str
    serpapi_hl: str
    serpapi_google_domain: str
    serpapi_device: str
    serpapi_competitor_domains: list[str]
    serpapi_advertiser_ids: list[str]
    serpapi_transparency_text: str | None
    serpapi_transparency_region: str | None

    business_domain: str | None
    default_date_range: str
    default_location_ids: list[str]
    default_language_id: str

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "Settings":
        if env_file:
            load_dotenv(env_file, override=False)

        return cls(
            google_ads_developer_token=_clean(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")),
            google_ads_client_id=_clean(os.getenv("GOOGLE_ADS_CLIENT_ID")),
            google_ads_client_secret=_clean(os.getenv("GOOGLE_ADS_CLIENT_SECRET")),
            google_ads_refresh_token=_clean(os.getenv("GOOGLE_ADS_REFRESH_TOKEN")),
            google_ads_login_customer_id=_clean(os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID")),
            google_ads_customer_id=first_non_empty(
                [os.getenv("GOOGLE_ADS_CUSTOMER_ID"), os.getenv("CUSTOMER_ID")]
            ),
            google_ads_api_version=_clean(os.getenv("GOOGLE_ADS_API_VERSION")) or "v24",
            google_ads_use_proto_plus=os.getenv("GOOGLE_ADS_USE_PROTO_PLUS", "true").lower()
            not in {"0", "false", "no"},
            anthropic_api_key=_clean(os.getenv("ANTHROPIC_API_KEY")),
            anthropic_model=_clean(os.getenv("ANTHROPIC_MODEL")) or "claude-sonnet-4-6",
            anthropic_max_tokens=_int_env("ANTHROPIC_MAX_TOKENS", 8000),
            anthropic_temperature=_float_env("ANTHROPIC_TEMPERATURE", 0.2),
            anthropic_max_input_chars=_int_env("ANTHROPIC_MAX_INPUT_CHARS", 180000),
            serpapi_api_key=_clean(os.getenv("SERPAPI_API_KEY")),
            serpapi_max_queries=_int_env("SERPAPI_MAX_QUERIES", 10),
            serpapi_location=_clean(os.getenv("SERPAPI_LOCATION")),
            serpapi_gl=_clean(os.getenv("SERPAPI_GL")) or "us",
            serpapi_hl=_clean(os.getenv("SERPAPI_HL")) or "en",
            serpapi_google_domain=_clean(os.getenv("SERPAPI_GOOGLE_DOMAIN")) or "google.com",
            serpapi_device=_clean(os.getenv("SERPAPI_DEVICE")) or "desktop",
            serpapi_competitor_domains=_csv_env("SERPAPI_COMPETITOR_DOMAINS"),
            serpapi_advertiser_ids=_csv_env("SERPAPI_ADVERTISER_IDS"),
            serpapi_transparency_text=_clean(os.getenv("SERPAPI_TRANSPARENCY_TEXT")),
            serpapi_transparency_region=_clean(os.getenv("SERPAPI_TRANSPARENCY_REGION")),
            business_domain=_clean(os.getenv("BUSINESS_DOMAIN")),
            default_date_range=_clean(os.getenv("REPORT_DATE_RANGE")) or "LAST_30_DAYS",
            default_location_ids=_csv_env("KEYWORD_PLANNER_LOCATION_IDS") or ["2840"],
            default_language_id=_clean(os.getenv("KEYWORD_PLANNER_LANGUAGE_ID")) or "1000",
        )

    def require_google_ads(self) -> None:
        missing = [
            name
            for name, value in {
                "GOOGLE_ADS_DEVELOPER_TOKEN": self.google_ads_developer_token,
                "GOOGLE_ADS_CLIENT_ID": self.google_ads_client_id,
                "GOOGLE_ADS_CLIENT_SECRET": self.google_ads_client_secret,
                "GOOGLE_ADS_REFRESH_TOKEN": self.google_ads_refresh_token,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing Google Ads configuration: " + ", ".join(sorted(missing))
            )

    def require_anthropic(self) -> None:
        if not self.anthropic_api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY")

