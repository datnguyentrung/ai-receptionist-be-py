"""Reject repair submissions that silently remove previously valid semantics."""

import logging

from app.schemas.ingestion_schema import SemanticGraphPatchFragment, ValidationIssue

logger = logging.getLogger(__name__)


class RepairGuard:
    @classmethod
    def compare(
        cls,
        *,
        previous_semantic_fragment: SemanticGraphPatchFragment,
        new_semantic_fragment: SemanticGraphPatchFragment,
        previous_validation_issues: list[dict],
    ) -> list[ValidationIssue]:
        issue_locations = {
            str(item.get("location") or "") for item in previous_validation_issues
        }
        issues: list[ValidationIssue] = []
        if issue_locations and all(".evidence." in location for location in issue_locations):
            issues.extend(
                cls._compare_evidence_only_repair(
                    previous_semantic_fragment,
                    new_semantic_fragment,
                )
            )
            logger.info(
                "REPAIR_DIFF previousNodes=%s newNodes=%s evidenceOnlyRejected=%s",
                len(previous_semantic_fragment.nodes),
                len(new_semantic_fragment.nodes),
                len(issues),
            )
            return issues
        old_nodes = {node.temp_id: (index, node) for index, node in enumerate(previous_semantic_fragment.nodes)}
        new_nodes = {node.temp_id: node for node in new_semantic_fragment.nodes}

        for temp_id, (node_index, old_node) in old_nodes.items():
            node_location = f"nodes.{node_index}"
            if temp_id not in new_nodes:
                if not cls._location_was_invalid(node_location, issue_locations):
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_NODE",
                            message="Repair removed a previously valid node that was not part of the validation failure.",
                            location=node_location,
                            retryable=True,
                        )
                    )
                continue
            new_properties = {
                item.property_name: item for item in new_nodes[temp_id].properties
            }
            for property_index, old_fact in enumerate(old_node.properties):
                if old_fact.property_name in new_properties:
                    continue
                property_location = f"{node_location}.properties.{property_index}"
                if not cls._location_was_invalid(property_location, issue_locations):
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_FACT",
                            message="Repair removed a previously valid property that was not part of the validation failure.",
                            location=property_location,
                            retryable=True,
                        )
                    )

        new_edges = {cls._edge_key(edge) for edge in new_semantic_fragment.edges}
        for edge_index, old_edge in enumerate(previous_semantic_fragment.edges):
            edge_location = f"edges.{edge_index}"
            if (
                cls._edge_key(old_edge) not in new_edges
                and not cls._location_was_invalid(edge_location, issue_locations)
            ):
                issues.append(
                    ValidationIssue(
                        code="REPAIR_DROPPED_VALID_EDGE",
                        message="Repair removed a previously valid edge that was not part of the validation failure.",
                        location=edge_location,
                        retryable=True,
                    )
                )
        logger.info(
            "REPAIR_DIFF previousNodes=%s newNodes=%s rejectedRemovals=%s",
            len(previous_semantic_fragment.nodes),
            len(new_semantic_fragment.nodes),
            len(issues),
        )
        return issues

    @staticmethod
    def _edge_key(edge) -> tuple[str, str, str]:
        return edge.edge_name, edge.source_temp_id, edge.target_temp_id

    @classmethod
    def _compare_evidence_only_repair(
        cls,
        previous: SemanticGraphPatchFragment,
        new: SemanticGraphPatchFragment,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        old_nodes = {node.temp_id: (index, node) for index, node in enumerate(previous.nodes)}
        new_nodes = {node.temp_id: node for node in new.nodes}
        for temp_id, (node_index, old_node) in old_nodes.items():
            if temp_id not in new_nodes:
                issues.append(
                    ValidationIssue(
                        code="REPAIR_MUTATED_UNRELATED_NODE",
                        message="Repair for evidence-only failures must not remove nodes.",
                        location=f"nodes.{node_index}",
                        retryable=True,
                    )
                )
                continue
            new_properties = {
                item.property_name: item for item in new_nodes[temp_id].properties
            }
            old_property_names = {item.property_name for item in old_node.properties}
            added_properties = set(new_properties) - old_property_names
            for property_name in sorted(added_properties):
                issues.append(
                    ValidationIssue(
                        code="REPAIR_MUTATED_UNRELATED_NODE",
                        message="Repair for evidence-only failures must not add properties.",
                        location=f"nodes.{node_index}.properties.{property_name}",
                        retryable=True,
                    )
                )
            for property_index, old_fact in enumerate(old_node.properties):
                new_fact = new_properties.get(old_fact.property_name)
                if new_fact is None:
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_FACT",
                            message="Repair removed a previously valid property that was not part of the validation failure.",
                            location=f"nodes.{node_index}.properties.{property_index}",
                            retryable=True,
                        )
                    )
                elif old_fact.value != new_fact.value:
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_MUTATED_UNRELATED_NODE",
                            message="Repair for evidence-only failures must not change property values.",
                            location=f"nodes.{node_index}.properties.{property_index}.value",
                            retryable=True,
                        )
                    )
        added_nodes = set(new_nodes) - set(old_nodes)
        for temp_id in sorted(added_nodes):
            issues.append(
                ValidationIssue(
                    code="REPAIR_MUTATED_UNRELATED_NODE",
                    message="Repair for evidence-only failures must not add nodes.",
                    location=f"nodes.{temp_id}",
                    retryable=True,
                )
            )
        if cls._node_shape(previous) != cls._node_shape(new):
            issues.append(
                ValidationIssue(
                    code="REPAIR_MUTATED_UNRELATED_NODE",
                    message="Repair for evidence-only failures must not change nodes or property values.",
                    location="nodes",
                    retryable=True,
                )
            )
        old_edges = [cls._edge_signature(edge) for edge in previous.edges]
        new_edges = [cls._edge_signature(edge) for edge in new.edges]
        if old_edges != new_edges:
            issues.append(
                ValidationIssue(
                    code="REPAIR_MUTATED_UNRELATED_EDGE",
                    message="Repair for evidence-only failures must not change edge names or endpoints.",
                    location="edges",
                    retryable=True,
                )
            )
        if [item.model_dump(mode="json") for item in previous.coverage] != [
            item.model_dump(mode="json") for item in new.coverage
        ]:
            issues.append(
                ValidationIssue(
                    code="REPAIR_MUTATED_UNRELATED_COVERAGE",
                    message="Repair for evidence-only failures must not change coverage.",
                    location="coverage",
                    retryable=True,
                )
            )
        return issues

    @staticmethod
    def _node_shape(fragment: SemanticGraphPatchFragment) -> list[tuple]:
        result = []
        for node in fragment.nodes:
            result.append((node.temp_id, node.class_name, node.confidence))
        return result

    @staticmethod
    def _edge_signature(edge) -> tuple:
        properties = tuple((item.property_name, item.value) for item in edge.properties)
        return (
            edge.edge_name,
            edge.source_temp_id,
            edge.target_temp_id,
            properties,
            edge.confidence,
        )

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
