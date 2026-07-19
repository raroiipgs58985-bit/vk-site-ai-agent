from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from ai import QueryPlan
from config import Settings
from crawler import PageDocument


_TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilySearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class TavilyOutcome:
    pages: list[PageDocument]
    urls_discovered: int
    queries_attempted: int
    errors: int


class TavilySearcher:
    """Searches an indexed copy of the target domain without direct crawling from Render."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {settings.tavily_api_key}",
                "Content-Type": "application/json",
                "User-Agent": settings.user_agent,
            }
        )
        self.hostname = (urlsplit(settings.site_base_url).hostname or "").casefold()

    def search(self, plan: QueryPlan, *, deep: bool) -> TavilyOutcome:
        if not self.settings.tavily_api_key:
            raise TavilySearchError("TAVILY_API_KEY не задан")

        queries = self._queries(plan, deep=deep)
        max_results = (
            self.settings.tavily_deep_max_results
            if deep
            else self.settings.tavily_max_results
        )
        pages_by_url: dict[str, PageDocument] = {}
        errors = 0

        for query in queries:
            try:
                response = self.session.post(
                    _TAVILY_SEARCH_URL,
                    json={
                        "query": query,
                        "topic": "general",
                        "search_depth": "basic",
                        "max_results": max_results,
                        "include_domains": [self.hostname],
                        "include_answer": False,
                        "include_raw_content": "text",
                        "include_images": False,
                    },
                    timeout=self.settings.tavily_timeout_seconds,
                )
                if response.status_code == 429:
                    raise TavilySearchError("Исчерпан временный лимит Tavily")
                if response.status_code >= 400:
                    raise TavilySearchError(
                        f"Tavily вернул HTTP {response.status_code}: {response.text[:300]}"
                    )
                payload = response.json()
            except (requests.RequestException, ValueError, TavilySearchError):
                errors += 1
                continue

            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                errors += 1
                continue

            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                title = str(item.get("title", "")).strip() or url
                if not self._allowed_url(url):
                    continue
                raw_content = item.get("raw_content")
                snippet = item.get("content")
                text = str(raw_content or snippet or "").strip()
                if len(text) < 80:
                    continue
                previous = pages_by_url.get(url)
                if previous is None or len(text) > len(previous.text):
                    pages_by_url[url] = PageDocument(
                        url=url,
                        title=title[:300],
                        text=text,
                        content_type="text/plain",
                    )

        pages = list(pages_by_url.values())
        return TavilyOutcome(
            pages=pages,
            urls_discovered=len(pages_by_url),
            queries_attempted=len(queries),
            errors=errors,
        )

    def _allowed_url(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        return (
            parsed.scheme.casefold() in {"http", "https"}
            and (parsed.hostname or "").casefold() == self.hostname
        )

    @staticmethod
    def _queries(plan: QueryPlan, *, deep: bool) -> list[str]:
        values: list[str] = [plan.english_question]
        if deep:
            values.extend(plan.search_queries[:2])
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = " ".join(str(value).split()).strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text[:400])
        return result[:3 if deep else 1]
