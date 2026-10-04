"""Protect previously validated semantics across repair submissions."""

import logging
from typing import Any

from app.schemas.ingestion_schema import GraphNode, GraphPatchFragment, ValidationIssue
from app.services.ingestion.fact_references import canonical_json
from app.services.ingestion.repository import stable_entity_key

logger = logging.getLogger(__name__)


class RepairGuard:
    @classmethod
    def _node_key(cls, node: GraphNode) -> str:
        return stable_entity_key(node.class_name, node.identity) if node.identity else node.temp_id

    @staticmethod
    def _evidence_keys(items: list[Any]) -> set[str]:
        return {
            canonical_json(item.model_dump(by_alias=True, mode="json", exclude_none=True))
            for item in items
        }

    @classmethod
    def compare(
        cls,
        *,
        previous_canonical_fragment: GraphPatchFragment,
        new_canonical_fragment: GraphPatchFragment,
        previous_validation_issues: list[dict] | None = None,
    ) -> list[ValidationIssue]:
        """Require every protected semantic item to survive unchanged.

        A validated baseline contains no invalid item, so previous issue locations must
        never exempt an item from preservation.  The optional argument remains only for
        backwards-compatible callers.
        """

        del previous_validation_issues
        issues: list[ValidationIssue] = []
        old_nodes = {cls._node_key(node): node for node in previous_canonical_fragment.nodes}
        new_nodes = {cls._node_key(node): node for node in new_canonical_fragment.nodes}

        for stable_key, old_node in old_nodes.items():
            new_node = new_nodes.get(stable_key)
            if new_node is None:
                issues.append(
                    ValidationIssue(
                        code="REPAIR_DROPPED_VALID_NODE",
                        message=f"Repair removed a validated {old_node.class_name} node.",
                        location=f"nodes[{stable_key}]",
                        retryable=True,
                    )
                )
                continue
            new_by_name: dict[str, list[Any]] = {}
            for fact in new_node.properties:
                new_by_name.setdefault(fact.property_name, []).append(fact)
            for old_fact in old_node.properties:
                candidates = new_by_name.get(old_fact.property_name, [])
                if not candidates:
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_FACT",
                            message=(
                                f"Repair removed validated property '{old_fact.property_name}' "
                                f"from {old_node.class_name}."
                            ),
                            location=f"nodes[{stable_key}].properties[{old_fact.property_name}]",
                            retryable=True,
                        )
                    )
                    continue
                same_value = next(
                    (
                        fact
                        for fact in candidates
                        if canonical_json(fact.value) == canonical_json(old_fact.value)
                    ),
                    None,
                )
                if same_value is None or not cls._evidence_keys(old_fact.evidence).issubset(
                    cls._evidence_keys(same_value.evidence)
                ):
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_CHANGED_VALID_FACT",
                            message=(
                                f"Repair changed validated property '{old_fact.property_name}' "
                                f"on {old_node.class_name}."
                            ),
                            location=f"nodes[{stable_key}].properties[{old_fact.property_name}]",
                            retryable=True,
                        )
                    )

        new_edges = {cls._edge_key(edge): edge for edge in new_canonical_fragment.edges}
        for old_edge in previous_canonical_fragment.edges:
            key = cls._edge_key(old_edge)
            new_edge = new_edges.get(key)
            if new_edge is None:
                issues.append(
                    ValidationIssue(
                        code="REPAIR_DROPPED_VALID_EDGE",
                        message=f"Repair removed validated edge '{old_edge.edge_name}'.",
                        location=f"edges[{canonical_json(key)}]",
                        retryable=True,
                    )
                )
            elif (
                canonical_json(new_edge.properties) != canonical_json(old_edge.properties)
                or not cls._evidence_keys(old_edge.evidence).issubset(
                    cls._evidence_keys(new_edge.evidence)
                )
            ):
                issues.append(
                    ValidationIssue(
                        code="REPAIR_CHANGED_VALID_EDGE",
                        message=f"Repair changed validated edge '{old_edge.edge_name}'.",
                        location=f"edges[{canonical_json(key)}]",
                        retryable=True,
                    )
                )

        new_coverage = {item.chunk_index: item for item in new_canonical_fragment.coverage}
        for old_item in previous_canonical_fragment.coverage:
            incoming = new_coverage.get(old_item.chunk_index)
            if incoming is None or incoming.decision != old_item.decision:
                issues.append(
                    ValidationIssue(
                        code="REPAIR_CHANGED_VALID_COVERAGE",
                        message=(
                            f"Repair changed validated coverage for chunk "
                            f"{old_item.chunk_index}."
                        ),
                        location=f"coverage[{old_item.chunk_index}]",
                        retryable=True,
                    )
                )

        logger.info(
            "REPAIR_DIFF previousNodes=%s newNodes=%s rejectedChanges=%s",
            len(previous_canonical_fragment.nodes),
            len(new_canonical_fragment.nodes),
            len(issues),
        )
        return issues

    @classmethod
    def extract_validated_baseline(
        cls,
        fragment: GraphPatchFragment,
        issues: list[dict] | list[ValidationIssue],
    ) -> GraphPatchFragment | None:
        """Extract a semantic snapshot containing only issue-free items."""

        locations = {
            str(item.get("location") if isinstance(item, dict) else item.location or "")
            for item in issues
        }
        valid_nodes: list[GraphNode] = []
        for index, node in enumerate(fragment.nodes):
            node_loc = f"nodes.{index}"
            direct_node_invalid = any(
                location == node_loc
                or location.startswith(
                    (f"{node_loc}.className", f"{node_loc}.identity")
                )
                for location in locations
            )
            if direct_node_invalid:
                continue
            properties = [
                fact
                for prop_index, fact in enumerate(node.properties)
                if not cls._location_was_invalid(
                    f"{node_loc}.properties.{prop_index}", locations
                )
            ]
            if node.properties and not properties:
                continue
            valid_nodes.append(node.model_copy(update={"properties": properties}))

        valid_edges = [
            edge
            for index, edge in enumerate(fragment.edges)
            if not cls._location_was_invalid(f"edges.{index}", locations)
        ]
        valid_coverage = [
            item
            for index, item in enumerate(fragment.coverage)
            if not cls._location_was_invalid(f"coverage.{index}", locations)
        ]
        if not valid_nodes and not valid_edges and not valid_coverage:
            return None
        return GraphPatchFragment(
            ontology_version=fragment.ontology_version,
            nodes=valid_nodes,
            edges=valid_edges,
            coverage=valid_coverage,
            warnings=[],
        )

    @classmethod
    def merge_baselines(
        cls,
        previous: GraphPatchFragment | None,
        incoming: GraphPatchFragment | None,
    ) -> GraphPatchFragment | None:
        """Monotonically add newly validated semantics to a protected snapshot."""

        if previous is None:
            return incoming.model_copy(deep=True) if incoming else None
        if incoming is None:
            return previous.model_copy(deep=True)
        merged = previous.model_copy(deep=True)
        nodes = {cls._node_key(node): node for node in merged.nodes}
        for node in incoming.nodes:
            key = cls._node_key(node)
            current = nodes.get(key)
            if current is None:
                copied = node.model_copy(deep=True)
                merged.nodes.append(copied)
                nodes[key] = copied
                continue
            known = {
                (fact.property_name, canonical_json(fact.value)): fact
                for fact in current.properties
            }
            for fact in node.properties:
                fact_key = (fact.property_name, canonical_json(fact.value))
                existing = known.get(fact_key)
                if existing is None:
                    copied = fact.model_copy(deep=True)
                    current.properties.append(copied)
                    known[fact_key] = copied
                else:
                    cls._merge_evidence(existing.evidence, fact.evidence)
            cls._merge_evidence(current.evidence, node.evidence)
        edges = {cls._edge_key(edge): edge for edge in merged.edges}
        for edge in incoming.edges:
            edge_key = cls._edge_key(edge)
            existing = edges.get(edge_key)
            if existing is None:
                copied = edge.model_copy(deep=True)
                merged.edges.append(copied)
                edges[edge_key] = copied
            else:
                for name, value in edge.properties.items():
                    existing.properties.setdefault(name, value)
                cls._merge_evidence(existing.evidence, edge.evidence)
        coverage_chunks = {item.chunk_index for item in merged.coverage}
        merged.coverage.extend(
            item.model_copy(deep=True)
            for item in incoming.coverage
            if item.chunk_index not in coverage_chunks
        )
        merged.warnings = list(dict.fromkeys([*merged.warnings, *incoming.warnings]))
        return merged

    @classmethod
    def _merge_evidence(cls, current: list[Any], incoming: list[Any]) -> None:
        known = cls._evidence_keys(current)
        for evidence in incoming:
            encoded = canonical_json(
                evidence.model_dump(by_alias=True, mode="json", exclude_none=True)
            )
            if encoded not in known:
                current.append(evidence.model_copy(deep=True))
                known.add(encoded)

    @staticmethod
    def _edge_key(edge: Any) -> tuple[str, str, str]:
        return edge.edge_name, edge.source_temp_id, edge.target_temp_id

    @staticmethod
    def _location_was_invalid(prefix: str, locations: set[str]) -> bool:
        return any(
            location == prefix or location.startswith(prefix + ".")
            for location in locations
            if location
        )


__all__ = ["RepairGuard"]
