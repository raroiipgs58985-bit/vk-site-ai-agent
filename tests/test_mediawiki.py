from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import Settings
from crawler import SiteCrawler
from mediawiki import MediaWikiSearcher


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/mediawiki/api.php"):
            body = json.dumps(
                {
                    "query": {
                        "search": [
                            {"title": "Imperial Tithe"},
                            {"title": "Adeptus Administratum"},
                        ]
                    }
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        if self.path.startswith("/wiki/Imperial_Tithe"):
            body = b'''<html><body><h1 id="firstHeading">Imperial Tithe</h1><div id="mw-content-text"><div class="mw-parser-output"><p>The Departmento Exacta oversees collection of the Exacta tithe across Imperial worlds and records the obligations imposed upon planetary authorities.</p></div></div></body></html>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/wiki/Adeptus_Administratum"):
            body = b'''<html><body><h1 id="firstHeading">Adeptus Administratum</h1><div id="mw-content-text"><div class="mw-parser-output"><p>The Adeptus Administratum manages records, taxation, logistics, and the Imperial obligations assigned to each compliant world.</p></div></div></body></html>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def make_settings(base: str) -> Settings:
    return Settings(
        site_base_url=base,
        agent_secret="x" * 20,
        openrouter_api_key="test",
        openrouter_model="openrouter/free",
        user_agent="test-agent",
        max_pages=10,
        deep_max_pages=20,
        max_sitemap_urls=100,
        request_timeout_seconds=3,
        request_delay_seconds=0,
        max_page_bytes=100000,
        concurrency=2,
        max_context_chars=10000,
        max_answer_chars=5000,
        allow_subdomains=False,
        respect_robots=True,
        job_timeout_seconds=120,
        search_mode="mediawiki",
        mediawiki_api_url=base + "mediawiki/api.php",
    )


def test_mediawiki_search_discovers_and_fetches_articles() -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/"
        settings = make_settings(base)
        discovery = MediaWikiSearcher(settings).discover(
            ["Imperial Tithe"], deep=False
        )
        assert len(discovery.urls) == 2
        outcome = SiteCrawler(settings).fetch_urls(discovery.urls)
        assert len(outcome.pages) == 2
        assert any("Departmento Exacta" in page.text for page in outcome.pages)
    finally:
        server.shutdown()
        server.server_close()


class FallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/mediawiki/api.php"):
            self.send_response(403)
            self.end_headers()
            return
        if self.path.startswith("/mediawiki/index.php"):
            body = b'''<html><body><ul class="mw-search-results"><li><div class="mw-search-result-heading"><a href="/wiki/Imperial_Tithe" title="Imperial Tithe">Imperial Tithe</a></div></li></ul></body></html>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def test_mediawiki_search_falls_back_to_html() -> None:
    server = HTTPServer(("127.0.0.1", 0), FallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/"
        settings = make_settings(base)
        discovery = MediaWikiSearcher(settings).discover(
            ["Imperial Tithe"], deep=False
        )
        assert discovery.urls == [base + "wiki/Imperial_Tithe"]
        assert discovery.api_available is False
        assert discovery.errors == 1
    finally:
        server.shutdown()
        server.server_close()
