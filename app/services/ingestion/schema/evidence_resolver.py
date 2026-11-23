"""Evidence resolver service: maps Evidence.block_id to canonical source text & locator."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from app.schemas.ingestion.coverage import PreparedChunk, ValidationIssue
from app.schemas.ingestion.document import SourceBlock
from app.schemas.ingestion.graph_patch import GraphPatchFragment
from app.schemas.ingestion.grounding import Evidence

logger = logging.getLogger(__name__)


class EvidenceResolver:
    """Nhiệm vụ:
    Evidence.blockId
          ↓
    find SourceBlock
          ↓
    backend điền:
    source, section, text, locator.
    """

    def __init__(self, chunks: Iterable[PreparedChunk]) -> None:
        self.chunk_by_index: dict[int, PreparedChunk] = {
            chunk.chunk_index: chunk for chunk in chunks
        }
        # Bản đồ tra cứu block_id -> (chunk_index, SourceBlock)
        self.block_index: dict[str, tuple[int, SourceBlock]] = {}
        for chunk in chunks:
            for block in getattr(chunk, "blocks", []) or []:
                self.block_index[block.block_id] = (chunk.chunk_index, block)

    def resolve_evidence(
        self,
        evidence: Evidence,
        location: str = "evidence",
    ) -> tuple[Evidence, list[ValidationIssue]]:
        """Resolve một Evidence duy nhất.
        
        Nếu evidence có block_id:
        - Kiểm tra block tồn tại?
        - Kiểm tra block có thuộc đúng chunkIndex không?
        - Backend điền text, source, section.
        """
        issues: list[ValidationIssue] = []

        if not evidence.block_id:
            return evidence, issues

        chunk = self.chunk_by_index.get(evidence.chunk_index)
        if chunk is None:
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_UNKNOWN_CHUNK",
                    message=f"Evidence references unknown chunk index {evidence.chunk_index}",
                    location=location,
                    retryable=False,
                )
            )
            return evidence, issues

        block_lookup = self.block_index.get(evidence.block_id)
        if block_lookup is None:
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_UNKNOWN_BLOCK",
                    message=f"Evidence references unknown blockId {evidence.block_id!r}",
                    location=location,
                    retryable=True,
                )
            )
            return evidence, issues

        block_chunk_idx, source_block = block_lookup
        if block_chunk_idx != evidence.chunk_index:
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_BLOCK_CHUNK_MISMATCH",
                    message=(
                        f"Evidence blockId {evidence.block_id!r} belongs to chunk {block_chunk_idx}, "
                        f"not chunkIndex {evidence.chunk_index}"
                    ),
                    location=location,
                    retryable=True,
                )
            )
            return evidence, issues

        # Resolve thành công: backend sở hữu chính xác text, source anchor, section
        resolved = evidence.model_copy(
            update={
                "text": source_block.text,
                "source": chunk.source_anchor,
                "section": chunk.section,
            }
        )
        return resolved, issues

    def resolve_fragment(
        self,
        fragment: GraphPatchFragment,
    ) -> tuple[GraphPatchFragment, list[ValidationIssue]]:
        """Resolve toàn bộ evidence trong nodes, properties và edges của GraphPatchFragment."""
        all_issues: list[ValidationIssue] = []

        # Resolve nodes & node properties
        resolved_nodes = []
        for n_idx, node in enumerate(fragment.nodes):
            # Node-level evidence
            resolved_node_evidence = []
            for e_idx, ev in enumerate(node.evidence):
                rev, issues = self.resolve_evidence(
                    ev, location=f"nodes.{n_idx}.evidence.{e_idx}"
                )
                resolved_node_evidence.append(rev)
                all_issues.extend(issues)

            # Property-level evidence
            resolved_properties = []
            for p_idx, prop in enumerate(node.properties):
                resolved_prop_evidence = []
                for e_idx, ev in enumerate(prop.evidence):
                    rev, issues = self.resolve_evidence(
                        ev, location=f"nodes.{n_idx}.properties.{p_idx}.evidence.{e_idx}"
                    )
                    resolved_prop_evidence.append(rev)
                    all_issues.extend(issues)

                resolved_properties.append(
                    prop.model_copy(update={"evidence": resolved_prop_evidence})
                )

            resolved_nodes.append(
                node.model_copy(
                    update={
                        "evidence": resolved_node_evidence,
                        "properties": resolved_properties,
                    }
                )
            )

        # Resolve edges
        resolved_edges = []
        for e_idx, edge in enumerate(fragment.edges):
            resolved_edge_evidence = []
            for ev_idx, ev in enumerate(edge.evidence):
                rev, issues = self.resolve_evidence(
                    ev, location=f"edges.{e_idx}.evidence.{ev_idx}"
                )
                resolved_edge_evidence.append(rev)
                all_issues.extend(issues)

            resolved_edges.append(
                edge.model_copy(update={"evidence": resolved_edge_evidence})
            )

        resolved_fragment = fragment.model_copy(
            update={
                "nodes": resolved_nodes,
                "edges": resolved_edges,
            }
        )
        return resolved_fragment, all_issues
