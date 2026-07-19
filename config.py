from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().casefold()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да"}


@dataclass(frozen=True)
class Settings:
    site_base_url: str
    agent_secret: str
    groq_api_key: str
    groq_model: str
    user_agent: str
    max_pages: int
    deep_max_pages: int
    max_sitemap_urls: int
    request_timeout_seconds: int
    request_delay_seconds: float
    max_page_bytes: int
    concurrency: int
    max_context_chars: int
    max_answer_chars: int
    allow_subdomains: bool
    respect_robots: bool
    job_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.environ.get("SITE_BASE_URL", "").strip()
        if base_url and not base_url.endswith("/"):
            base_url += "/"

        return cls(
            site_base_url=base_url,
            agent_secret=os.environ.get("AGENT_SECRET", "").strip(),
            groq_api_key=os.environ.get("GROQ_API_KEY", "").strip(),
            groq_model=os.environ.get(
                "GROQ_MODEL", "qwen/qwen3.6-27b"
            ).strip(),
            user_agent=os.environ.get(
                "SITE_USER_AGENT", "KubyatnyaSiteAgent/2.0 (+site research bot)"
            ).strip(),
            max_pages=_env_int("SITE_MAX_PAGES", 120, 1, 2000),
            deep_max_pages=_env_int("SITE_DEEP_MAX_PAGES", 350, 1, 5000),
            max_sitemap_urls=_env_int("SITE_MAX_SITEMAP_URLS", 10000, 100, 100000),
            request_timeout_seconds=_env_int("SITE_REQUEST_TIMEOUT", 20, 3, 120),
            request_delay_seconds=_env_float("SITE_REQUEST_DELAY", 0.25, 0.0, 10.0),
            max_page_bytes=_env_int("SITE_MAX_PAGE_BYTES", 3_000_000, 100_000, 20_000_000),
            concurrency=_env_int("SITE_CONCURRENCY", 4, 1, 12),
            max_context_chars=_env_int("MAX_CONTEXT_CHARS", 18_000, 4_000, 40_000),
            max_answer_chars=_env_int("MAX_ANSWER_CHARS", 8_000, 1_000, 20_000),
            allow_subdomains=_env_bool("SITE_ALLOW_SUBDOMAINS", False),
            respect_robots=_env_bool("RESPECT_ROBOTS_TXT", True),
            job_timeout_seconds=_env_int("JOB_TIMEOUT_SECONDS", 480, 60, 900),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        parsed = urlsplit(self.site_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            errors.append("SITE_BASE_URL должен быть полным адресом http:// или https://")
        if not self.agent_secret or len(self.agent_secret) < 16:
            errors.append("AGENT_SECRET должен содержать не менее 16 символов")
        if not self.groq_api_key:
            errors.append("GROQ_API_KEY не задан")
        if not self.groq_model:
            errors.append("GROQ_MODEL не задан")
        return errors
