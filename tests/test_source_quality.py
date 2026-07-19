from search import TextChunk
from service import prepare_answer_sources
from source_utils import canonicalize_source_url


def _chunk(title: str, url: str) -> TextChunk:
    return TextChunk(source_id=1, title=title, url=url, text="x" * 100, index=0)


def test_lexicanum_urls_are_canonicalized():
    desktop = (
        "https://wh40k.lexicanum.com/mediawiki/index.php"
        "?title=Boltgun&mobileaction=toggle_view_desktop"
    )
    canonical = "https://wh40k.lexicanum.com/wiki/Boltgun"
    assert canonicalize_source_url(desktop) == canonical
    assert canonicalize_source_url(canonical) == canonical


def test_citations_are_renumbered_and_duplicate_pages_collapsed():
    included = [
        _chunk("Heavy bolter", "https://wh40k.lexicanum.com/wiki/Heavy_bolter"),
        _chunk("Boltgun", "https://wh40k.lexicanum.com/wiki/Boltgun"),
        _chunk(
            "Boltgun",
            "https://wh40k.lexicanum.com/mediawiki/index.php"
            "?title=Boltgun&mobileaction=toggle_view_desktop",
        ),
    ]
    answer, sources = prepare_answer_sources(
        "Общее описание [2, 3]. Дополнение [1].",
        [2, 3, 1],
        included,
        max_sources=6,
    )
    assert answer == "Общее описание [1]. Дополнение [2]."
    assert [source["url"] for source in sources] == [
        "https://wh40k.lexicanum.com/wiki/Boltgun",
        "https://wh40k.lexicanum.com/wiki/Heavy_bolter",
    ]


def test_invalid_citations_are_removed():
    included = [_chunk("Boltgun", "https://wh40k.lexicanum.com/wiki/Boltgun")]
    answer, sources = prepare_answer_sources(
        "Описание [99]. Подтверждение [1].",
        [1],
        included,
    )
    assert answer == "Описание. Подтверждение [1]."
    assert len(sources) == 1
