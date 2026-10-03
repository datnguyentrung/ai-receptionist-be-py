"""Workspace-wide semantic readiness validation and strict graph compilation."""

import hashlib
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    OntologyProjection,
    PropertyFact,
    ValidationIssue,
)
from app.services.ingestion.fact_references import build_fact_index, canonical_json
from app.services.ingestion.ontology import OntologyRegistry
from app.services.ingestion.repair_guard import RepairGuard
from app.services.ingestion.repository import Workspace, workspace_chunks


@dataclass
class ReadinessResult:
    fragment: GraphPatchFragment | None
    issues_by_batch: dict[int, list[ValidationIssue]] = field(default_factory=dict)

    @property
    def issues(self) -> list[ValidationIssue]:
        return [issue for issues in self.issues_by_batch.values() for issue in issues]

    @property
    def digest(self) -> str | None:
        if self.fragment is None or self.issues:
            return None
        payload = self.fragment.model_dump(by_alias=True, mode="json")
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class WorkspaceReadinessValidator:
    """The single seam for all semantic invariants required before persistence."""

    def validate(
        self, workspace: Workspace, projection: OntologyProjection
    ) -> ReadinessResult:
        fragments: list[tuple[int, GraphPatchFragment]] = []
        issues_by_batch: dict[int, list[ValidationIssue]] = {}
        for batch in workspace.batches:
            if not batch.graph_fragment:
                issues_by_batch.setdefault(batch.batch_index, []).append(
                    ValidationIssue(
                        code="MISSING_STAGED_GRAPH_FRAGMENT",
                        message="Staged batch has no graph fragment",
                        location=f"batches.{batch.batch_index}",
                        retryable=True,
                    )
                )
                continue
            try:
                fragments.append(
                    (
                        batch.batch_index,
                        GraphPatchFragment.model_validate(batch.graph_fragment),
                    )
                )
            except ValidationError as exc:
                issues_by_batch.setdefault(batch.batch_index, []).extend(
                    ValidationIssue(
                        code="INVALID_STAGED_GRAPH_FRAGMENT",
                        message=item["msg"],
                        location=".".join(str(part) for part in item["loc"]),
                        retryable=True,
                    )
                    for item in exc.errors()
                )

        materialized = [fragment for _, fragment in fragments]
        facts = build_fact_index(materialized)
        external_types = {
            node.temp_id: node.class_name
            for fragment in materialized
            for node in fragment.nodes
        }
        registry = OntologyRegistry(projection)
        batches = {batch.batch_index: batch for batch in workspace.batches}
        for batch_index, fragment in fragments:
            batch = batches[batch_index]
            batch_issues = registry.validate_fragment(
                fragment,
                workspace_chunks(workspace, batch.chunk_indexes),
                external_node_types=external_types,
                available_facts=facts,
                artifact_name=getattr(workspace.document, "name", None),
            )
            if batch.validated_baseline:
                try:
                    baseline = GraphPatchFragment.model_validate(batch.validated_baseline)
                    batch_issues.extend(
                        RepairGuard.compare(
                            previous_canonical_fragment=baseline,
                            new_canonical_fragment=fragment,
                        )
                    )
                except ValidationError as exc:
                    batch_issues.append(
                        ValidationIssue(
                            code="INVALID_REPAIR_BASELINE",
                            message=str(exc),
                            location="validatedBaseline",
                        )
                    )
            if batch_issues:
                issues_by_batch.setdefault(batch_index, []).extend(batch_issues)

        merged, merge_issues = self._strict_merge(fragments, projection)
        for batch_index, issue in merge_issues:
            issues_by_batch.setdefault(batch_index, []).append(issue)
        return ReadinessResult(
            fragment=None if issues_by_batch else merged,
            issues_by_batch=issues_by_batch,
        )

    @classmethod
    def _strict_merge(
        cls,
        fragments: list[tuple[int, GraphPatchFragment]],
        projection: OntologyProjection,
    ) -> tuple[GraphPatchFragment, list[tuple[int, ValidationIssue]]]:
        contracts = {
            (item["entityType"], item["technicalName"]): item
            for item in projection.properties
        }
        nodes: dict[str, Any] = {}
        node_batches: dict[str, int] = {}
        issues: list[tuple[int, ValidationIssue]] = []
        coverage = []
        warnings: list[str] = []
        for batch_index, fragment in fragments:
            coverage.extend(item.model_copy(deep=True) for item in fragment.coverage)
            warnings.extend(fragment.warnings)
            for node_index, incoming in enumerate(fragment.nodes):
                key = incoming.temp_id
                current = nodes.get(key)
                if current is None:
                    current = incoming.model_copy(deep=True, update={"properties": []})
                    nodes[key] = current
                    node_batches[key] = batch_index
                elif (
                    current.class_name != incoming.class_name
                    or canonical_json(current.identity) != canonical_json(incoming.identity)
                ):
                    issues.append(
                        (
                            batch_index,
                            ValidationIssue(
                                code="CANONICAL_ENTITY_CONFLICT",
                                message=f"Canonical entity {key} changed class or identity",
                                location=f"nodes[{key}]",
                            ),
                        )
                    )
                    continue
                cls._merge_node_facts(
                    current,
                    incoming,
                    contracts,
                    batch_index,
                    node_index,
                    issues,
                )
                current.evidence = cls._dedupe_models(
                    [*current.evidence, *incoming.evidence]
                )

        edge_map: dict[tuple[str, str, str], Any] = {}
        for batch_index, fragment in fragments:
            for edge_index, incoming in enumerate(fragment.edges):
                key = (
                    incoming.edge_name,
                    incoming.source_temp_id,
                    incoming.target_temp_id,
                )
                current = edge_map.get(key)
                if current is None:
                    edge_map[key] = incoming.model_copy(deep=True)
                elif canonical_json(current.properties) != canonical_json(incoming.properties):
                    issues.append(
                        (
                            batch_index,
                            ValidationIssue(
                                code="EDGE_PROPERTY_CONFLICT",
                                message=f"Edge {incoming.edge_name} has conflicting properties",
                                location=f"edges.{edge_index}.properties",
                            ),
                        )
                    )
                else:
                    current.evidence = cls._dedupe_models(
                        [*current.evidence, *incoming.evidence]
                    )

        return (
            GraphPatchFragment(
                ontology_version=projection.version_id,
                nodes=list(nodes.values()),
                edges=list(edge_map.values()),
                coverage=coverage,
                warnings=list(dict.fromkeys(warnings)),
            ),
            issues,
        )

    @classmethod
    def _merge_node_facts(
        cls,
        current: Any,
        incoming: Any,
        contracts: dict[tuple[str, str], dict[str, Any]],
        batch_index: int,
        node_index: int,
        issues: list[tuple[int, ValidationIssue]],
    ) -> None:
        by_name: dict[str, PropertyFact] = {
            fact.property_name: fact for fact in current.properties
        }
        for property_index, fact in enumerate(incoming.properties):
            existing = by_name.get(fact.property_name)
            if existing is None:
                copied = fact.model_copy(deep=True)
                current.properties.append(copied)
                by_name[fact.property_name] = copied
                continue
            if canonical_json(existing.value) == canonical_json(fact.value):
                existing.evidence = cls._dedupe_models(
                    [*existing.evidence, *fact.evidence]
                )
                continue
            contract = contracts.get((current.class_name, fact.property_name), {})
            if not contract.get("multiValue", False):
                issues.append(
                    (
                        batch_index,
                        ValidationIssue(
                            code="SCALAR_PROPERTY_CONFLICT",
                            message=(
                                f"{current.class_name}.{fact.property_name} has multiple "
                                "distinct values while multiValue=false"
                            ),
                            location=f"nodes.{node_index}.properties.{property_index}.value",
                            retryable=True,
                        ),
                    )
                )
                continue
            values = existing.value if isinstance(existing.value, list) else [existing.value]
            additions = fact.value if isinstance(fact.value, list) else [fact.value]
            for value in additions:
                if canonical_json(value) not in {canonical_json(item) for item in values}:
                    values.append(value)
            existing.value = values
            existing.evidence = cls._dedupe_models([*existing.evidence, *fact.evidence])

    @staticmethod
    def _dedupe_models(items: list[Any]) -> list[Any]:
        result = []
        seen: set[str] = set()
        for item in items:
            key = canonical_json(item.model_dump(by_alias=True, mode="json", exclude_none=True))
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result


__all__ = ["ReadinessResult", "WorkspaceReadinessValidator"]
