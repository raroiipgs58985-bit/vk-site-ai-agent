from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit


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
    openrouter_api_key: str
    openrouter_model: str
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
    search_mode: str = "auto"
    mediawiki_api_url: str = ""
    mediawiki_article_path: str = "/wiki/{title}"
    mediawiki_results_per_query: int = 8
    mediawiki_deep_results_per_query: int = 15
    mediawiki_query_limit: int = 6
    mediawiki_deep_query_limit: int = 8

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.environ.get("SITE_BASE_URL", "").strip()
        if base_url and not base_url.endswith("/"):
            base_url += "/"

        return cls(
            site_base_url=base_url,
            agent_secret=os.environ.get("AGENT_SECRET", "").strip(),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
            openrouter_model=os.environ.get(
                "OPENROUTER_MODEL", "openrouter/free"
            ).strip(),
            user_agent=os.environ.get(
                "SITE_USER_AGENT", "KubyatnyaSiteAgent/2.1 (+site research bot)"
            ).strip(),
            max_pages=_env_int("SITE_MAX_PAGES", 40, 1, 2000),
            deep_max_pages=_env_int("SITE_DEEP_MAX_PAGES", 100, 1, 5000),
            max_sitemap_urls=_env_int("SITE_MAX_SITEMAP_URLS", 10000, 100, 100000),
            request_timeout_seconds=_env_int("SITE_REQUEST_TIMEOUT", 20, 3, 120),
            request_delay_seconds=_env_float("SITE_REQUEST_DELAY", 0.5, 0.0, 10.0),
            max_page_bytes=_env_int("SITE_MAX_PAGE_BYTES", 3_000_000, 100_000, 20_000_000),
            concurrency=_env_int("SITE_CONCURRENCY", 3, 1, 12),
            max_context_chars=_env_int("MAX_CONTEXT_CHARS", 18_000, 4_000, 40_000),
            max_answer_chars=_env_int("MAX_ANSWER_CHARS", 8_000, 1_000, 20_000),
            allow_subdomains=_env_bool("SITE_ALLOW_SUBDOMAINS", False),
            respect_robots=_env_bool("RESPECT_ROBOTS_TXT", True),
            job_timeout_seconds=_env_int("JOB_TIMEOUT_SECONDS", 480, 60, 900),
            search_mode=os.environ.get("SITE_SEARCH_MODE", "auto").strip().casefold() or "auto",
            mediawiki_api_url=os.environ.get("MEDIAWIKI_API_URL", "").strip(),
            mediawiki_article_path=os.environ.get(
                "MEDIAWIKI_ARTICLE_PATH", "/wiki/{title}"
            ).strip() or "/wiki/{title}",
            mediawiki_results_per_query=_env_int(
                "MEDIAWIKI_RESULTS_PER_QUERY", 8, 1, 50
            ),
            mediawiki_deep_results_per_query=_env_int(
                "MEDIAWIKI_DEEP_RESULTS_PER_QUERY", 15, 1, 100
            ),
            mediawiki_query_limit=_env_int("MEDIAWIKI_QUERY_LIMIT", 6, 1, 12),
            mediawiki_deep_query_limit=_env_int(
                "MEDIAWIKI_DEEP_QUERY_LIMIT", 8, 1, 16
            ),
        )

    @property
    def resolved_search_mode(self) -> str:
        if self.search_mode in {"crawl", "mediawiki"}:
            return self.search_mode
        hostname = (urlsplit(self.site_base_url).hostname or "").casefold()
        if self.mediawiki_api_url or hostname.endswith("lexicanum.com"):
            return "mediawiki"
        return "crawl"

    @property
    def resolved_mediawiki_api_url(self) -> str:
        if self.mediawiki_api_url:
            return self.mediawiki_api_url
        return urljoin(self.site_base_url, "/mediawiki/api.php")

    def validate(self) -> list[str]:
        errors: list[str] = []
        parsed = urlsplit(self.site_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            errors.append("SITE_BASE_URL должен быть полным адресом http:// или https://")
        if not self.agent_secret or len(self.agent_secret) < 16:
            errors.append("AGENT_SECRET должен содержать не менее 16 символов")
        if not self.openrouter_api_key:
            errors.append("OPENROUTER_API_KEY не задан")
        if not self.openrouter_model:
            errors.append("OPENROUTER_MODEL не задан")
        if self.search_mode not in {"auto", "crawl", "mediawiki"}:
            errors.append("SITE_SEARCH_MODE должен быть auto, crawl или mediawiki")
        if self.resolved_search_mode == "mediawiki":
            api = urlsplit(self.resolved_mediawiki_api_url)
            if api.scheme not in {"http", "https"} or not api.hostname:
                errors.append("MEDIAWIKI_API_URL должен быть полным http/https адресом")
            elif parsed.hostname and api.hostname.casefold() != parsed.hostname.casefold():
                errors.append("MEDIAWIKI_API_URL должен находиться на том же домене")
            if "{title}" not in self.mediawiki_article_path:
                errors.append("MEDIAWIKI_ARTICLE_PATH должен содержать {title}")
        return errors
