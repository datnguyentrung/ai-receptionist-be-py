"""Reject repair submissions that silently remove previously valid semantics."""

import logging
from typing import Any

from app.schemas.ingestion_schema import GraphNode, GraphPatchFragment, ValidationIssue
from app.services.ingestion.repository import stable_entity_key

logger = logging.getLogger(__name__)


class RepairGuard:
    @classmethod
    def _node_key(cls, node: GraphNode) -> str:
        if node.identity:
            return stable_entity_key(node.class_name, node.identity)
        return node.temp_id

    @classmethod
    def compare(
        cls,
        *,
        previous_canonical_fragment: GraphPatchFragment,
        new_canonical_fragment: GraphPatchFragment,
        previous_validation_issues: list[dict] | None = None,
    ) -> list[ValidationIssue]:
        issue_locations = {
            str(item.get("location") or "")
            for item in (previous_validation_issues or [])
            if isinstance(item, dict)
        }
        issues: list[ValidationIssue] = []
        old_nodes = {
            cls._node_key(node): (index, node)
            for index, node in enumerate(previous_canonical_fragment.nodes)
        }
        new_nodes = {
            cls._node_key(node): node for node in new_canonical_fragment.nodes
        }

        for stable_key, (node_index, old_node) in old_nodes.items():
            node_location = f"nodes.{node_index}"
            if stable_key not in new_nodes:
                if not cls._location_was_invalid(node_location, issue_locations):
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_NODE",
                            message=(
                                f"Repair removed a previously valid node ({old_node.class_name}) "
                                "that was not part of the validation failure."
                            ),
                            location=node_location,
                            retryable=True,
                        )
                    )
                continue
            new_properties = {
                item.property_name: item for item in new_nodes[stable_key].properties
            }
            for property_index, old_fact in enumerate(old_node.properties):
                if old_fact.property_name in new_properties:
                    continue
                property_location = f"{node_location}.properties.{property_index}"
                if not cls._location_was_invalid(property_location, issue_locations):
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_FACT",
                            message=(
                                f"Repair removed a previously valid property '{old_fact.property_name}' "
                                f"from {old_node.class_name}."
                            ),
                            location=property_location,
                            retryable=True,
                        )
                    )

        new_edges = {cls._edge_key(edge) for edge in new_canonical_fragment.edges}
        for edge_index, old_edge in enumerate(previous_canonical_fragment.edges):
            edge_location = f"edges.{edge_index}"
            if (
                cls._edge_key(old_edge) not in new_edges
                and not cls._location_was_invalid(edge_location, issue_locations)
            ):
                issues.append(
                    ValidationIssue(
                        code="REPAIR_DROPPED_VALID_EDGE",
                        message=(
                            f"Repair removed a previously valid edge '{old_edge.edge_name}' "
                            "that was not part of the validation failure."
                        ),
                        location=edge_location,
                        retryable=True,
                    )
                )
        logger.info(
            "REPAIR_DIFF previousNodes=%s newNodes=%s rejectedRemovals=%s",
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
        """Extract only the valid nodes, properties, and edges from a compiled fragment."""
        issue_locs = {
            str(item.get("location") if isinstance(item, dict) else item.location or "")
            for item in issues
        }
        valid_nodes = []
        valid_node_keys = set()
        for idx, node in enumerate(fragment.nodes):
            node_loc = f"nodes.{idx}"
            if cls._location_was_invalid(node_loc, issue_locs):
                valid_props = []
                for prop_idx, prop in enumerate(node.properties):
                    prop_loc = f"{node_loc}.properties.{prop_idx}"
                    if not cls._location_was_invalid(prop_loc, issue_locs):
                        valid_props.append(prop)
                if valid_props and not any(
                    loc == node_loc or loc == f"{node_loc}.className"
                    for loc in issue_locs
                ):
                    valid_nodes.append(node.model_copy(update={"properties": valid_props}))
                    valid_node_keys.add(node.temp_id)
            else:
                valid_nodes.append(node)
                valid_node_keys.add(node.temp_id)

        valid_edges = []
        for idx, edge in enumerate(fragment.edges):
            edge_loc = f"edges.{idx}"
            if not cls._location_was_invalid(edge_loc, issue_locs):
                if (
                    edge.source_temp_id in valid_node_keys
                    and edge.target_temp_id in valid_node_keys
                ):
                    valid_edges.append(edge)

        if not valid_nodes and not valid_edges:
            return None

        return GraphPatchFragment(
            ontology_version=fragment.ontology_version,
            nodes=valid_nodes,
            edges=valid_edges,
            coverage=[],
            warnings=[],
        )

    @staticmethod
    def _edge_key(edge: Any) -> tuple[str, str, str]:
        return edge.edge_name, edge.source_temp_id, edge.target_temp_id

    @staticmethod
    def _location_was_invalid(prefix: str, locations: set[str]) -> bool:
        return any(
            location == prefix
            or location.startswith(prefix + ".")
            or prefix.startswith(location + ".")
            for location in locations
            if location
        )


__all__ = ["RepairGuard"]
