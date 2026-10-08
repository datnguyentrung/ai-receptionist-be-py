"""Nhóm định nghĩa Schema, phân giải ngữ nghĩa và kiểm tra tính toàn vẹn tri thức đồ thị.

Danh sách các module trong package:
- `ontology`: Quản lý snapshot ontology schema, cache và xác thực đồ thị.
- `graph_patch_compiled`: Biên dịch semantic fragment sang canonical graph patch fragment.
- `identity_resolver`: Phân giải định danh thực thể chuẩn hóa từ metadata Ontology.
- `semantic_resolution`: Khử nhập nhằng thực thể dựa trên Vector Embedding và LLM Gemini.
- `evidence_guard`: Chuẩn hóa và làm sạch trích dẫn bằng chứng (evidence spans).
- `repair_guard`: Bảo vệ các nút/cạnh hợp lệ khi LLM sửa lỗi batch.
"""

from app.services.ingestion.schema.evidence_guard import (
    GraphFragmentEvidenceGuard,
    canonical_bullet_excerpt,
    canonical_markdown_excerpt,
    canonical_prefix_completion_excerpt,
    canonical_sentence_window_excerpt,
    canonical_table_excerpt,
    canonical_whitespace_excerpt,
)
from app.services.ingestion.schema.graph_patch_compiled import (
    GraphPatchCompileResult,
    GraphPatchCompiler,
)
from app.services.ingestion.schema.identity_resolver import (
    IdentityResolutionError,
    OntologyIdentityResolver,
)
from app.services.ingestion.schema.ontology import (
    COMPILER_VERSION,
    OntologyCache,
    OntologyRegistry,
    merge_projections,
    validate_coverage_integrity,
)
from app.services.ingestion.schema.repair_guard import RepairGuard
from app.services.ingestion.schema.semantic_resolution import (
    AdkMergeVerifier,
    DisambiguationPair,
    SemanticCandidate,
    SemanticEntityResolver,
)

__all__ = [
    "AdkMergeVerifier",
    "COMPILER_VERSION",
    "DisambiguationPair",
    "GraphFragmentEvidenceGuard",
    "GraphPatchCompileResult",
    "GraphPatchCompiler",
    "IdentityResolutionError",
    "OntologyCache",
    "OntologyIdentityResolver",
    "OntologyRegistry",
    "RepairGuard",
    "SemanticCandidate",
    "SemanticEntityResolver",
    "canonical_bullet_excerpt",
    "canonical_markdown_excerpt",
    "canonical_prefix_completion_excerpt",
    "canonical_sentence_window_excerpt",
    "canonical_table_excerpt",
    "canonical_whitespace_excerpt",
    "merge_projections",
    "validate_coverage_integrity",
]
