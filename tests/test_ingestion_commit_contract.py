"""Commit gate and source assertion regressions."""

import asyncio

from app.schemas.ingestion.document import DocumentChunk
from app.schemas.ingestion.ontology import ActiveOntology
from app.services.ingestion.engine.assertions import compare_assertions, temporal_interval
from app.enums import IngestionJobStatus
from app.services.ingestion.engine import operations
from app.services.ingestion.engine.operations import fill
from app.services.ingestion.engine.repository import IngestionRepository
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

        async def prepare_version(self, workspace):
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
        assert await repo.knowledge_revision() == 1
        assert (await operations.commit(repo, graph, ingestion_id, digest))["idempotent"] is True
        assert await repo.knowledge_revision() == 1

    asyncio.run(run())
