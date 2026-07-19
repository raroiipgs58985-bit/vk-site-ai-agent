from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import Settings
from crawler import SiteCrawler


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/sitemap.xml":
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f'<url><loc>http://127.0.0.1:{self.server.server_port}/tithe</loc></url>'
                '</urlset>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/tithe":
            body = b"<html><head><title>Imperial Tithe</title></head><body><main>The Adeptus Administratum assesses the Imperial Tithe for each world and records the obligations assigned to planetary governors across the Imperium.</main></body></html>"
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


def test_sitemap_crawl_allows_missing_robots() -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/"
        settings = Settings(
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
        )
        outcome = SiteCrawler(settings).crawl(query_hints=["Imperial Tithe"])
        assert len(outcome.pages) == 1
        assert "Adeptus Administratum" in outcome.pages[0].text
    finally:
        server.shutdown()
        server.server_close()
