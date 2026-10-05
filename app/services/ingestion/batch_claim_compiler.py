"""Deep module: compile atomic claim ledgers into canonical graph fragments and schema gaps."""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.schemas.ingestion_schema import (
    ChunkCoverage,
    DuplicateFactClaim,
    Evidence,
    GraphEdge,
    GraphNode,
    GraphPatchFragment,
    OntologyProjection,
    PreparedChunk,
    PropertyFact,
    SemanticBatchExtraction,
    SemanticEntity,
)
from app.services.ingestion.evidence_guard import (
    EvidenceGroundingEngine,
    property_value_supported_by_evidence,
    validate_property_grounding,
)
from app.services.ingestion.identity_resolver import (
    IdentityResolutionError,
    OntologyIdentityResolver,
)
from app.services.ingestion.ontology import OntologyRegistry, _matches_contract

logger = logging.getLogger(__name__)


@dataclass
class CompiledBatchResult:
    """Canonical compiler output produced exclusively from claims and ontology."""

    fragment: GraphPatchFragment | None
    canonical_claims: list[dict[str, Any]] = field(default_factory=list)
    schema_gaps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


class BatchClaimCompiler:
    """Pure in-memory compiler for First-Pass Batch Ingestion."""

    def compile(
        self,
        extraction: SemanticBatchExtraction,
        ontology_projection: OntologyProjection,
        staged_context: dict[str, Any] | None = None,
        source_chunks: list[PreparedChunk] | None = None,
    ) -> CompiledBatchResult:
        source_chunks = source_chunks or []
        staged_context = staged_context or {}
        staged_entities = staged_context.get("staged_entities") or staged_context.get("entities") or {}
        staged_facts = staged_context.get("staged_facts") or staged_context.get("facts") or {}

        registry = OntologyRegistry(ontology_projection)
        resolver = OntologyIdentityResolver(ontology_projection)
        grounding = EvidenceGroundingEngine()

        errors: list[dict[str, Any]] = []
        warnings: list[str] = list(extraction.warnings)
        schema_gaps: list[dict[str, Any]] = []
        canonical_claims: list[dict[str, Any]] = []

        chunk_by_index: dict[int, PreparedChunk] = {
            chunk.chunk_index: chunk for chunk in source_chunks
        }
        ledger_by_index: dict[int, Any] = {}

        # 1. Chunk accounting: check exactly one ledger per input chunk
        for chunk_ledger in extraction.chunks:
            idx = chunk_ledger.chunk_index
            if idx not in chunk_by_index:
                errors.append({
                    "code": "UNKNOWN_CHUNK_INDEX",
                    "message": f"Chunk {idx} does not exist in the source batch",
                    "location": f"chunks[{idx}]",
                    "retryable": False,
                })
                continue
            if idx in ledger_by_index:
                errors.append({
                    "code": "DUPLICATE_CHUNK_LEDGER",
                    "message": f"Chunk {idx} has multiple ledger entries",
                    "location": f"chunks[{idx}]",
                    "retryable": False,
                })
                continue
            ledger_by_index[idx] = chunk_ledger

        for expected_idx in chunk_by_index:
            if expected_idx not in ledger_by_index:
                errors.append({
                    "code": "MISSING_CHUNK_LEDGER",
                    "message": f"Chunk {expected_idx} is missing from batch ledger",
                    "location": "chunks",
                    "retryable": False,
                })

        # 2. Check claim uniqueness and semantic no-fact decisions
        seen_claim_ids: set[str] = set()
        for chunk_ledger in extraction.chunks:
            if not chunk_ledger.claims:
                if not chunk_ledger.no_relevant_fact_reason or not chunk_ledger.no_relevant_fact_reason.strip():
                    errors.append({
                        "code": "MISSING_NO_RELEVANT_FACT_REASON",
                        "message": (
                            f"Chunk {chunk_ledger.chunk_index} has no claims but is missing "
                            "noRelevantFactReason"
                        ),
                        "location": f"chunks[{chunk_ledger.chunk_index}].noRelevantFactReason",
                        "retryable": False,
                    })
            for claim in chunk_ledger.claims:
                if not claim.claim_id or not claim.claim_id.strip():
                    errors.append({
                        "code": "EMPTY_CLAIM_ID",
                        "message": f"Claim in chunk {chunk_ledger.chunk_index} has empty claimId",
                        "location": f"chunks[{chunk_ledger.chunk_index}].claims",
                        "retryable": False,
                    })
                elif claim.claim_id in seen_claim_ids:
                    errors.append({
                        "code": "DUPLICATE_CLAIM_ID",
                        "message": f"Claim ID '{claim.claim_id}' is duplicated across the batch",
                        "location": f"claims.{claim.claim_id}",
                        "retryable": False,
                    })
                else:
                    seen_claim_ids.add(claim.claim_id)

        # 3. Entity declarations
        declared_entities: dict[str, SemanticEntity] = {}
        for entity in extraction.entities:
            temp_id = entity.temp_id.strip() if entity.temp_id else ""
            if not temp_id and not entity.entity_ref:
                errors.append({
                    "code": "INVALID_ENTITY_DECLARATION",
                    "message": "Entity declaration must have tempId or entityRef",
                    "location": "entities",
                    "retryable": False,
                })
                continue
            if temp_id:
                if temp_id in declared_entities:
                    errors.append({
                        "code": "DUPLICATE_ENTITY_TEMP_ID",
                        "message": f"Entity tempId '{temp_id}' declared more than once",
                        "location": f"entities.{temp_id}",
                        "retryable": False,
                    })
                    continue
                canonical_class = registry.resolve_entity_name(entity.class_name) or entity.class_name
                if canonical_class not in registry.entity_types:
                    errors.append({
                        "code": "UNKNOWN_ENTITY_TYPE",
                        "message": f"Entity class '{entity.class_name}' is unknown in ontology",
                        "location": f"entities.{temp_id}.className",
                        "retryable": False,
                    })
                    continue
                declared_entities[temp_id] = entity.model_copy(update={"class_name": canonical_class})

        # Also register staged entity references into declared entities lookup if referenced
        for entity in extraction.entities:
            if entity.entity_ref and not entity.temp_id:
                ref = entity.entity_ref
                staged = staged_entities.get(ref) or staged_entities.get(f"entity:{ref}")
                if not staged:
                    errors.append({
                        "code": "UNKNOWN_ENTITY_REFERENCE",
                        "message": f"Referenced staged entity '{ref}' does not exist",
                        "location": f"entities.{ref}",
                        "retryable": False,
                    })
                else:
                    declared_entities[ref] = SemanticEntity(
                        temp_id=ref,
                        class_name=staged["className"],
                        entity_ref=ref,
                    )

        # Helper to resolve an entity ref (tempId or entityRef)
        def resolve_entity_class(ref: str) -> str | None:
            if ref in declared_entities:
                return declared_entities[ref].class_name
            staged = staged_entities.get(ref) or staged_entities.get(f"entity:{ref}")
            if staged:
                return staged["className"]
            return None

        # 4. Process Claims
        mapped_properties_by_entity: dict[str, list[PropertyFact]] = {}
        mapped_edges: list[GraphEdge] = []
        duplicate_claims_by_chunk: dict[int, list[DuplicateFactClaim]] = {}
        auto_downgraded_schema_gap_claim_ids: set[str] = set()

        for chunk_ledger in extraction.chunks:
            source_chunk = chunk_by_index.get(chunk_ledger.chunk_index)
            for claim in chunk_ledger.claims:
                # Grounding check
                if claim.evidence.chunk_index != chunk_ledger.chunk_index:
                    errors.append({
                        "code": "EVIDENCE_CHUNK_MISMATCH",
                        "message": (
                            f"Claim '{claim.claim_id}' in chunk {chunk_ledger.chunk_index} "
                            f"has evidence referencing chunk {claim.evidence.chunk_index}"
                        ),
                        "location": f"claims.{claim.claim_id}.evidence.chunkIndex",
                        "retryable": False,
                    })
                    continue

                if source_chunk:
                    resolution = grounding.resolve(source_chunk, claim.evidence)
                    if resolution.evidence is None:
                        errors.append({
                            "code": resolution.error_code or "EVIDENCE_NOT_GROUNDED",
                            "message": (
                                f"Claim '{claim.claim_id}': "
                                f"{resolution.message or 'evidence is not grounded'}"
                            ),
                            "location": f"claims.{claim.claim_id}.evidence",
                            "retryable": False,
                        })
                        continue
                    # Source-owned evidence is authoritative; model-provided text is replaced.
                    claim.evidence = resolution.evidence

                # Capture canonical claim only after evidence has been resolved.
                canonical_claims.append(claim.model_dump(by_alias=True, mode="json"))

                # Process outcome
                if claim.outcome == "MAPPED":
                    if claim.mapping is None:
                        errors.append({
                            "code": "MAPPED_CLAIM_MISSING_MAPPING",
                            "message": f"Claim '{claim.claim_id}' marked MAPPED but mapping is missing",
                            "location": f"claims.{claim.claim_id}.mapping",
                            "retryable": False,
                        })
                        continue

                    if claim.mapping.kind == "PROPERTY":
                        entity_ref = claim.mapping.entity_ref
                        entity_class = resolve_entity_class(entity_ref)
                        if not entity_class:
                            errors.append({
                                "code": "UNRESOLVED_ENTITY_REFERENCE",
                                "message": (
                                    f"Property mapping in claim '{claim.claim_id}' references "
                                    f"unknown entity '{entity_ref}'"
                                ),
                                "location": f"claims.{claim.claim_id}.mapping.entityRef",
                                "retryable": False,
                            })
                            continue

                        canonical_prop = (
                            registry.resolve_property_name(entity_class, claim.mapping.property_name)
                            or claim.mapping.property_name
                        )
                        contract = registry.properties.get((entity_class, canonical_prop))
                        if contract is None:
                            warnings.append(
                                f"Claim '{claim.claim_id}': property "
                                f"'{claim.mapping.property_name}' unknown for entity "
                                f"'{entity_class}' - auto-downgraded to SCHEMA_GAP"
                            )
                            gap_data = {
                                "kind": "PROPERTY",
                                "entityRef": claim.mapping.entity_ref,
                                "entityType": entity_class,
                                "technicalName": canonical_prop,
                                "displayName": claim.mapping.property_name,
                                "dataType": "STRING",
                                "value": claim.mapping.value,
                                "reason": (
                                    f"Property '{claim.mapping.property_name}' is not defined "
                                    f"for entity '{entity_class}' in the current ontology. "
                                    f"Auto-downgraded from MAPPED."
                                ),
                                "claimId": claim.claim_id,
                                "statement": claim.statement,
                                "evidence": claim.evidence.model_dump(by_alias=True, mode="json"),
                            }
                            schema_gaps.append(gap_data)
                            auto_downgraded_schema_gap_claim_ids.add(claim.claim_id)
                            continue

                        if not _matches_contract(claim.mapping.value, contract):
                            errors.append({
                                "code": "INVALID_PROPERTY_DATATYPE",
                                "message": (
                                    f"Property '{canonical_prop}' expects {contract['dataType']}, "
                                    f"got {type(claim.mapping.value).__name__} ({claim.mapping.value!r})"
                                ),
                                "location": f"claims.{claim.claim_id}.mapping.value",
                                "retryable": False,
                            })
                            continue

                        grounding_res = validate_property_grounding(
                            claim.mapping.value, claim.evidence.text, contract
                        )
                        if not grounding_res.valid:
                            errors.append({
                                "code": grounding_res.error_code or "PROPERTY_VALUE_NOT_SUPPORTED_BY_EVIDENCE",
                                "message": (
                                    grounding_res.error_message or
                                    f"Property '{canonical_prop}' value {claim.mapping.value!r} "
                                    f"cannot be recovered deterministically from claim "
                                    f"'{claim.claim_id}' evidence"
                                ),
                                "location": f"claims.{claim.claim_id}.mapping.value",
                                "retryable": False,
                            })
                            continue

                        prop_fact = PropertyFact(
                            property_name=canonical_prop,
                            value=claim.mapping.value,
                            evidence=[claim.evidence],
                            value_verified=grounding_res.value_verified,
                        )
                        mapped_properties_by_entity.setdefault(entity_ref, []).append(prop_fact)

                    elif claim.mapping.kind == "EDGE":
                        source_ref = claim.mapping.source_ref
                        target_ref = claim.mapping.target_ref
                        source_class = resolve_entity_class(source_ref)
                        target_class = resolve_entity_class(target_ref)
                        if not source_class or not target_class:
                            errors.append({
                                "code": "UNRESOLVED_ENTITY_REFERENCE",
                                "message": (
                                    f"Edge mapping in claim '{claim.claim_id}' has unresolved endpoint "
                                    f"(source: {source_ref}, target: {target_ref})"
                                ),
                                "location": f"claims.{claim.claim_id}.mapping",
                                "retryable": False,
                            })
                            continue

                        canonical_edge = (
                            registry.resolve_relationship_name(claim.mapping.edge_name)
                            or claim.mapping.edge_name
                        )
                        rel_contracts = registry.relationships.get(canonical_edge)
                        if not rel_contracts:
                            warnings.append(
                                f"Claim '{claim.claim_id}': relationship "
                                f"'{claim.mapping.edge_name}' unknown in ontology - "
                                f"auto-downgraded to SCHEMA_GAP"
                            )
                            gap_data = {
                                "kind": "RELATIONSHIP",
                                "technicalName": canonical_edge,
                                "displayName": claim.mapping.edge_name,
                                "sourceRef": claim.mapping.source_ref,
                                "targetRef": claim.mapping.target_ref,
                                "sourceEntityType": source_class,
                                "targetEntityType": target_class,
                                "cardinality": "MANY_TO_MANY",
                                "reason": (
                                    f"Relationship '{claim.mapping.edge_name}' does not exist "
                                    f"in the current ontology. Auto-downgraded from MAPPED."
                                ),
                                "claimId": claim.claim_id,
                                "statement": claim.statement,
                                "evidence": claim.evidence.model_dump(by_alias=True, mode="json"),
                            }
                            schema_gaps.append(gap_data)
                            auto_downgraded_schema_gap_claim_ids.add(claim.claim_id)
                            continue

                        matching = registry.relationships_by_signature.get(
                            (canonical_edge, source_class, target_class)
                        )
                        if not matching:
                            expected_pairs = [
                                f"{c['sourceEntityType']} -> {c['targetEntityType']}"
                                for c in rel_contracts
                            ]
                            errors.append({
                                "code": "RELATIONSHIP_DOMAIN_RANGE_MISMATCH",
                                "message": (
                                    f"Relationship '{canonical_edge}' ({source_class} -> {target_class}) "
                                    f"violates domain/range; expects {' or '.join(expected_pairs)}"
                                ),
                                "location": f"claims.{claim.claim_id}.mapping",
                                "retryable": False,
                            })
                            continue

                        edge_patch = GraphEdge(
                            edge_name=canonical_edge,
                            source_temp_id=source_ref,
                            target_temp_id=target_ref,
                            properties=claim.mapping.properties or {},
                            evidence=[claim.evidence],
                        )
                        mapped_edges.append(edge_patch)

                elif claim.outcome == "DUPLICATE":
                    if not claim.fact_ref or not claim.fact_ref.strip():
                        errors.append({
                            "code": "UNRESOLVED_DUPLICATE_FACT_REF",
                            "message": f"Claim '{claim.claim_id}' marked DUPLICATE must provide factRef",
                            "location": f"claims.{claim.claim_id}.factRef",
                            "retryable": False,
                        })
                    else:
                        dup = DuplicateFactClaim(
                            fact_ref=claim.fact_ref,
                            evidence=claim.evidence,
                        )
                        duplicate_claims_by_chunk.setdefault(chunk_ledger.chunk_index, []).append(dup)

                elif claim.outcome == "SCHEMA_GAP":
                    if claim.schema_gap is None:
                        errors.append({
                            "code": "SCHEMA_GAP_MISSING_SPEC",
                            "message": f"Claim '{claim.claim_id}' marked SCHEMA_GAP must provide schemaGap object",
                            "location": f"claims.{claim.claim_id}.schemaGap",
                            "retryable": False,
                        })
                    else:
                        if (
                            claim.schema_gap.kind == "PROPERTY"
                            and claim.schema_gap.value is not None
                        ):
                            gap_contract = {
                                "dataType": claim.schema_gap.data_type,
                                "technicalName": claim.schema_gap.technical_name,
                                "constraints": {},
                            }
                            gap_grounding = validate_property_grounding(
                                claim.schema_gap.value, claim.evidence.text, gap_contract
                            )
                            if not gap_grounding.valid:
                                errors.append({
                                    "code": "SCHEMA_GAP_VALUE_NOT_SUPPORTED_BY_EVIDENCE",
                                    "message": (
                                        gap_grounding.error_message or
                                        f"Schema-gap value {claim.schema_gap.value!r} cannot be "
                                        f"recovered deterministically from claim "
                                        f"'{claim.claim_id}' evidence"
                                    ),
                                    "location": f"claims.{claim.claim_id}.schemaGap.value",
                                    "retryable": False,
                                })
                                continue
                        gap_data = claim.schema_gap.model_dump(by_alias=True, mode="json")
                        gap_data["claimId"] = claim.claim_id
                        gap_data["statement"] = claim.statement
                        gap_data["evidence"] = claim.evidence.model_dump(by_alias=True, mode="json")
                        if claim.schema_gap.kind == "PROPERTY":
                            entity_class = resolve_entity_class(claim.schema_gap.entity_ref)
                            if entity_class:
                                gap_data["entityType"] = entity_class
                        elif claim.schema_gap.kind == "RELATIONSHIP":
                            source_class = resolve_entity_class(claim.schema_gap.source_ref)
                            target_class = resolve_entity_class(claim.schema_gap.target_ref)
                            if source_class:
                                gap_data["sourceEntityType"] = source_class
                            if target_class:
                                gap_data["targetEntityType"] = target_class
                        schema_gaps.append(gap_data)

                elif claim.outcome == "AMBIGUOUS":
                    warnings.append(
                        f"Ambiguous claim {claim.claim_id}: {claim.statement} (reason: {claim.reason})"
                    )

        # 5. Entity Identity and Node Assembly
        canonical_nodes: list[GraphNode] = []
        for temp_id, entity in declared_entities.items():
            props = mapped_properties_by_entity.get(temp_id, [])
            staged = staged_entities.get(temp_id) or staged_entities.get(f"entity:{temp_id}")
            if staged:
                # Reusing staged entity
                identity = staged.get("identity") or {}
            else:
                try:
                    identity = resolver.resolve(class_name=entity.class_name, properties=props)
                except IdentityResolutionError as exc:
                    for missing_field in exc.missing_fields:
                        errors.append({
                            "code": "IDENTITY_SOURCE_PROPERTY_MISSING",
                            "message": (
                                f"Ontology requires identity field '{missing_field}' for entity "
                                f"'{entity.class_name}' ({temp_id}), but no mapped claim provides it"
                            ),
                            "location": f"entities.{temp_id}.properties",
                            "retryable": False,
                        })
                    identity = {}

            all_evidence: list[Evidence] = []
            for p in props:
                all_evidence.extend(p.evidence)

            canonical_nodes.append(
                GraphNode(
                    temp_id=temp_id,
                    class_name=entity.class_name,
                    identity=identity,
                    properties=props,
                    evidence=all_evidence,
                )
            )

        # 6. Canonical Coverage Synthesis (Section 6 of plan)
        coverage_list: list[ChunkCoverage] = []
        for chunk_ledger in extraction.chunks:
            idx = chunk_ledger.chunk_index
            claims = chunk_ledger.claims
            if not claims:
                decision = "NO_RELEVANT_FACT"
                reason = chunk_ledger.no_relevant_fact_reason or "No relevant facts identified in chunk"
            else:
                outcomes = {
                    "SCHEMA_GAP" if c.claim_id in auto_downgraded_schema_gap_claim_ids else c.outcome
                    for c in claims
                }
                if outcomes == {"MAPPED"}:
                    decision = "MAPPED"
                    reason = "All claims mapped to ontology"
                elif outcomes == {"DUPLICATE"}:
                    decision = "DUPLICATE_EVIDENCE"
                    reason = "All claims duplicate existing facts"
                elif outcomes == {"SCHEMA_GAP"}:
                    decision = "UNSUPPORTED_BY_ONTOLOGY"
                    reason = "All claims represent ontology schema gaps"
                elif outcomes == {"AMBIGUOUS"}:
                    decision = "AMBIGUOUS"
                    reason = "All claims are ambiguous"
                elif "MAPPED" in outcomes and "SCHEMA_GAP" in outcomes:
                    decision = "PARTIALLY_MAPPED"
                    reason = "Chunk contains both mapped claims and schema gaps"
                elif "MAPPED" in outcomes and "AMBIGUOUS" in outcomes:
                    decision = "PARTIALLY_MAPPED"
                    reason = "Chunk contains both mapped and ambiguous claims"
                elif "DUPLICATE" in outcomes and "SCHEMA_GAP" in outcomes:
                    decision = "PARTIALLY_MAPPED"
                    reason = "Chunk contains duplicate facts and schema gaps"
                else:
                    decision = "PARTIALLY_MAPPED"
                    reason = "Chunk contains mixed claim outcomes"

            coverage_list.append(
                ChunkCoverage(
                    chunk_index=idx,
                    decision=decision,
                    reason=reason,
                    duplicate_claims=duplicate_claims_by_chunk.get(idx, []),
                )
            )

        # 7. Final Fragment Assembly
        fragment: GraphPatchFragment | None = None
        if not errors:
            fragment = GraphPatchFragment(
                ontology_version=ontology_projection.version,
                nodes=canonical_nodes,
                edges=mapped_edges,
                coverage=coverage_list,
                warnings=warnings,
            )

        return CompiledBatchResult(
            fragment=fragment,
            canonical_claims=canonical_claims,
            schema_gaps=schema_gaps,
            warnings=warnings,
            errors=errors,
        )


__all__ = ["BatchClaimCompiler", "CompiledBatchResult"]
