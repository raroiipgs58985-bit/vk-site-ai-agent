from __future__ import annotations

import time
from dataclasses import dataclass

from ai import GroqQwenClient
from config import Settings
from crawler import SiteCrawler
from search import build_source_context, chunk_documents, rank_chunks
from security import validate_public_http_url


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


class SiteResearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def research(self, question: str, *, deep: bool = False) -> ResearchResult:
        started = time.monotonic()
        validate_public_http_url(self.settings.site_base_url)
        ai = GroqQwenClient(
            api_key=self.settings.groq_api_key,
            model=self.settings.groq_model,
            timeout_seconds=min(120, self.settings.job_timeout_seconds),
        )
        plan = ai.plan_queries(question)

        hints = [
            plan.english_question,
            *plan.search_queries,
            *plan.keywords,
            *plan.entities,
        ]
        crawler = SiteCrawler(self.settings)
        crawl = crawler.crawl(
            query_hints=hints,
            deep=deep,
            deadline=started + self.settings.job_timeout_seconds,
        )
        chunks = chunk_documents(crawl.pages)
        ranked = rank_chunks(chunks, plan, limit=16 if deep else 12)
        if not ranked:
            elapsed = time.monotonic() - started
            return ResearchResult(
                answer=(
                    "На проверенных страницах не найдено фрагментов, достаточно близких "
                    "к вопросу. Это не доказывает, что информации на сайте нет: она могла "
                    "находиться на странице, которую нельзя было прочитать, либо быть "
                    "сформулирована иначе."
                ),
                sources=[],
                confidence="low",
                pages_scanned=len(crawl.pages),
                urls_discovered=crawl.discovered_urls,
                errors=crawl.errors,
                skipped_by_robots=crawl.skipped_by_robots,
                search_queries=plan.search_queries,
                elapsed_seconds=elapsed,
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
        )
