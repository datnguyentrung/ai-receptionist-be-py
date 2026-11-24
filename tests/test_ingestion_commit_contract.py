"""Commit gate and source assertion regressions."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.tools.ingestion_tools import registry, workflow
from app.enums import IngestionJobStatus
from app.schemas.ingestion.coverage import PreparedChunk
from app.schemas.ingestion.document import DocumentChunk
from app.schemas.ingestion.graph_patch import GraphNode, GraphPatchFragment
from app.schemas.ingestion.grounding import Evidence, PropertyFact
from app.schemas.ingestion.ontology import ActiveOntology
from app.services.ingestion.engine import operations
from app.services.ingestion.engine.assertions import (
    assertion_key,
    compare_assertions,
    temporal_interval,
)
from app.services.ingestion.engine.entity_resolution import resolve_entities
from app.services.ingestion.engine.graph_store import Neo4jIngestionStore
from app.services.ingestion.engine.operations import fill
from app.services.ingestion.engine.repository import IngestionRepository
from app.services.ingestion.schema.ontology import validate_coverage_integrity
from app.utils import workspace_fingerprint


def _fact(value, *, start=None, end=None):
    return {
        "factId": "new", "factType": "PROPERTY", "predicate": "rank",
        "statement": "rank", "valueJson": f'"{value}"', "subjectKey": "person:a",
        "evidenceChunkIds": ["chunk"], "validFrom": start, "validTo": end,
    }


def _old(value, *, start=None, end=None):
    return {
        "factId": "old", "factType": "PROPERTY", "predicate": "rank",
        "valueJson": f'"{value}"', "subjectKey": "person:a",
        "sourceAnchors": ["docs/old.md:L1"], "documentName": "old.md",
        "validFrom": start, "validTo": end,
    }


def test_support_conflict_and_disjoint_time() -> None:
    anchors = {"chunk": "docs/new.md:L2"}
    support, conflicts, rows = compare_assertions([_fact("black")], [_old("black")], anchors)
    assert len(support) == 1 and not conflicts and rows[0]["classification"] == "SUPPORTED"

    support, conflicts, rows = compare_assertions([_fact("black")], [_old("red")], anchors)
    assert not support and len(conflicts) == 1 and rows[0]["classification"] == "CONFLICT"
    assert conflicts[0]["oldSourceAnchors"] == ["docs/old.md:L1"]
    assert conflicts[0]["newSourceAnchors"] == ["docs/new.md:L2"]

    support, conflicts, rows = compare_assertions(
        [_fact("black", start="2020-01-01", end="2021-01-01")],
        [_old("red", start="2017-01-01", end="2018-01-01")], anchors,
    )
    assert not support and not conflicts and rows[0]["classification"] == "ADDED"


def test_year_is_explicit_and_unambiguous() -> None:
    assert temporal_interval("rank", "black", [{"text": "In 2017 earned the rank"}]) == (
        "2017-01-01", "2018-01-01"
    )
    assert temporal_interval("rank", "black", [{"text": "In 2017 and 2018"}]) == (None, None)


def test_duplicate_evidence_accepts_committed_history_without_staged_nodes() -> None:
    fragment = GraphPatchFragment(
        ontologyVersion="v1",
        nodes=[],
        edges=[],
        coverage=[{
            "chunkIndex": 0,
            "decision": "DUPLICATE_EVIDENCE",
            "reason": "The five-minute rule already exists in committed knowledge.",
        }],
        warnings=[],
    )
    chunks = [PreparedChunk(
        chunk_id="chunk-1", chunk_index=0,
        text="Học viên phải có mặt trước giờ tập 5 phút.",
        content_hash="hash", token_count=8, source_anchor="doc.md#L1",
    )]
    before = validate_coverage_integrity(fragment, chunks)
    after = validate_coverage_integrity(
        fragment, chunks, prior_fact_available=True
    )
    assert [item.code for item in before] == ["DUPLICATE_WITHOUT_PRIOR_FACT"]
    assert after == []


def test_mapped_list_requires_every_source_item_in_property_value() -> None:
    source = (
        "| - Chuẩn bị sân tập.<br>- Quản lý học viên.<br>"
        "- Chuẩn bị thiết bị. |"
    )
    chunk = PreparedChunk(
        chunk_id="chunk-1", chunk_index=0, text=source,
        content_hash="hash", token_count=8, source_anchor="doc.md#L1",
    )
    evidence = Evidence(chunk_index=0, text="- Chuẩn bị sân tập.")
    incomplete = GraphPatchFragment(
        ontologyVersion="v1",
        nodes=[GraphNode(
            tempId="policy-1",
            className="policy",
            identity={"name": "Quy định hoạt động"},
            properties=[PropertyFact(
                propertyName="description", value="- Chuẩn bị sân tập.",
                evidence=[evidence],
            )],
        )],
        edges=[],
        coverage=[{
            "chunkIndex": 0, "decision": "MAPPED", "reason": "Đã lưu quy định."
        }],
        warnings=[],
    )
    complete = incomplete.model_copy(deep=True)
    complete.nodes[0].properties[0].value = (
        "- Chuẩn bị sân tập.\n- Quản lý học viên.\n- Chuẩn bị thiết bị."
    )

    issues = validate_coverage_integrity(incomplete, [chunk])
    assert [issue.code for issue in issues] == ["LIST_ITEMS_NOT_REPRESENTED"]
    assert validate_coverage_integrity(complete, [chunk]) == []


def test_fact_write_reuses_legacy_node_by_fact_id() -> None:
    class Result:
        async def consume(self):
            return None

        async def single(self):
            return {"nodes": 1, "edges": 0, "chunks": 1, "facts": 1}

    class Tx:
        queries: list[str] = []

        async def run(self, query, **params):
            self.queries.append(query)
            return Result()

    payload = {
        "version_id": "version-1",
        "document_id": "document-1",
        "document_key": "docs/test.md",
        "document_name": "test.md",
        "ontology_version_id": "ontology-1",
        "embedding_model": "embedding-1",
        "chunks": [{
            "chunkId": "chunk-1", "chunkIndex": 0, "text": "text",
            "contentHash": "hash", "tokenCount": 1, "section": None,
            "pageStart": None, "pageEnd": None, "sourceAnchor": "test.md#L1",
            "scopeKey": "scope", "scopeKeys": ["scope"], "embedding": [0.1],
        }],
        "chunk_links": [],
        "nodes": [{
            "tempId": "node-1", "stableKey": "entity-1",
            "className": "policy", "identityJson": '{"name":"policy"}',
            "searchText": "policy",
        }],
        "mentions": [],
        "edges": [],
        "facts": [{
            "factId": "legacy-fact-id", "assertionKey": "legacy-fact-id",
            "occurrenceId": "occurrence-1", "factType": "PROPERTY",
            "statement": "policy", "predicate": "name", "valueJson": '"policy"',
            "subjectKey": "entity-1", "objectKey": None,
            "embedding": [0.1], "evidenceChunkIds": ["chunk-1"],
            "validFrom": None, "validTo": None, "confidence": None,
        }],
    }

    async def run():
        tx = Tx()
        await Neo4jIngestionStore._write_and_verify(tx, payload, publish=False)
        fact_query = next(
            query for query in tx.queries
            if "MERGE (f:TaekwondoKnowledgeFact" in query
        )
        assert "{factId: fact.factId}" in fact_query
        assert "SET f.assertionKey = fact.assertionKey" in fact_query

    asyncio.run(run())


def test_relationships_with_different_objects_are_not_duplicate_assertions() -> None:
    anchors = {"chunk": "docs/new.md:L2"}
    new = {
        **_fact({}, start=None, end=None),
        "factType": "RELATIONSHIP",
        "predicate": "has_contact_channel",
        "objectKey": "channel:hotline",
    }
    old = {
        **_old({}, start=None, end=None),
        "factType": "RELATIONSHIP",
        "predicate": "has_contact_channel",
        "objectKey": "channel:zalo",
    }
    supports, conflicts, rows = compare_assertions([new], [old], anchors)
    assert not supports
    assert not conflicts
    assert rows[0]["classification"] == "ADDED"


def test_assertion_key_normalizes_text_without_provenance_fields() -> None:
    kwargs = {
        "fact_type": "PROPERTY", "subject_key": "person:a", "predicate": "rank",
    }
    assert assertion_key(**kwargs, value="  black   belt ") == assertion_key(
        **kwargs, value="black belt"
    )


def test_graph_backed_entity_resolution_reuses_hotline_and_fee_but_reviews_policy() -> None:
    hotline = {"stableKey": "new-hotline", "className": "contact_channel", "identityJson":
               '{"channel_type":"Đường dây nóng","value":"0339998191"}', "propertiesJson": "{}"}
    old_hotline = {"stableKey": "old-hotline", "entityType": "contact_channel", "identityJson":
                   '{"channel_type":"Hotline","value":"0339998191"}', "properties": {}}
    fee = {"stableKey": "new-fee", "className": "fee", "identityJson":
           '{"name":"Phí lớp ngoài trời - Quý"}', "propertiesJson":
           '{"amount":400000,"billing_period":"Quý 3 tháng"}'}
    old_fee = {"stableKey": "old-fee", "entityType": "fee", "identityJson":
               '{"name":"Học phí lớp ngoài trời - Đóng theo quý"}', "properties":
               {"amount": 400000, "billing_period": "Quý 3 tháng"}}
    policy = {"stableKey": "new-policy", "className": "policy", "identityJson":
              '{"name":"Quy định đi tập của học viên"}', "propertiesJson":
              '{"description":"Học viên cần có mặt trước giờ tập 5 phút"}'}
    old_policy = {"stableKey": "old-policy", "entityType": "policy", "identityJson":
                  '{"name":"Đi muộn"}', "properties":
                  {"description": "Học viên có mặt trước giờ tập 5 phút"}}
    decisions = resolve_entities(
        [hotline, fee, policy],
        {"new-hotline": [old_hotline], "new-fee": [old_fee], "new-policy": [old_policy]},
    )
    assert [item.decision for item in decisions] == [
        "DETERMINISTIC_REUSE", "DETERMINISTIC_REUSE", "REVIEW_REQUIRED"
    ]
    assert decisions[0].canonical_key == "old-hotline"
    assert decisions[1].canonical_key == "old-fee"


def test_fee_resolution_normalizes_decimal_and_uses_unique_plan_amount() -> None:
    plans = [("quý", 400000), ("tuần", 250000), ("tháng", 450000),
             ("quý", 1000000), ("tuần", 600000), ("tháng", 1100000)]
    extracted, candidates, lookup = [], [], {}
    for index, (period, amount) in enumerate(plans):
        key = f"new-{index}"
        extracted.append({"stableKey": key, "className": "fee", "identityJson":
                          f'{{"name":"Đóng theo {period}"}}', "propertiesJson":
                          f'{{"amount":{amount}.0,"billing_period":"{period}"}}'})
        candidates.append({"stableKey": f"old-{index}", "entityType": "fee", "identityJson":
                           f'{{"name":"Học phí lớp phòng tập - {period}"}}', "properties":
                           {"amount": amount, "billing_period": period}})
        lookup[key] = candidates
    decisions = resolve_entities(extracted, lookup)
    assert [item.decision for item in decisions] == ["DETERMINISTIC_REUSE"] * 6
    assert [item.canonical_key for item in decisions] == [f"old-{index}" for index in range(6)]


def test_entity_review_blocks_commit_then_reuse_updates_canonical_map() -> None:
    class FakeGraph:
        async def preview_payload(self, workspace):
            return {
                "nodes": [{
                    "stableKey": "new-policy",
                    "className": "policy",
                    "entityType": "policy",
                    "identityJson": '{"name":"Đi muộn"}',
                    "propertiesJson": '{"description":"Có mặt trước giờ tập 5 phút"}',
                    "searchText": "policy | Đi muộn | Có mặt trước giờ tập 5 phút",
                }],
                "edges": [],
                "chunks": [],
                "facts": [],
            }

        async def find_entity_candidates(self, requests):
            return {
                item["stableKey"]: [{
                    "stableKey": "old-policy",
                    "entityType": "policy",
                    "identityJson": '{"name":"Đi muộn"}',
                    "properties": {"description": "Có mặt trước giờ tập 5 phút"},
                }]
                for item in requests
            }

        async def current_assertions(self, keys):
            return []

        async def current_entity_keys(self, keys):
            return set()

        async def current_relation_keys(self, edges):
            return set()

    async def run():
        repo = IngestionRepository()
        graph = FakeGraph()
        ontology = ActiveOntology(
            version_id="00000000-0000-0000-0000-000000000001", version="v1"
        )
        chunk = DocumentChunk(
            index=0, source="test.md", content="text", chunk_id="c",
            content_hash="a" * 64, structural_path="root", start_line=1, end_line=1,
        )
        workspace, _, _ = await repo.create_or_resume(
            artifact_name="test.md", content_hash="b" * 64, chunks=[chunk],
            ontology=ontology, document_key="docs/test.md", scope_hint=None,
            skill_digest="s", model_id="m", compiler_version="c", batch_size=1,
            max_batch_chars=100,
        )
        ingestion_id = str(workspace.job.id)
        workspace = replace(
            workspace,
            batches=tuple(
                replace(batch, status="STAGED") for batch in workspace.batches
            ),
        )
        repo._workspaces[ingestion_id] = workspace
        workspace.job.status = IngestionJobStatus.READY
        workspace.job.readiness_fingerprint = workspace_fingerprint(workspace)

        pending = await operations.build_preview(repo, graph, ingestion_id)
        assert pending["pendingEntityReviewCount"] == 1
        review = pending["entityResolutions"][0]
        assert review["reviewId"]
        assert review["reviewDecision"] == "PENDING"
        assert await repo.pending_entity_resolution_reviews(ingestion_id) == 1

        denied = await operations.commit(
            repo, graph, ingestion_id, pending["reportDigest"]
        )
        assert denied["errors"][0]["code"] == "REVIEW_REQUIRED"
        assert denied["terminal"] is False

        workspace = await repo.get_workspace(ingestion_id)
        workspace.job.readiness_fingerprint = "stale-fingerprint"
        await repo.review_entity_resolution(review["reviewId"], "REUSE")
        resolved = await operations.build_preview(repo, graph, ingestion_id)
        assert resolved["pendingEntityReviewCount"] == 0
        assert resolved["canonicalKeyMap"] == {"new-policy": "old-policy"}
        assert resolved["entityResolutions"][0]["reviewDecision"] == "REUSE"
        assert resolved["reportDigest"] != pending["reportDigest"]
        assert workspace.job.readiness_fingerprint == workspace_fingerprint(workspace)
        await repo.record_commit_approval(
            ingestion_id, resolved["reportDigest"],
            user_id="human", session_id="session", function_call_id="call",
        )

    asyncio.run(run())


def test_entity_review_tool_is_registered_and_wrong_fact_tool_is_recoverable() -> None:
    assert workflow.review_entity_resolutions in registry.INGESTION_ROOT_TOOLS
    assert workflow.review_entity_resolutions in registry.INGESTION_TOOLS
    assert workflow.review_ingestion_conflicts in registry.INGESTION_ROOT_TOOLS
    assert workflow.review_ingestion_conflicts in registry.INGESTION_TOOLS
    assert workflow.review_entity_resolution not in registry.INGESTION_ROOT_TOOLS
    assert workflow.review_ingestion_conflict not in registry.INGESTION_ROOT_TOOLS
    from app.agent.skills.ingestion.tools import get_tools

    registered_names = {
        getattr(
            tool,
            "name",
            getattr(tool, "__name__", getattr(getattr(tool, "func", None), "__name__", None)),
        )
        for tool in get_tools()
    }
    assert "review_entity_resolutions" in registered_names
    assert "review_ingestion_conflicts" in registered_names
    assert "review_entity_resolution" not in registered_names
    assert "review_ingestion_conflict" not in registered_names

    async def run():
        repository = SimpleNamespace(
            review_fact_conflict=AsyncMock(side_effect=KeyError("entity-review-id")),
            entity_resolution_review_exists=AsyncMock(return_value=True),
        )
        container = SimpleNamespace(repository=repository)
        context = SimpleNamespace()
        with patch.object(
            workflow, "get_service_container", AsyncMock(return_value=container)
        ):
            result = await workflow.review_ingestion_conflict(
                "entity-review-id", "CONFIRM", context
            )
        assert result["terminal"] is False
        assert result["nextAction"] == "review_entity_resolutions"
        assert result["errors"][0]["code"] == "WRONG_REVIEW_TOOL"

    asyncio.run(run())


def test_bulk_fact_conflict_review_applies_all_with_one_confirmed_call() -> None:
    async def run():
        ingestion_id = "00000000-0000-0000-0000-000000000099"
        repository = SimpleNamespace(
            review_fact_conflicts=AsyncMock(return_value=[
                {"conflictId": "conflict-1"},
                {"conflictId": "conflict-2"},
            ]),
            get_preview=AsyncMock(return_value={
                "reportDigest": "digest-2",
                "pendingConflictCount": 0,
                "pendingEntityReviewCount": 0,
                "nextAction": "confirm_and_commit",
            }),
        )
        container = SimpleNamespace(repository=repository)
        context = SimpleNamespace()
        with patch.object(
            workflow, "get_service_container", AsyncMock(return_value=container)
        ):
            result = await workflow.review_ingestion_conflicts(
                ingestion_id, "REJECT", context
            )
        repository.review_fact_conflicts.assert_awaited_once_with(
            ingestion_id, "REJECT", None
        )
        assert result["updatedConflictIds"] == ["conflict-1", "conflict-2"]
        assert result["pendingConflictCount"] == 0
        assert result["nextAction"] == "confirm_and_commit"

    asyncio.run(run())


def test_repository_bulk_fact_review_updates_every_pending_conflict() -> None:
    async def run():
        repo = IngestionRepository()
        ontology = ActiveOntology(
            version_id="00000000-0000-0000-0000-000000000001", version="v1"
        )
        chunk = DocumentChunk(
            index=0, source="test.md", content="text", chunk_id="c",
            content_hash="a" * 64, structural_path="root", start_line=1, end_line=1,
        )
        workspace, _, _ = await repo.create_or_resume(
            artifact_name="test.md", content_hash="b" * 64, chunks=[chunk],
            ontology=ontology, document_key="docs/test.md", scope_hint=None,
            skill_digest="s", model_id="m", compiler_version="c", batch_size=1,
            max_batch_chars=100,
        )
        ingestion_id = str(workspace.job.id)
        await repo.record_fact_conflicts(ingestion_id, [
            {"newFactId": "new-1", "oldFactId": "old-1"},
            {"newFactId": "new-2", "oldFactId": "old-2"},
        ])
        reviewed = await repo.review_fact_conflicts(
            ingestion_id, "REJECT"
        )
        assert len(reviewed) == 2
        assert {item["decision"] for item in reviewed} == {"REJECT"}
        assert all(
            item["decision"] == "REJECT"
            for item in await repo.list_fact_conflicts(ingestion_id)
        )

    asyncio.run(run())


def test_bulk_entity_review_applies_all_with_one_confirmed_call() -> None:
    async def run():
        ingestion_id = "00000000-0000-0000-0000-000000000099"
        repository = SimpleNamespace(
            review_entity_resolutions=AsyncMock(return_value=[
                {"reviewId": "review-1"},
                {"reviewId": "review-2"},
            ])
        )
        lifecycle = SimpleNamespace(
            ingestion_schema_changes=AsyncMock(return_value=[]),
            active_version_id=AsyncMock(return_value="ontology-v1"),
        )
        container = SimpleNamespace(
            repository=repository,
            graph_store=object(),
            ontology_lifecycle=lifecycle,
        )
        context = SimpleNamespace()
        rebuilt = {
            "reportDigest": "digest-2",
            "pendingConflictCount": 0,
            "pendingEntityReviewCount": 0,
            "nextAction": "confirm_and_commit",
        }
        with (
            patch.object(
                workflow, "get_service_container", AsyncMock(return_value=container)
            ),
            patch.object(
                workflow.operations,
                "build_preview",
                AsyncMock(return_value=rebuilt),
            ),
        ):
            result = await workflow.review_entity_resolutions(
                ingestion_id, "REUSE", context
            )
        repository.review_entity_resolutions.assert_awaited_once_with(
            ingestion_id, "REUSE", None
        )
        assert result["updatedReviewIds"] == ["review-1", "review-2"]
        assert result["pendingEntityReviewCount"] == 0
        assert result["nextAction"] == "confirm_and_commit"

    asyncio.run(run())


def test_entity_review_tool_rebuilds_preview_and_returns_next_action() -> None:
    async def run():
        ingestion_id = "00000000-0000-0000-0000-000000000099"
        review = {
            "reviewId": "review-1",
            "ingestionId": ingestion_id,
            "decision": "REUSE",
        }
        repository = SimpleNamespace(
            review_entity_resolution=AsyncMock(return_value=review)
        )
        lifecycle = SimpleNamespace(
            ingestion_schema_changes=AsyncMock(return_value=[]),
            active_version_id=AsyncMock(return_value="ontology-v1"),
        )
        container = SimpleNamespace(
            repository=repository,
            graph_store=object(),
            ontology_lifecycle=lifecycle,
        )
        context = SimpleNamespace()
        rebuilt = {
            "reportDigest": "digest-2",
            "pendingConflictCount": 0,
            "pendingEntityReviewCount": 0,
            "nextAction": "confirm_and_commit",
        }
        with (
            patch.object(
                workflow, "get_service_container", AsyncMock(return_value=container)
            ),
            patch.object(
                workflow.operations,
                "build_preview",
                AsyncMock(return_value=rebuilt),
            ),
        ):
            result = await workflow.review_entity_resolution(
                "review-1", "REUSE", context
            )
        assert result["success"] is True
        assert result["reportDigest"] == "digest-2"
        assert result["nextAction"] == "confirm_and_commit"

    asyncio.run(run())


def test_legacy_fill_is_blocked_and_revision_retry_is_idempotent() -> None:
    async def run():
        assert (await fill(None, None, "job"))["success"] is False
        repo = IngestionRepository()
        ontology = ActiveOntology(version_id="00000000-0000-0000-0000-000000000001", version="v1")
        chunk = DocumentChunk(index=0, source="test.md", content="text", chunk_id="c",
                              content_hash="a" * 64, structural_path="root", start_line=1, end_line=1)
        common = dict(artifact_name="test.md", chunks=[chunk], ontology=ontology, scope_hint=None,
                      skill_digest="s", model_id="m", compiler_version="c", batch_size=1,
                      max_batch_chars=100, document_key="docs/test.md")
        first, _, _ = await repo.create_or_resume(content_hash="b" * 64, **common)
        second, _, _ = await repo.create_or_resume(content_hash="c" * 64, **common)
        assert first.version.id != second.version.id
        assert await repo.advance_knowledge_revision(str(first.job.id)) == 1
        assert await repo.advance_knowledge_revision(str(first.job.id)) == 1
        assert await repo.advance_knowledge_revision(str(second.job.id)) == 2

    asyncio.run(run())


def test_preview_is_read_only_and_commit_requires_matching_approval() -> None:
    class FakeGraph:
        def __init__(self):
            self.status = None
            self.writes = 0
            self.fail_publish_once = True

        async def preview_payload(self, workspace):
            return {"nodes": [], "edges": [], "chunks": [], "facts": []}

        async def current_assertions(self, keys):
            return []

        async def current_entity_keys(self, keys):
            return set()

        async def current_relation_keys(self, edges):
            return set()

        async def version_status(self, version_id):
            return self.status

        async def prepare_version(self, workspace, canonical_map=None, supported_fact_map=None, suppressed_fact_ids=None):
            self.writes += 1
            self.status = "PREPARED"
            return {"nodes": 0, "edges": 0, "chunks": 0, "facts": 0}

        async def publish_version(self, *args):
            if self.fail_publish_once:
                self.fail_publish_once = False
                raise RuntimeError("injected publish interruption")
            self.status = "COMMITTED"

        async def committed_summary(self, version_id):
            return {"nodes": 0, "edges": 0, "chunks": 0, "facts": 0,
                    "readbackVerified": True}

    async def run():
        repo = IngestionRepository()
        graph = FakeGraph()
        ontology = ActiveOntology(version_id="00000000-0000-0000-0000-000000000001", version="v1")
        chunk = DocumentChunk(index=0, source="test.md", content="text", chunk_id="c",
                              content_hash="a" * 64, structural_path="root", start_line=1, end_line=1)
        workspace, _, _ = await repo.create_or_resume(
            artifact_name="test.md", content_hash="b" * 64, chunks=[chunk], ontology=ontology,
            document_key="docs/test.md", scope_hint=None, skill_digest="s", model_id="m",
            compiler_version="c", batch_size=1, max_batch_chars=100,
        )
        ingestion_id = str(workspace.job.id)
        workspace.job.status = IngestionJobStatus.READY
        workspace.job.readiness_fingerprint = workspace_fingerprint(workspace)
        preview = await operations.build_preview(repo, graph, ingestion_id)
        digest = preview["reportDigest"]
        assert graph.writes == 0
        denied = await operations.commit(repo, graph, ingestion_id, digest)
        assert denied["errors"][0]["code"] == "APPROVAL_REQUIRED"
        assert graph.writes == 0
        await repo.record_commit_approval(
            ingestion_id, digest, user_id="human", session_id="session", function_call_id="call"
        )
        interrupted = await operations.commit(repo, graph, ingestion_id, digest)
        assert interrupted["errors"][0]["code"] == "COMMIT_RETRY_REQUIRED"
        assert graph.status == "PREPARED"
        assert (await repo.get_workspace(ingestion_id)).job.status == IngestionJobStatus.COMMITTING
        result = await operations.commit(repo, graph, ingestion_id, digest)
        assert result["readbackVerified"] is True
        assert graph.writes == 1
        assert (await repo.get_workspace(ingestion_id)).job.error_message is None
        assert await repo.knowledge_revision() == 1
        assert (await operations.commit(repo, graph, ingestion_id, digest))["idempotent"] is True
        assert await repo.knowledge_revision() == 1

    asyncio.run(run())
