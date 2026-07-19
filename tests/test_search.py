from ai import QueryPlan
from crawler import PageDocument
from search import build_source_context, chunk_documents, rank_chunks


def test_english_plan_finds_relevant_chunk() -> None:
    pages = [
        PageDocument(
            url="https://example.com/tithe",
            title="Imperial Tithe",
            text=(
                "The Adeptus Administratum assesses the Imperial Tithe. "
                "Planetary governors are responsible for meeting the assigned obligations."
            ),
            content_type="text/html",
        ),
        PageDocument(
            url="https://example.com/ships",
            title="Voidships",
            text="A voidship travels between star systems using the Warp.",
            content_type="text/html",
        ),
    ]
    plan = QueryPlan(
        english_question="Who assesses the Imperial Tithe?",
        search_queries=["Imperial Tithe assessment", "Adeptus Administratum tithe"],
        keywords=["tithe", "assessment", "governor"],
        entities=["Adeptus Administratum"],
    )
    chunks = chunk_documents(pages)
    ranked = rank_chunks(chunks, plan, limit=5)
    assert ranked
    assert ranked[0].chunk.url.endswith("/tithe")
    context, included = build_source_context(ranked, max_chars=5000)
    assert "Adeptus Administratum" in context
    assert included
