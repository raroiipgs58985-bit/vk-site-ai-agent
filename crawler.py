from __future__ import annotations

import gzip
import io
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from config import Settings


_SPACE_RE = re.compile(r"\s+")
_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
_SKIP_EXTENSIONS = {
    ".7z", ".avi", ".bmp", ".css", ".csv", ".doc", ".docx", ".eot",
    ".epub", ".exe", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".js",
    ".json", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".ogg",
    ".otf", ".png", ".ppt", ".pptx", ".rar", ".rss", ".svg", ".tar",
    ".tif", ".tiff", ".ttf", ".wav", ".webm", ".webp", ".woff", ".woff2",
    ".xls", ".xlsx", ".zip",
}
_ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/pdf",
}


@dataclass(frozen=True)
class PageDocument:
    url: str
    title: str
    text: str
    content_type: str


@dataclass(frozen=True)
class CrawlOutcome:
    pages: list[PageDocument]
    discovered_urls: int
    attempted_urls: int
    errors: int
    skipped_by_robots: int


class _RateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self._lock = threading.Lock()
        self._next_time = 0.0

    def wait(self) -> None:
        if self.delay_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next_time - now
            if delay > 0:
                time.sleep(delay)
            self._next_time = time.monotonic() + self.delay_seconds


class SiteCrawler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        parsed = urlsplit(settings.site_base_url)
        self.scheme = parsed.scheme.casefold()
        self.hostname = (parsed.hostname or "").casefold()
        self.port = parsed.port
        self.base_netloc = parsed.netloc.casefold()
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.8,*/*;q=0.1",
                "Accept-Language": "en,ru;q=0.8",
            }
        )
        self.rate_limiter = _RateLimiter(settings.request_delay_seconds)
        self.robots = self._load_robots()

    def crawl(
        self,
        *,
        query_hints: Iterable[str],
        deep: bool = False,
        deadline: float | None = None,
    ) -> CrawlOutcome:
        limit = self.settings.deep_max_pages if deep else self.settings.max_pages
        sitemap_urls = self._discover_sitemap_urls(deadline=deadline)
        hints = [value.casefold() for value in query_hints if value.strip()]

        if sitemap_urls:
            candidates = self._prioritize_urls(sitemap_urls, hints, limit)
            return self._fetch_candidate_batch(candidates, deadline=deadline)
        return self._crawl_breadth_first(limit=limit, deadline=deadline)

    def fetch_urls(
        self, urls: Iterable[str], *, deadline: float | None = None
    ) -> CrawlOutcome:
        normalized: list[str] = []
        for value in urls:
            url = self._normalize_url(value)
            if url and self._is_fetchable_page_url(url):
                normalized.append(url)
        return self._fetch_candidate_batch(
            list(dict.fromkeys(normalized)), deadline=deadline
        )

    def _load_robots(self) -> RobotFileParser | None:
        if not self.settings.respect_robots:
            return None
        robots_url = urljoin(self.settings.site_base_url, "/robots.txt")
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = self.session.get(
                robots_url,
                timeout=self.settings.request_timeout_seconds,
                allow_redirects=True,
            )
            if response.status_code >= 400:
                parser.parse([])
                return parser
            parser.parse(response.text.splitlines())
            return parser
        except requests.RequestException:
            parser.parse([])
            return parser

    def _can_fetch(self, url: str) -> bool:
        if self.robots is None:
            return True
        try:
            return bool(self.robots.can_fetch(self.settings.user_agent, url))
        except Exception:
            return True

    def _discover_sitemap_urls(self, *, deadline: float | None = None) -> list[str]:
        seeds = [urljoin(self.settings.site_base_url, "/sitemap.xml")]
        robots_url = urljoin(self.settings.site_base_url, "/robots.txt")
        try:
            response = self.session.get(
                robots_url,
                timeout=self.settings.request_timeout_seconds,
            )
            if response.ok:
                for line in response.text.splitlines():
                    if line.casefold().startswith("sitemap:"):
                        value = line.split(":", 1)[1].strip()
                        normalized = self._normalize_url(value)
                        if normalized:
                            seeds.append(normalized)
        except requests.RequestException:
            pass

        page_urls: list[str] = []
        seen_sitemaps: set[str] = set()
        queue: deque[str] = deque(dict.fromkeys(seeds))
        while queue and len(page_urls) < self.settings.max_sitemap_urls:
            if deadline is not None and time.monotonic() >= deadline:
                break
            sitemap_url = queue.popleft()
            if sitemap_url in seen_sitemaps or not self._is_allowed_url(sitemap_url):
                continue
            seen_sitemaps.add(sitemap_url)
            try:
                raw = self._download_bytes(sitemap_url, max_bytes=15_000_000)
                if raw[:2] == b"\x1f\x8b" or sitemap_url.casefold().endswith(".gz"):
                    raw = gzip.decompress(raw)
                root = ET.fromstring(raw)
            except (requests.RequestException, ET.ParseError, OSError, ValueError):
                continue

            root_name = root.tag.rsplit("}", 1)[-1].casefold()
            locations = [
                (element.text or "").strip()
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1].casefold() == "loc"
                and (element.text or "").strip()
            ]
            if root_name == "sitemapindex":
                for location in locations:
                    normalized = self._normalize_url(location)
                    if normalized and normalized not in seen_sitemaps:
                        queue.append(normalized)
            else:
                for location in locations:
                    normalized = self._normalize_url(location)
                    if normalized and self._is_fetchable_page_url(normalized):
                        page_urls.append(normalized)
                        if len(page_urls) >= self.settings.max_sitemap_urls:
                            break
        return list(dict.fromkeys(page_urls))

    def _prioritize_urls(self, urls: list[str], hints: list[str], limit: int) -> list[str]:
        hint_tokens = {
            token
            for hint in hints
            for token in re.findall(r"[a-z0-9]{3,}", hint.casefold())
        }

        def score(url: str) -> tuple[int, int, str]:
            decoded = url.casefold().replace("-", " ").replace("_", " ")
            matches = sum(1 for token in hint_tokens if token in decoded)
            path_depth = len([part for part in urlsplit(url).path.split("/") if part])
            return (-matches, path_depth, url)

        ordered = sorted(dict.fromkeys(urls), key=score)
        return ordered[:limit]

    def _fetch_candidate_batch(
        self, urls: list[str], *, deadline: float | None = None
    ) -> CrawlOutcome:
        pages: list[PageDocument] = []
        errors = 0
        skipped = 0
        allowed: list[str] = []
        for url in urls:
            if self._can_fetch(url):
                allowed.append(url)
            else:
                skipped += 1

        attempted = 0
        for start in range(0, len(allowed), self.settings.concurrency):
            if deadline is not None and time.monotonic() >= deadline:
                break
            batch = allowed[start:start + self.settings.concurrency]
            attempted += len(batch)
            with ThreadPoolExecutor(max_workers=self.settings.concurrency) as executor:
                futures = {executor.submit(self._fetch_document, url): url for url in batch}
                for future in as_completed(futures):
                    try:
                        document = future.result()
                    except Exception:
                        errors += 1
                        continue
                    if document is not None:
                        pages.append(document)
        return CrawlOutcome(
            pages=pages,
            discovered_urls=len(urls),
            attempted_urls=attempted,
            errors=errors,
            skipped_by_robots=skipped,
        )

    def _crawl_breadth_first(
        self, *, limit: int, deadline: float | None = None
    ) -> CrawlOutcome:
        queue: deque[str] = deque([self._normalize_url(self.settings.site_base_url) or self.settings.site_base_url])
        seen: set[str] = set()
        pages: list[PageDocument] = []
        errors = 0
        skipped = 0

        while queue and len(seen) < limit:
            if deadline is not None and time.monotonic() >= deadline:
                break
            batch: list[str] = []
            while queue and len(batch) < self.settings.concurrency and len(seen) + len(batch) < limit:
                url = queue.popleft()
                if url in seen or not self._is_fetchable_page_url(url):
                    continue
                seen.add(url)
                if not self._can_fetch(url):
                    skipped += 1
                    continue
                batch.append(url)

            if not batch:
                continue

            with ThreadPoolExecutor(max_workers=self.settings.concurrency) as executor:
                futures = {executor.submit(self._fetch_html_with_links, url): url for url in batch}
                for future in as_completed(futures):
                    try:
                        document, links = future.result()
                    except Exception:
                        errors += 1
                        continue
                    if document is not None:
                        pages.append(document)
                    for link in links:
                        if link not in seen:
                            queue.append(link)

        return CrawlOutcome(
            pages=pages,
            discovered_urls=len(seen) + len(queue),
            attempted_urls=len(seen),
            errors=errors,
            skipped_by_robots=skipped,
        )

    def _fetch_html_with_links(self, url: str) -> tuple[PageDocument | None, list[str]]:
        document, soup = self._fetch_document_internal(url, keep_soup=True)
        links: list[str] = []
        if soup is not None:
            for anchor in soup.find_all("a", href=True):
                normalized = self._normalize_url(urljoin(url, anchor.get("href", "")))
                if normalized and self._is_fetchable_page_url(normalized):
                    links.append(normalized)
        return document, list(dict.fromkeys(links))

    def _fetch_document(self, url: str) -> PageDocument | None:
        document, _ = self._fetch_document_internal(url, keep_soup=False)
        return document

    def _fetch_document_internal(
        self, url: str, *, keep_soup: bool
    ) -> tuple[PageDocument | None, BeautifulSoup | None]:
        self.rate_limiter.wait()
        response = self.session.get(
            url,
            timeout=self.settings.request_timeout_seconds,
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        final_url = self._normalize_url(response.url)
        if not final_url or not self._is_allowed_url(final_url):
            return None, None

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            return None, None
        raw = self._read_limited(response, self.settings.max_page_bytes)

        if content_type == "application/pdf" or final_url.casefold().endswith(".pdf"):
            text, title = self._extract_pdf(raw, final_url)
            if len(text) < 80:
                return None, None
            return PageDocument(final_url, title, text, "application/pdf"), None

        encoding = response.encoding or response.apparent_encoding or "utf-8"
        body = raw.decode(encoding, errors="replace")
        if content_type == "text/plain":
            text = _SPACE_RE.sub(" ", body).strip()
            if len(text) < 80:
                return None, None
            return PageDocument(final_url, PurePosixPath(urlsplit(final_url).path).name or final_url, text, content_type), None

        soup = BeautifulSoup(body, "html.parser")
        title, text = self._extract_html(soup, final_url)
        if len(text) < 80:
            return None, soup if keep_soup else None
        return PageDocument(final_url, title, text, content_type), soup if keep_soup else None

    def _download_bytes(self, url: str, *, max_bytes: int) -> bytes:
        self.rate_limiter.wait()
        response = self.session.get(
            url,
            timeout=self.settings.request_timeout_seconds,
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        final_url = self._normalize_url(response.url)
        if not final_url or not self._is_allowed_url(final_url):
            raise ValueError("Sitemap redirected outside allowed site")
        return self._read_limited(response, max_bytes)

    @staticmethod
    def _read_limited(response: requests.Response, max_bytes: int) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > max_bytes:
            raise ValueError("Page exceeds byte limit")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Page exceeds byte limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _extract_html(soup: BeautifulSoup, url: str) -> tuple[str, str]:
        for tag in soup.find_all(
            ["script", "style", "noscript", "svg", "canvas", "form", "nav", "footer", "header", "aside"]
        ):
            tag.decompose()

        for selector in (
            ".mw-editsection",
            ".mw-jump-link",
            ".printfooter",
            ".catlinks",
            ".navbox",
            ".vertical-navbox",
            ".metadata",
            ".noprint",
            "#toc",
            "#siteSub",
            "#contentSub",
        ):
            for node in soup.select(selector):
                node.decompose()

        title = ""
        heading = soup.select_one("#firstHeading") or soup.find("h1")
        if heading:
            title = heading.get_text(" ", strip=True)
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
        if not title:
            title = PurePosixPath(urlsplit(url).path).name or url

        root = (
            soup.select_one("#mw-content-text .mw-parser-output")
            or soup.select_one("#mw-content-text")
            or soup.find("article")
            or soup.find("main")
            or soup.body
            or soup
        )
        text = root.get_text(" ", strip=True)
        return _SPACE_RE.sub(" ", title).strip()[:300], _SPACE_RE.sub(" ", text).strip()

    @staticmethod
    def _extract_pdf(raw: bytes, url: str) -> tuple[str, str]:
        reader = PdfReader(io.BytesIO(raw))
        pages: list[str] = []
        for page in reader.pages[:300]:
            try:
                value = page.extract_text() or ""
            except Exception:
                value = ""
            if value:
                pages.append(value)
        text = _SPACE_RE.sub(" ", " ".join(pages)).strip()
        title = ""
        try:
            title = str(reader.metadata.title or "").strip() if reader.metadata else ""
        except Exception:
            title = ""
        if not title:
            title = PurePosixPath(urlsplit(url).path).name or url
        return text, title[:300]

    def _normalize_url(self, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold() not in _TRACKING_KEYS
            ],
            doseq=True,
        )
        path = parsed.path or "/"
        return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, query, ""))

    def _is_allowed_url(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme.casefold() not in {"http", "https"}:
            return False
        if self.settings.allow_subdomains:
            return host == self.hostname or host.endswith("." + self.hostname)
        return host == self.hostname and (parsed.port or None) == (self.port or None)

    def _is_fetchable_page_url(self, url: str) -> bool:
        if not self._is_allowed_url(url):
            return False
        suffix = PurePosixPath(urlsplit(url).path.casefold()).suffix
        if suffix in _SKIP_EXTENSIONS:
            return False
        return True
