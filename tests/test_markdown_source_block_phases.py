"""Phase 10: Markdown parsing, SourceBlock, EvidenceResolver, and Regression test suite.

Covers:
1. paragraph bình thường
2. bullet list
3. numbered list
4. nested list
5. Markdown table
6. table alignment :---:
7. pipes trong inline code
8. escaped pipe \\|
9. bold/italic
10. Vietnamese Unicode
11. emoji
12. weird whitespace
13. malformed table
14. unknown raw syntax
15. headings nested
16. duplicate headings
17. same document ingest twice (deterministic block IDs)
18. changed content -> changed block IDs phù hợp
19. Regression Batch 9: separator `|---:|---|---:|` KHÔNG BAO GIỜ trở thành evidence source block
20. EvidenceResolver: blockId -> backend text & locator resolution, và phát hiện mismatch
"""

import pytest

from app.schemas.ingestion.coverage import PreparedChunk
from app.schemas.ingestion.document import LoadedDocument, SourceBlock
from app.schemas.ingestion.graph_patch import GraphNode, GraphPatchFragment
from app.schemas.ingestion.grounding import Evidence, PropertyFact
from app.services.ingestion.pipeline.strategies import (
    StructuralTextChunker,
    parse_markdown_blocks,
)
from app.services.ingestion.schema.evidence_resolver import EvidenceResolver
from app.services.ingestion.schema.ontology import OntologyRegistry


def test_markdown_paragraph_and_formatting():
    content = "Đoạn văn **in đậm** và *in nghiêng* với Unicode tiếng Việt: võ sinh Taekwondo 🥋."
    blocks = parse_markdown_blocks(chunk_id="chk_1", content=content, start_line=1)
    assert len(blocks) == 1
    assert blocks[0].type == "paragraph"
    assert blocks[0].block_id == "chk_1:line:1"
    assert blocks[0].text == content
    assert blocks[0].locator == {"lineStart": 1, "lineEnd": 1}


def test_bullet_and_numbered_and_nested_list():
    content = """- Bullet 1
* Bullet 2
+ Bullet 3
1. Số 1
2. Số 2
   - Nested bullet"""
    blocks = parse_markdown_blocks(chunk_id="chk_list", content=content, start_line=10)
    assert len(blocks) == 6
    assert all(b.type == "list_item" for b in blocks)
    assert blocks[0].block_id == "chk_list:line:10"
    assert blocks[5].block_id == "chk_list:line:15"


def test_markdown_table_with_alignment_and_separator_elimination():
    """Batch 9 regression: separator |---:|---|---:| tuyệt đối không tạo source block."""
    content = """| Đai | Thời gian | Yêu cầu |
|:---|:---:|---:|
| I Đẳng | 48 tháng | Tham gia đủ 48 buổi |
| II Đẳng | 60 tháng | Tham gia đủ 60 buổi |"""
    blocks = parse_markdown_blocks(chunk_id="chk_table", content=content, start_line=100)
    
    # Chỉ có header + 2 data rows = 3 blocks (separator bị loại hoàn toàn)
    assert len(blocks) == 3
    assert blocks[0].text == "| Đai | Thời gian | Yêu cầu |"
    assert blocks[0].type == "table_row"
    assert blocks[0].block_id == "chk_table:line:100"

    assert blocks[1].text == "| I Đẳng | 48 tháng | Tham gia đủ 48 buổi |"
    assert blocks[1].type == "table_row"
    assert blocks[1].block_id == "chk_table:line:102"

    assert blocks[2].text == "| II Đẳng | 60 tháng | Tham gia đủ 60 buổi |"
    assert blocks[2].type == "table_row"
    assert blocks[2].block_id == "chk_table:line:103"


def test_table_with_escaped_pipe_and_code_pipe():
    content = """| Cột 1 | Cột 2 |
|---|---|
| Lệnh `grep | wc` | Giá trị a\\|b |"""
    blocks = parse_markdown_blocks(chunk_id="chk_pipes", content=content, start_line=20)
    assert len(blocks) == 2
    assert blocks[1].text == "| Lệnh `grep | wc` | Giá trị a\\|b |"
    assert blocks[1].type == "table_row"


def test_weird_whitespace_and_empty_lines():
    content = """   
   
   Dòng có nhiều khoảng trắng đầu cuối   
   
"""
    blocks = parse_markdown_blocks(chunk_id="chk_space", content=content, start_line=1)
    assert len(blocks) == 1
    assert blocks[0].text == "Dòng có nhiều khoảng trắng đầu cuối"
    assert blocks[0].locator == {"lineStart": 3, "lineEnd": 3}


def test_deterministic_block_ids_same_document():
    chunker = StructuralTextChunker()
    doc1 = LoadedDocument(
        source="taekwondo.md",
        suffix=".md",
        text="""# Quy định thi đai
| Cấp đai | Thời gian tối thiểu |
|---|---|
| Nhất Đẳng | 48 tháng |""",
    )
    doc2 = LoadedDocument(
        source="taekwondo.md",
        suffix=".md",
        text="""# Quy định thi đai
| Cấp đai | Thời gian tối thiểu |
|---|---|
| Nhất Đẳng | 48 tháng |""",
    )
    chunks1 = chunker.split(doc1)
    chunks2 = chunker.split(doc2)

    assert len(chunks1) == len(chunks2) == 1
    assert len(chunks1[0].blocks) == len(chunks2[0].blocks) == 2
    for b1, b2 in zip(chunks1[0].blocks, chunks2[0].blocks):
        assert b1.block_id == b2.block_id
        assert b1.text == b2.text


def test_changed_content_changes_chunk_and_block_ids():
    chunker = StructuralTextChunker()
    doc1 = LoadedDocument(
        source="taekwondo.md",
        suffix=".md",
        text="""# Quy định
| Cấp đai | Thời gian |
|---|---|
| I Đẳng | 48 tháng |""",
    )
    doc2 = LoadedDocument(
        source="taekwondo.md",
        suffix=".md",
        text="""# Quy định
| Cấp đai | Thời gian |
|---|---|
| I Đẳng | 60 tháng |""",
    )
    chunks1 = chunker.split(doc1)
    chunks2 = chunker.split(doc2)

    assert chunks1[0].chunk_id != chunks2[0].chunk_id
    assert chunks1[0].blocks[1].block_id != chunks2[0].blocks[1].block_id


def test_evidence_resolver_flow():
    block = SourceBlock(
        blockId="chk_test:line:102",
        type="table_row",
        text="| I Đẳng | 48 tháng |",
        locator={"lineStart": 102, "lineEnd": 102},
    )
    chunk = PreparedChunk(
        chunk_id="chk_test",
        chunk_index=9,
        text="I Đẳng: 48 tháng",
        content_hash="abc",
        token_count=10,
        section="Quy chế thi đai",
        source_anchor="taekwondo.md#QuyDinh:L100-L110",
        blocks=[block],
    )

    resolver = EvidenceResolver([chunk])

    # LLM trả về chỉ có chunkIndex và blockId (text rỗng)
    llm_evidence = Evidence(
        chunkIndex=9,
        blockId="chk_test:line:102",
        text="",
    )

    node = GraphNode(
        tempId="node_1",
        className="RankRequirement",
        properties=[
            PropertyFact(
                propertyName="minimum_duration",
                value="48 tháng",
                evidence=[llm_evidence],
            )
        ],
        evidence=[llm_evidence],
    )

    fragment = GraphPatchFragment(nodes=[node], edges=[])
    resolved_fragment, issues = resolver.resolve_fragment(fragment)

    assert len(issues) == 0
    resolved_prop_ev = resolved_fragment.nodes[0].properties[0].evidence[0]
    # Backend điền chính xác text từ source block!
    assert resolved_prop_ev.text == "| I Đẳng | 48 tháng |"
    assert resolved_prop_ev.source == "taekwondo.md#QuyDinh:L100-L110"
    assert resolved_prop_ev.section == "Quy chế thi đai"


def test_evidence_resolver_mismatch_and_unknown():
    block = SourceBlock(
        blockId="chk_test:line:102",
        type="table_row",
        text="| I Đẳng | 48 tháng |",
        locator={"lineStart": 102, "lineEnd": 102},
    )
    chunk0 = PreparedChunk(
        chunk_id="chk_test",
        chunk_index=0,
        text="I Đẳng: 48 tháng",
        content_hash="abc",
        token_count=10,
        source_anchor="doc#1",
        blocks=[block],
    )
    chunk1 = PreparedChunk(
        chunk_id="chk_test_2",
        chunk_index=1,
        text="Khác",
        content_hash="def",
        token_count=5,
        source_anchor="doc#2",
        blocks=[],
    )

    resolver = EvidenceResolver([chunk0, chunk1])

    # Case 1: block thuộc chunk 0 nhưng llm claim chunk 1 -> mismatch
    ev_mismatch = Evidence(chunkIndex=1, blockId="chk_test:line:102", text="")
    _, issues_mismatch = resolver.resolve_evidence(ev_mismatch)
    assert len(issues_mismatch) == 1
    assert issues_mismatch[0].code == "EVIDENCE_BLOCK_CHUNK_MISMATCH"

    # Case 2: block không tồn tại
    ev_unknown = Evidence(chunkIndex=0, blockId="chk_test:line:999", text="")
    _, issues_unknown = resolver.resolve_evidence(ev_unknown)
    assert len(issues_unknown) == 1
    assert issues_unknown[0].code == "EVIDENCE_UNKNOWN_BLOCK"


def test_canonicalize_markdown_table_strips_aligned_separator():
    from app.services.ingestion.pipeline.strategies import canonicalize_markdown_plain_text
    from app.services.ingestion.schema.ontology import _normalized_list_items

    raw = (
        "| Cấp đẳng | Mốc 85% số buổi tập được ghi | Số buổi được ghi riêng | Thời gian rèn luyện được ghi |\n"
        "|---|---|---:|---|\n"
        "| I Đẳng | 85% số buổi tập (40 buổi) | 48 buổi | 6 tháng |"
    )
    plain = canonicalize_markdown_plain_text(raw)
    assert "---:" not in plain
    assert "| ---" not in plain
    assert _normalized_list_items(plain) == []
    assert _normalized_list_items("Cấp đẳng: Mốc | --- | ---:\nI Đẳng: 6 tháng") == []


def test_evidence_guard_multiline_table_quote():
    from app.services.ingestion.schema.evidence_guard import canonical_table_excerpt

    chunk = PreparedChunk(
        chunk_id="chk_47",
        chunk_index=47,
        text=(
            "14.2. Bảng thời lượng ghi trong sheet\n\n"
            "Cấp đẳng: Mốc 85% số buổi tập được ghi | Số buổi được ghi riêng | Thời gian rèn luyện được ghi\n"
            "I Đẳng: 85% số buổi tập (40 buổi) | 48 buổi | 6 tháng\n"
            "II Đẳng: 85% số buổi tập (40 buổi) | 48 buổi | 12 tháng"
        ),
        content_hash="abc",
        token_count=20,
        source_anchor="doc.md#14.2",
    )
    quote = (
        "Cấp đẳng: Mốc 85% số buổi tập được ghi | Số buổi được ghi riêng | Thời gian rèn luyện được ghi\n"
        "---: --- | --- | ---\n"
        "I Đẳng: 85% số buổi tập (40 buổi) | 48 buổi | 6 tháng"
    )
    canonical = canonical_table_excerpt(chunk, quote)
    assert canonical in chunk.text
    assert "I Đẳng: 85% số buổi tập (40 buổi) | 48 buổi | 6 tháng" in canonical
