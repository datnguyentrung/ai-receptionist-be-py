import pytest

from app.services.ingestion.document import (
    DocumentReadError,
    DocumentReader,
    StructuralTextChunker,
)


def test_markdown_chunking_is_deterministic_and_structural() -> None:
    data = "# Lịch học\n\nThứ Hai 18:00\n\n# Học phí\n\n500000".encode()

    reader = DocumentReader()
    first = reader.read_bytes(filename="lich.md", data=data)
    second = reader.read_bytes(filename="lich.md", data=data)

    assert len(first) == 2
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert first[0].structural_path == "Lịch học#0"
    assert first[1].structural_path == "Học phí#0"
    assert first[0].content.startswith("Lịch học\n\n")
    assert "Lịch học" in first[0].content


@pytest.mark.parametrize("name,data", [("x.csv", b"a,b"), ("x.txt", b"\xff")])
def test_rejects_unsupported_or_invalid_text(name: str, data: bytes) -> None:
    reader = DocumentReader()
    with pytest.raises(DocumentReadError):
        reader.read_bytes(filename=name, data=data)
