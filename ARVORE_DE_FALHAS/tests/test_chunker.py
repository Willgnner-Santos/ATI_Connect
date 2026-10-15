from src.envelope.chunker import chunk_pages, chunk_plain_text


def test_chunk_pages_basic():
    pages = [(1, "A" * 3000)]
    chunks = chunk_pages(pages, chunk_size=1000, overlap=100)
    assert len(chunks) >= 3
    assert chunks[0].page_number == 1
    assert chunks[0].char_start <= chunks[1].char_start


def test_chunk_pages_empty():
    assert chunk_pages([]) == []


def test_chunk_plain_text_single():
    chunks = chunk_plain_text("Hello world " * 100, chunk_size=100, overlap=10)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.text) <= 100


def test_chunk_min_chars():
    # O último chunk fica com 10 chars (abaixo de min_chars=30) e é descartado.
    chunks = chunk_plain_text("a" * 60, chunk_size=50, overlap=0, min_chars=30)
    assert len(chunks) == 1