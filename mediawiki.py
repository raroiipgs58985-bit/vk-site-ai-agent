from __future__ import annotations

import html
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import quote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from config import Settings


@dataclass(frozen=True)
class MediaWikiDiscovery:
    urls: list[str]
    queries_attempted: int
    errors: int
    api_available: bool


class MediaWikiSearcher:
    """Discovers likely article URLs through MediaWiki search, not a full crawl."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_url = settings.resolved_mediawiki_api_url
        parsed = urlsplit(settings.site_base_url)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.1",
                "Accept-Language": "en,ru;q=0.8",
            }
        )

    def discover(
        self,
        queries: Iterable[str],
        *,
        deep: bool,
        deadline: float | None = None,
    ) -> MediaWikiDiscovery:
        query_limit = (
            self.settings.mediawiki_deep_query_limit
            if deep
            else self.settings.mediawiki_query_limit
        )
        result_limit = (
            self.settings.mediawiki_deep_results_per_query
            if deep
            else self.settings.mediawiki_results_per_query
        )
        article_limit = self.settings.deep_max_pages if deep else self.settings.max_pages

        cleaned = self._clean_queries(queries, query_limit)
        urls: list[str] = []
        seen: set[str] = set()
        errors = 0
        api_available = True
        attempted = 0

        for query in cleaned:
            if deadline is not None and time.monotonic() >= deadline:
                break
            attempted += 1
            found: list[str] = []
            if api_available:
                try:
                    found = self._search_api(query, result_limit)
                except requests.RequestException:
                    errors += 1
                    api_available = False
                except (ValueError, TypeError, KeyError):
                    errors += 1
                    api_available = False

            if not found:
                try:
                    found = self._search_html(query, result_limit)
                except requests.RequestException:
                    errors += 1

            for url in found:
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                if len(urls) >= article_limit:
                    return MediaWikiDiscovery(
                        urls=urls,
                        queries_attempted=attempted,
                        errors=errors,
                        api_available=api_available,
                    )

            if self.settings.request_delay_seconds > 0:
                time.sleep(self.settings.request_delay_seconds)

        return MediaWikiDiscovery(
            urls=urls,
            queries_attempted=attempted,
            errors=errors,
            api_available=api_available,
        )

    def _search_api(self, query: str, limit: int) -> list[str]:
        response = self.session.get(
            self.api_url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srnamespace": "0",
                "srlimit": str(limit),
                "srprop": "snippet|titlesnippet",
                "utf8": "1",
                "format": "json",
                "formatversion": "2",
            },
            timeout=self.settings.request_timeout_seconds,
            allow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        raw_results = payload.get("query", {}).get("search", [])
        if not isinstance(raw_results, list):
            raise ValueError("MediaWiki search response has no result list")

        urls: list[str] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url = self._article_url(title)
            if url:
                urls.append(url)
        return list(dict.fromkeys(urls))

    def _search_html(self, query: str, limit: int) -> list[str]:
        search_url = urljoin(self.settings.site_base_url, "/mediawiki/index.php")
        response = self.session.get(
            search_url,
            params={
                "title": "Special:Search",
                "search": query,
                "fulltext": "Search",
                "ns0": "1",
            },
            timeout=self.settings.request_timeout_seconds,
            allow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        anchors = soup.select(
            ".mw-search-result-heading a, "
            "ul.mw-search-results li a, "
            ".searchresults a"
        )
        urls: list[str] = []
        for anchor in anchors:
            href = str(anchor.get("href", "")).strip()
            title = str(anchor.get("title", "")).strip()
            if href:
                url = urljoin(response.url, href)
            else:
                url = self._article_url(title)
            if not url or not self._is_article_url(url):
                continue
            urls.append(self._strip_fragment(url))
            if len(urls) >= limit:
                break
        return list(dict.fromkeys(urls))

    def _article_url(self, title: str) -> str | None:
        title = html.unescape(title).strip()
        if not title or self._is_non_article_title(title):
            return None
        encoded = quote(title.replace(" ", "_"), safe="()'!,:;@+-._~")
        path = self.settings.mediawiki_article_path.format(title=encoded)
        return urljoin(self.origin + "/", path.lstrip("/"))

    def _is_article_url(self, url: str) -> bool:
        parsed = urlsplit(url)
        base = urlsplit(self.settings.site_base_url)
        if parsed.hostname != base.hostname:
            return False
        lowered = url.casefold()
        blocked = (
            "special:",
            "talk:",
            "user:",
            "file:",
            "category:",
            "template:",
            "help:",
            "portal:",
        )
        return not any(value in lowered for value in blocked)

    @staticmethod
    def _is_non_article_title(title: str) -> bool:
        namespace = title.split(":", 1)[0].casefold() if ":" in title else ""
        return namespace in {
            "special",
            "talk",
            "user",
            "user talk",
            "file",
            "file talk",
            "category",
            "category talk",
            "template",
            "template talk",
            "help",
            "help talk",
            "portal",
            "portal talk",
        }

    @staticmethod
    def _strip_fragment(url: str) -> str:
        return urlsplit(url)._replace(fragment="").geturl()

    @staticmethod
    def _clean_queries(values: Iterable[str], limit: int) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = " ".join(str(value).split()).strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text[:180])
            if len(result) >= limit:
                break
        return result
