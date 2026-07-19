from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable

from ai import OpenRouterClient
from config import Settings
from crawler import CrawlOutcome, SiteCrawler
from mediawiki import MediaWikiSearcher
from search import TextChunk, build_source_context, chunk_documents, rank_chunks
from security import validate_public_http_url
from source_utils import canonicalize_source_url
from tavily_search import TavilySearcher


_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass(frozen=True)
class ResearchResult:
    answer: str
    sources: list[dict[str, str]]
    confidence: str
    pages_scanned: int
    urls_discovered: int
    errors: int
    skipped_by_robots: int
    search_queries: list[str]
    elapsed_seconds: float
    search_backend: str


def _ordered_unique(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _citation_ids(answer: str) -> list[int]:
    values: list[int] = []
    for match in _CITATION_RE.finditer(answer):
        for raw in match.group(1).split(","):
            try:
                values.append(int(raw.strip()))
            except ValueError:
                continue
    return _ordered_unique(values)


def _rewrite_citations(answer: str, mapping: dict[int, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        remapped: list[int] = []
        for raw in match.group(1).split(","):
            try:
                source_id = int(raw.strip())
            except ValueError:
                continue
            display_id = mapping.get(source_id)
            if display_id is not None and display_id not in remapped:
                remapped.append(display_id)
        if not remapped:
            return ""
        return "[" + ", ".join(str(value) for value in remapped) + "]"

    rewritten = _CITATION_RE.sub(replace, answer)
    rewritten = re.sub(r"[ \t]+([,.;:])", r"\1", rewritten)
    rewritten = re.sub(r" {2,}", " ", rewritten)
    return rewritten.strip()


def prepare_answer_sources(
    answer: str,
    used_source_ids: list[int],
    included: list[TextChunk],
    *,
    max_sources: int = 6,
) -> tuple[str, list[dict[str, str]]]:
    """Deduplicate pages and rewrite model citations to the displayed source list."""
    candidate_ids = _ordered_unique([
        *_citation_ids(answer),
        *used_source_ids,
    ])
    if not candidate_ids:
        candidate_ids = list(range(1, min(4, len(included)) + 1))

    sources: list[dict[str, str]] = []
    display_by_url: dict[str, int] = {}
    display_by_context_id: dict[int, int] = {}

    for context_id in candidate_ids:
        if not 1 <= context_id <= len(included):
            continue
        chunk = included[context_id - 1]
        canonical_url = canonicalize_source_url(chunk.url) or chunk.url
        display_id = display_by_url.get(canonical_url)
        if display_id is None:
            if len(sources) >= max_sources:
                continue
            display_id = len(sources) + 1
            display_by_url[canonical_url] = display_id
            sources.append({
                "title": chunk.title,
                "url": canonical_url,
            })
        display_by_context_id[context_id] = display_id

    # A model can cite another excerpt from a page already selected above.
    for context_id, chunk in enumerate(included, start=1):
        canonical_url = canonicalize_source_url(chunk.url) or chunk.url
        display_id = display_by_url.get(canonical_url)
        if display_id is not None:
            display_by_context_id[context_id] = display_id

    return _rewrite_citations(answer, display_by_context_id), sources


class SiteResearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def research(self, question: str, *, deep: bool = False) -> ResearchResult:
        started = time.monotonic()
        deadline = started + self.settings.job_timeout_seconds
        validate_public_http_url(self.settings.site_base_url)
        if self.settings.resolved_search_mode == "mediawiki":
            validate_public_http_url(self.settings.resolved_mediawiki_api_url)

        ai = OpenRouterClient(
            api_key=self.settings.openrouter_api_key,
            model=self.settings.openrouter_model,
            timeout_seconds=min(120, self.settings.job_timeout_seconds),
        )
        plan = ai.plan_queries(question)

        hints = [
            *plan.entities,
            plan.english_question,
            *plan.search_queries,
            *plan.keywords,
        ]
        search_backend = self.settings.resolved_search_mode

        if search_backend == "tavily":
            outcome = TavilySearcher(self.settings).search(plan, deep=deep)
            crawl = CrawlOutcome(
                pages=outcome.pages,
                discovered_urls=outcome.urls_discovered,
                attempted_urls=outcome.queries_attempted,
                errors=outcome.errors,
                skipped_by_robots=0,
            )
        else:
            crawler = SiteCrawler(self.settings)
            if search_backend == "mediawiki":
                discovery = MediaWikiSearcher(self.settings).discover(
                    hints,
                    deep=deep,
                    deadline=deadline,
                )
                fetched = crawler.fetch_urls(discovery.urls, deadline=deadline)
                crawl = CrawlOutcome(
                    pages=fetched.pages,
                    discovered_urls=len(discovery.urls),
                    attempted_urls=fetched.attempted_urls,
                    errors=fetched.errors + discovery.errors,
                    skipped_by_robots=fetched.skipped_by_robots,
                )
            else:
                crawl = crawler.crawl(
                    query_hints=hints,
                    deep=deep,
                    deadline=deadline,
                )

        chunks = chunk_documents(crawl.pages)
        ranked = rank_chunks(chunks, plan, limit=18 if deep else 14)
        if not ranked:
            elapsed = time.monotonic() - started
            if crawl.discovered_urls == 0:
                reason = "Поисковый сервис не вернул страниц с указанного сайта."
            elif not crawl.pages:
                reason = "Найденные страницы не удалось прочитать."
            else:
                reason = "В найденных материалах нет фрагментов, достаточно близких к вопросу."
            return ResearchResult(
                answer=(
                    reason + " Это не доказывает, что информации на сайте нет: она могла "
                    "быть сформулирована иначе или отсутствовать в поисковом индексе."
                ),
                sources=[],
                confidence="low",
                pages_scanned=len(crawl.pages),
                urls_discovered=crawl.discovered_urls,
                errors=crawl.errors,
                skipped_by_robots=crawl.skipped_by_robots,
                search_queries=plan.search_queries,
                elapsed_seconds=elapsed,
                search_backend=search_backend,
            )

        context, included = build_source_context(
            ranked,
            max_chars=self.settings.max_context_chars,
        )
        answer_limit = min(
            self.settings.max_answer_chars,
            6000 if deep else 3200,
        )
        draft = ai.compose_answer(
            question=question,
            english_question=plan.english_question,
            source_context=context,
            max_answer_chars=answer_limit,
        )
        answer, sources = prepare_answer_sources(
            draft.answer,
            draft.used_source_ids,
            included,
            max_sources=8 if deep else 6,
        )

        return ResearchResult(
            answer=answer,
            sources=sources,
            confidence=draft.confidence,
            pages_scanned=len(crawl.pages),
            urls_discovered=crawl.discovered_urls,
            errors=crawl.errors,
            skipped_by_robots=crawl.skipped_by_robots,
            search_queries=plan.search_queries,
            elapsed_seconds=time.monotonic() - started,
            search_backend=search_backend,
        )
