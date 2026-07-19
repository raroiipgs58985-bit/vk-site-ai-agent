from __future__ import annotations

import time
from dataclasses import dataclass

from ai import OpenRouterClient
from config import Settings
from crawler import CrawlOutcome, SiteCrawler
from mediawiki import MediaWikiSearcher
from search import build_source_context, chunk_documents, rank_chunks
from security import validate_public_http_url
from tavily_search import TavilySearcher


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
        draft = ai.compose_answer(
            question=question,
            english_question=plan.english_question,
            source_context=context,
            max_answer_chars=self.settings.max_answer_chars,
        )

        selected_ids = draft.used_source_ids or list(range(1, min(4, len(included)) + 1))
        sources: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for source_id in selected_ids:
            if not 1 <= source_id <= len(included):
                continue
            chunk = included[source_id - 1]
            if chunk.url in seen_urls:
                continue
            seen_urls.add(chunk.url)
            sources.append({"title": chunk.title, "url": chunk.url})
            if len(sources) >= 8:
                break

        return ResearchResult(
            answer=draft.answer,
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
