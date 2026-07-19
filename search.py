from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from ai import QueryPlan
from crawler import PageDocument


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'’-]{1,}", re.IGNORECASE)
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "by", "from", "at", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "who", "what", "when", "where", "why",
    "how", "does", "did", "do", "as", "it", "its", "their", "there", "about",
}


@dataclass(frozen=True)
class TextChunk:
    source_id: int
    title: str
    url: str
    text: str
    index: int


@dataclass(frozen=True)
class RankedChunk:
    chunk: TextChunk
    score: float


def _tokens(text: str) -> list[str]:
    return [
        token.casefold().strip("-'’")
        for token in _TOKEN_RE.findall(text)
        if len(token.strip("-'’")) >= 2 and token.casefold() not in _STOPWORDS
    ]


def _normalized_subject(text: str) -> str:
    return " ".join(_tokens(text))


def chunk_documents(
    pages: Iterable[PageDocument], *, size: int = 1800, overlap: int = 250
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    source_id = 0
    for page in pages:
        source_id += 1
        text = page.text.strip()
        if not text:
            continue
        start = 0
        index = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                boundary = max(
                    text.rfind(". ", start + size // 2, end),
                    text.rfind("; ", start + size // 2, end),
                )
                if boundary > start:
                    end = boundary + 1
            piece = text[start:end].strip()
            if len(piece) >= 80:
                chunks.append(
                    TextChunk(
                        source_id=source_id,
                        title=page.title,
                        url=page.url,
                        text=piece,
                        index=index,
                    )
                )
                index += 1
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


def rank_chunks(chunks: list[TextChunk], plan: QueryPlan, *, limit: int = 14) -> list[RankedChunk]:
    if not chunks:
        return []

    query_phrases = [plan.english_question, *plan.search_queries, *plan.entities]
    query_tokens = _tokens(" ".join([*query_phrases, *plan.keywords]))
    query_counts = Counter(query_tokens)
    if not query_counts:
        return []

    primary_subjects = {
        normalized
        for normalized in (
            _normalized_subject(value)
            for value in [*plan.entities[:3], plan.english_question]
        )
        if normalized
    }

    chunk_token_counts: list[Counter[str]] = []
    document_frequency: Counter[str] = Counter()
    lengths: list[int] = []
    for chunk in chunks:
        counts = Counter(_tokens(f"{chunk.title} {chunk.text}"))
        chunk_token_counts.append(counts)
        lengths.append(sum(counts.values()))
        for token in counts:
            if token in query_counts:
                document_frequency[token] += 1

    avg_length = sum(lengths) / max(1, len(lengths))
    n = len(chunks)
    ranked: list[RankedChunk] = []
    k1 = 1.5
    b = 0.75

    for chunk, counts, length in zip(chunks, chunk_token_counts, lengths):
        score = 0.0
        for token, qtf in query_counts.items():
            tf = counts.get(token, 0)
            if not tf:
                continue
            df = document_frequency.get(token, 0)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            denominator = tf + k1 * (1 - b + b * length / max(1.0, avg_length))
            score += idf * (tf * (k1 + 1) / denominator) * min(2, qtf)

        lowered_text = chunk.text.casefold()
        lowered_title = chunk.title.casefold()
        lowered_url = chunk.url.casefold().replace("-", " ").replace("_", " ")
        for phrase in query_phrases:
            phrase = phrase.strip().casefold()
            if len(phrase) < 4:
                continue
            if phrase in lowered_text:
                score += 8.0
            if phrase in lowered_title:
                score += 12.0
            if phrase in lowered_url:
                score += 6.0

        normalized_title = _normalized_subject(chunk.title)
        for subject in primary_subjects:
            if normalized_title == subject:
                score += 35.0
            elif normalized_title.startswith(subject + " ") or subject.startswith(normalized_title + " "):
                score += 16.0

        title_hits = sum(1 for token in query_counts if token in lowered_title)
        url_hits = sum(1 for token in query_counts if token in lowered_url)
        score += title_hits * 1.7 + url_hits * 1.2
        if score > 0:
            ranked.append(RankedChunk(chunk=chunk, score=score))

    ranked.sort(key=lambda item: item.score, reverse=True)

    selected: list[RankedChunk] = []
    per_url: defaultdict[str, int] = defaultdict(int)
    for item in ranked:
        if per_url[item.chunk.url] >= 3:
            continue
        selected.append(item)
        per_url[item.chunk.url] += 1
        if len(selected) >= limit:
            break
    return selected


def build_source_context(ranked: list[RankedChunk], *, max_chars: int) -> tuple[str, list[TextChunk]]:
    blocks: list[str] = []
    included: list[TextChunk] = []
    used = 0
    for number, item in enumerate(ranked, start=1):
        chunk = item.chunk
        block = (
            f"[SOURCE {number}]\n"
            f"Title: {chunk.title}\n"
            f"URL: {chunk.url}\n"
            f"Excerpt: {chunk.text}\n"
        )
        if used + len(block) > max_chars and blocks:
            break
        blocks.append(block)
        included.append(chunk)
        used += len(block)
    return "\n".join(blocks), included
