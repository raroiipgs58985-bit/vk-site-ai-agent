from __future__ import annotations

from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


_DROP_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "mobileaction",
    "printable",
    "ref",
    "source",
    "useskin",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def canonicalize_source_url(url: str) -> str:
    """Return a stable public URL so alternate MediaWiki views deduplicate."""
    value = str(url).strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold()
    if scheme not in {"http", "https"} or not hostname:
        return value

    netloc = hostname
    if parsed.port and not (
        (scheme == "http" and parsed.port == 80)
        or (scheme == "https" and parsed.port == 443)
    ):
        netloc = f"{hostname}:{parsed.port}"

    path = parsed.path or "/"
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_map = {key.casefold(): val for key, val in pairs}

    if hostname.endswith("lexicanum.com"):
        normalized_path = path.rstrip("/").casefold()
        title = query_map.get("title", "").strip()
        if normalized_path == "/mediawiki/index.php" and title:
            article = unquote(title).strip().replace(" ", "_")
            encoded = quote(article, safe="()'!,:;@+-._~")
            path = f"/wiki/{encoded}"
            pairs = []
        elif path.casefold().startswith("/wiki/"):
            article = unquote(path[len("/wiki/"):]).strip().replace(" ", "_")
            encoded = quote(article, safe="()'!,:;@+-._~")
            path = f"/wiki/{encoded}" if encoded else "/wiki/"
            pairs = []
        else:
            pairs = [
                (key, val)
                for key, val in pairs
                if key.casefold() not in _DROP_QUERY_KEYS
            ]
    else:
        pairs = [
            (key, val)
            for key, val in pairs
            if key.casefold() not in _DROP_QUERY_KEYS
        ]

    query = urlencode(pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))
