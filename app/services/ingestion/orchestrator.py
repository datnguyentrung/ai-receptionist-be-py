"""Application orchestration for the interactive ingestion state machine."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    IngestionJobStatus,
    PreparedChunk,
    SourceVersionStatus,
)
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import (
    COMPILER_VERSION,
    OntologyCache,
    OntologyRegistry,
)
from app.services.ingestion.preprocessing import prepare_document
from app.services.ingestion.repository import (
    IngestionRepository,
    Workspace,
    workspace_chunks,
    workspace_fingerprint,
)

MAX_BATCH_VALIDATION_ATTEMPTS = 2


class IngestionOrchestrator:
    def __init__(
        self,
        repository: IngestionRepository,
        graph_store: Neo4jIngestionStore,
        ontology_cache: OntologyCache,
        *,
        max_file_size: int,
        chunk_size_chars: int,
        batch_size: int,
        skill_digest: str,
        model_id: str,
    ) -> None:
        self.repository = repository
        self.graph_store = graph_store
        self.ontology_cache = ontology_cache
        self.max_file_size = max_file_size
        self.chunk_size_chars = chunk_size_chars
        self.batch_size = max(1, batch_size)
        self.skill_digest = skill_digest
        self.model_id = model_id

    async def begin(
        self,
        artifact_name: str,
        data: bytes,
        *,
        document_key: str | None = None,
        scope_hint: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        projection = await self.ontology_cache.get(scope_hint or "core")
        prepared = prepare_document(
            artifact_name,
            data,
            max_file_size=self.max_file_size,
            chunk_size_chars=self.chunk_size_chars,
            mime_type=mime_type,
        )
        stable_key = document_key or _document_key(artifact_name)
        workspace, resumed, committed = await self.repository.create_or_resume(
            prepared,
            projection,
            document_key=stable_key,
            scope_hint=scope_hint,
            skill_digest=self.skill_digest,
            model_id=self.model_id,
            compiler_version=COMPILER_VERSION,
            batch_size=self.batch_size,
        )
        if committed:
            return self._status(workspace, resumed=True, idempotent=True)
        return self._status(workspace, resumed=resumed)

    async def get_batch(self, ingestion_id: str, batch_index: int) -> dict[str, Any]:
        workspace = await self._required_workspace(ingestion_id)
        if workspace.job.status in {IngestionJobStatus.COMMITTED, IngestionJobStatus.FAILED}:
            return self._status(workspace)
        batch = next((item for item in workspace.batches if item.batch_index == batch_index), None)
        if batch is None:
            return _error(
                "batch_validation",
                ingestion_id,
                "INVALID_BATCH_INDEX",
                f"Unknown batch index: {batch_index}",
            )
        chunks = workspace_chunks(workspace, batch.chunk_indexes)
        return {
            "success": True,
            "stage": "batch_retrieved",
            "terminal": False,
            "nextAction": "submit_batch",
            "ingestionId": ingestion_id,
            "batch": {
                "batchIndex": batch.batch_index,
                "chunks": [item.model_dump(by_alias=True, mode="json") for item in chunks],
                "canonicalGraphContext": _canonical_context(workspace, batch_index),
            },
        }

    async def submit_batch(
        self,
        ingestion_id: str,
        batch_index: int,
        graph_fragment: dict[str, Any],
    ) -> dict[str, Any]:
        workspace = await self._required_workspace(ingestion_id)
        batch = next((item for item in workspace.batches if item.batch_index == batch_index), None)
        if batch is None:
            return _error("batch_validation", ingestion_id, "INVALID_BATCH_INDEX", str(batch_index))
        if batch.validation_attempts >= MAX_BATCH_VALIDATION_ATTEMPTS and batch.status == "REPAIR_REQUIRED":
            failed = await self.repository.mark_failed(
                ingestion_id,
                "batch_validation",
                f"Batch {batch_index} exceeded the validation retry limit",
            )
            return self._status(failed)
        try:
            fragment = GraphPatchFragment.model_validate(graph_fragment)
        except ValidationError as exc:
            issues = [
                {
                    "code": "INVALID_GRAPH_PATCH",
                    "message": item["msg"],
                    "location": ".".join(str(part) for part in item["loc"]),
                    "retryable": True,
                }
                for item in exc.errors()
            ]
            workspace = await self.repository.store_batch_result(
                ingestion_id, batch_index, None, issues
            )
            return self._batch_failure(workspace, batch_index, issues)

        projection = await self.ontology_cache.get(workspace.job.scope_hint or "core")
        issues = [
            item.model_dump(by_alias=True, mode="json")
            for item in OntologyRegistry(projection).validate_fragment(
                fragment, workspace_chunks(workspace, batch.chunk_indexes)
            )
        ]
        if issues:
            workspace = await self.repository.store_batch_result(
                ingestion_id, batch_index, None, issues
            )
            return self._batch_failure(workspace, batch_index, issues)

        payload = fragment.model_dump(by_alias=True, mode="json")
        await self.graph_store.stage_batch(ingestion_id, batch_index, payload)
        workspace = await self.repository.store_batch_result(
            ingestion_id, batch_index, fragment, []
        )
        result = self._status(workspace)
        result["submittedBatchIndex"] = batch_index
        return result

    async def finalize(self, ingestion_id: str) -> dict[str, Any]:
        workspace = await self._required_workspace(ingestion_id)
        incomplete = [
            item.batch_index for item in workspace.batches if item.status != "STAGED"
        ]
        if incomplete:
            return {
                "success": False,
                "stage": "repair_required",
                "terminal": False,
                "nextAction": "repair_batches",
                "ingestionId": ingestion_id,
                "repairBatchIndexes": incomplete,
                "errors": [
                    {
                        "code": "INCOMPLETE_BATCHES",
                        "message": f"Batches require repair: {incomplete}",
                        "location": "batches",
                        "retryable": True,
                    }
                ],
            }
        fingerprint = workspace_fingerprint(workspace)
        workspace = await self.repository.mark_ready(ingestion_id, fingerprint)
        return self._status(workspace)

    async def fill(self, ingestion_id: str) -> dict[str, Any]:
        workspace = await self._required_workspace(ingestion_id)
        if workspace.job.status == IngestionJobStatus.COMMITTED:
            return self._status(workspace, idempotent=True)
        expected = workspace_fingerprint(workspace)
        if workspace.job.status != IngestionJobStatus.READY or workspace.job.readiness_fingerprint != expected:
            return _error(
                "validation_precondition",
                ingestion_id,
                "VALIDATION_PRECONDITION",
                "finalize_ingestion must succeed before fill_ingestion",
            )
        await self.repository.mark_writing(ingestion_id)
        try:
            result = await self.graph_store.fill(workspace)
            previous_version_id = workspace.document.current_version_id
            if previous_version_id and previous_version_id != workspace.version.id:
                result["superseded"] = await self.graph_store.deactivate_version(
                    str(previous_version_id),
                    "SUPERSEDED",
                )
            committed = await self.repository.mark_committed(ingestion_id, result)
            await self.graph_store.purge_staging(ingestion_id)
            return {**self._status(committed), **result}
        except Exception as exc:  # noqa: BLE001 - persistence failures require reconciliation
            reconciled = await self.graph_store.committed_summary(str(workspace.version.id))
            if reconciled is not None:
                committed = await self.repository.mark_committed(ingestion_id, reconciled)
                await self.graph_store.purge_staging(ingestion_id)
                return {**self._status(committed), **reconciled}
            await self.repository.mark_failed(ingestion_id, "persistence_failure", str(exc))
            return _error(
                "persistence_failure",
                ingestion_id,
                "PERSISTENCE_FAILED",
                str(exc),
            )

    async def status(self, ingestion_id: str) -> dict[str, Any]:
        return self._status(await self._required_workspace(ingestion_id))

    async def load_scope(self, scope_key: str) -> dict[str, Any]:
        projection = await self.ontology_cache.get(scope_key)
        return {
            "success": True,
            "stage": "schema_loaded",
            "terminal": False,
            "ingestionId": None,
            "nextAction": "extract_batch",
            "scope": projection.model_dump(by_alias=True, mode="json"),
        }

    async def validate_patch(self, graph_patch: dict[str, Any]) -> dict[str, Any]:
        try:
            fragment = GraphPatchFragment.model_validate(graph_patch)
        except ValidationError as exc:
            return {
                "success": False,
                "stage": "graph_validation",
                "terminal": True,
                "ingestionId": None,
                "nextAction": None,
                "errors": exc.errors(include_url=False),
            }
        projection = await self.ontology_cache.get("core")
        chunks = _chunks_from_evidence(fragment)
        issues = OntologyRegistry(projection).validate_fragment(fragment, chunks)
        return {
            "success": not issues,
            "stage": "graph_validation",
            "terminal": True,
            "ingestionId": None,
            "nextAction": None,
            "valid": not issues,
            "errors": [item.model_dump(by_alias=True, mode="json") for item in issues],
        }

    async def delete_document(self, document_id: str, if_missing: str) -> dict[str, Any]:
        try:
            document, version = await self.repository.set_document_version_status(
                document_id, None, SourceVersionStatus.DELETED
            )
        except KeyError:
            if if_missing == "ignore":
                return {"success": True, "stage": "deleted", "terminal": True, "ingestionId": None, "nextAction": None, "documentId": document_id, "missing": True}
            return _error("delete", None, "DOCUMENT_NOT_FOUND", document_id)
        result = await self.graph_store.deactivate_version(str(version.id), "DELETED")
        return {"success": True, "stage": "deleted", "terminal": True, "ingestionId": None, "nextAction": None, "documentId": str(document.id), **result}

    async def rollback_version(self, document_id: str, version_id: str) -> dict[str, Any]:
        try:
            document, version = await self.repository.set_document_version_status(
                document_id, version_id, SourceVersionStatus.ROLLED_BACK
            )
        except KeyError:
            return _error("rollback", None, "VERSION_NOT_FOUND", version_id)
        result = await self.graph_store.deactivate_version(str(version.id), "ROLLED_BACK")
        return {"success": True, "stage": "rolled_back", "terminal": True, "ingestionId": None, "nextAction": None, "documentId": str(document.id), **result}

    async def _required_workspace(self, ingestion_id: str) -> Workspace:
        workspace = await self.repository.get_workspace(ingestion_id)
        if workspace is None:
            raise KeyError(f"Unknown ingestionId: {ingestion_id}")
        return workspace

    def _batch_failure(self, workspace: Workspace, batch_index: int, issues: list[dict]) -> dict[str, Any]:
        return {
            "success": False,
            "stage": "batch_validation",
            "terminal": False,
            "nextAction": "repair_batch",
            "ingestionId": str(workspace.job.id),
            "batchIndex": batch_index,
            "errors": issues,
            "validationAttempts": next(
                item.validation_attempts for item in workspace.batches if item.batch_index == batch_index
            ),
            "maxValidationAttempts": MAX_BATCH_VALIDATION_ATTEMPTS,
        }

    def _status(self, workspace: Workspace, *, resumed: bool = False, idempotent: bool = False) -> dict[str, Any]:
        pending = next((item for item in workspace.batches if item.status != "STAGED"), None)
        if workspace.job.status == IngestionJobStatus.COMMITTED:
            stage, terminal, next_action = "committed", True, None
        elif workspace.job.status == IngestionJobStatus.FAILED:
            stage, terminal, next_action = "failed", True, None
        elif workspace.job.status == IngestionJobStatus.READY:
            stage, terminal, next_action = "ready_to_fill", False, "fill"
        elif pending is None:
            stage, terminal, next_action = "ready_to_finalize", False, "finalize"
        elif pending.status == "REPAIR_REQUIRED":
            stage, terminal, next_action = "repair_required", False, "repair_batch"
        else:
            stage, terminal, next_action = "batching", False, "process_batch"
        result: dict[str, Any] = {
            "success": workspace.job.status != IngestionJobStatus.FAILED,
            "stage": stage,
            "terminal": terminal,
            "nextAction": next_action,
            "ingestionId": str(workspace.job.id),
            "documentId": str(workspace.document.id),
            "documentVersionId": str(workspace.version.id),
            "ontologyVersionId": str(workspace.version.ontology_version_id),
            "workspaceStats": {
                "chunks": len(workspace.chunks),
                "batches": len(workspace.batches),
                "stagedBatches": sum(item.status == "STAGED" for item in workspace.batches),
            },
            "resumed": resumed,
            "idempotent": idempotent,
        }
        if pending:
            result["nextBatch"] = {
                "batchIndex": pending.batch_index,
                "chunkIndexes": pending.chunk_indexes,
            }
        if workspace.job.error_message:
            result["errors"] = [
                {
                    "code": "INGESTION_FAILED",
                    "message": workspace.job.error_message,
                    "location": workspace.job.error_stage,
                }
            ]
        return result


def _document_key(filename: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.casefold()).strip("-")
    return stem or hashlib.sha256(filename.encode("utf-8")).hexdigest()[:24]


def _canonical_context(workspace: Workspace, before_batch: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for batch in workspace.batches:
        if batch.batch_index >= before_batch or not batch.graph_fragment:
            continue
        result.extend(
            {
                "tempId": node["tempId"],
                "className": node["className"],
                "identity": node.get("identity") or {},
            }
            for node in batch.graph_fragment.get("nodes", [])
        )
    return result[:50]


def _chunks_from_evidence(fragment: GraphPatchFragment) -> list[PreparedChunk]:
    texts: dict[int, list[str]] = {}
    evidence_items = [e for node in fragment.nodes for e in node.evidence]
    evidence_items.extend(e for node in fragment.nodes for prop in node.properties for e in prop.evidence)
    evidence_items.extend(e for edge in fragment.edges for e in edge.evidence)
    for evidence in evidence_items:
        texts.setdefault(evidence.chunk_index, []).append(evidence.text)
    return [
        PreparedChunk(
            chunk_id=f"direct-{index}",
            chunk_index=index,
            text="\n".join(texts.get(index, [])),
            content_hash=hashlib.sha256("\n".join(texts.get(index, [])).encode()).hexdigest(),
            token_count=max(1, len("\n".join(texts.get(index, []))) // 4),
            source_anchor=f"direct-patch#chunk-{index}",
        )
        for index in sorted({item.chunk_index for item in fragment.coverage} | set(texts))
    ]


def _error(stage: str, ingestion_id: str | None, code: str, message: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": ingestion_id,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }
    return result


__all__ = ["MAX_BATCH_VALIDATION_ATTEMPTS", "IngestionOrchestrator"]
