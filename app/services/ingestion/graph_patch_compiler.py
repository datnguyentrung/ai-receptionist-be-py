"""Compile an LLM-facing semantic fragment into a canonical graph fragment."""

from pydantic import BaseModel, Field

from app.schemas.ingestion_schema import (
    GraphEdge,
    GraphNode,
    GraphPatchFragment,
    OntologyProjection,
    SemanticGraphPatchFragment,
    ValidationIssue,
)
from app.services.ingestion.repository import stable_entity_key
from app.services.ingestion.identity_resolver import (
    IdentityResolutionError,
    OntologyIdentityResolver,
)


class GraphPatchCompileResult(BaseModel):
    fragment: GraphPatchFragment | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


class GraphPatchCompiler:
    def compile(
        self,
        semantic_fragment: SemanticGraphPatchFragment,
        projection: OntologyProjection,
        *,
        staged_entities: dict[str, dict] | None = None,
    ) -> GraphPatchCompileResult:
        resolver = OntologyIdentityResolver(projection)
        staged_entities = staged_entities or {}
        issues: list[ValidationIssue] = []
        canonical_nodes: list[GraphNode] = []
        local_entity_keys: dict[str, str] = {}
        node_types: dict[str, str] = {
            item["stableKey"]: item["className"] for item in staged_entities.values()
        }

        for node_index, node in enumerate(semantic_fragment.nodes):
            try:
                identity = resolver.resolve(
                    class_name=node.class_name,
                    properties=node.properties,
                )
            except IdentityResolutionError as exc:
                if exc.unknown_class:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_ENTITY_TYPE",
                            message=f"Unknown entity type: {node.class_name}",
                            location=f"nodes.{node_index}.className",
                        )
                    )
                else:
                    for field in exc.missing_fields:
                        issues.append(
                            ValidationIssue(
                                code="IDENTITY_SOURCE_PROPERTY_MISSING",
                                message=(
                                    f"Ontology requires identity field '{field}' for "
                                    f"{node.class_name}, but the semantic fragment does "
                                    "not contain that property."
                                ),
                                location=f"nodes.{node_index}.properties",
                                retryable=True,
                            )
                        )
                continue
            key = stable_entity_key(node.class_name, identity)
            local_entity_keys[node.temp_id] = key
            node_types[key] = node.class_name
            canonical_nodes.append(
                GraphNode(
                    temp_id=key,
                    class_name=node.class_name,
                    identity=identity,
                    properties=node.properties,
                    evidence=node.evidence,
                    confidence=node.confidence,
                )
            )

        if issues:
            return GraphPatchCompileResult(issues=issues)

        canonical_edges: list[GraphEdge] = []
        relationships = {
            item["technicalName"]: item for item in projection.relationships
        }
        for edge_index, edge in enumerate(semantic_fragment.edges):
            source_key = self._resolve_endpoint(
                edge.source_temp_id, local_entity_keys, staged_entities
            )
            target_key = self._resolve_endpoint(
                edge.target_temp_id, local_entity_keys, staged_entities
            )
            if source_key is None:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_ENTITY_REFERENCE",
                        message=f"Unknown edge source reference: {edge.source_temp_id}",
                        location=f"edges.{edge_index}.sourceTempId",
                        retryable=True,
                    )
                )
            if target_key is None:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_ENTITY_REFERENCE",
                        message=f"Unknown edge target reference: {edge.target_temp_id}",
                        location=f"edges.{edge_index}.targetTempId",
                        retryable=True,
                    )
                )
            contract = relationships.get(edge.edge_name)
            if contract and source_key and target_key:
                source_type = node_types.get(source_key)
                target_type = node_types.get(target_key)
                if (
                    source_type != contract["sourceEntityType"]
                    or target_type != contract["targetEntityType"]
                ):
                    issues.append(
                        ValidationIssue(
                            code="RELATIONSHIP_DOMAIN_RANGE_MISMATCH",
                            message=f"{edge.edge_name} expects {contract['sourceEntityType']} -> {contract['targetEntityType']}",
                            location=f"edges.{edge_index}",
                            retryable=True,
                        )
                    )
            if source_key and target_key:
                canonical_edges.append(
                    GraphEdge(
                        edge_name=edge.edge_name,
                        source_temp_id=source_key,
                        target_temp_id=target_key,
                        properties={
                            item.property_name: item.value for item in edge.properties
                        },
                        evidence=edge.evidence,
                        confidence=edge.confidence,
                    )
                )
        if issues:
            return GraphPatchCompileResult(issues=issues)
        return GraphPatchCompileResult(
            fragment=GraphPatchFragment(
                ontology_version=semantic_fragment.ontology_version,
                nodes=canonical_nodes,
                edges=canonical_edges,
                coverage=semantic_fragment.coverage,
                warnings=semantic_fragment.warnings,
            )
        )

    @staticmethod
    def _resolve_endpoint(
        value: str,
        local_entity_keys: dict[str, str],
        staged_entities: dict[str, dict],
    ) -> str | None:
        if value in local_entity_keys:
            return local_entity_keys[value]
        if value.startswith("entity:"):
            staged = staged_entities.get(value)
            return staged["stableKey"] if staged else None
        return None


__all__ = ["GraphPatchCompileResult", "GraphPatchCompiler"]
