"""Deterministic orchestration for one structured ingestion batch."""

import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from google.genai.types import HttpRetryOptions
from pydantic import Field

from app.agent.tools.ingestion_tools import (
    batch_result_payload,
    log_ingestion_event,
    repair_context,
    store_checkpoint,
    tool_exception,
    update_batch_accumulator,
)
from app.core.config import settings
from app.core.ingestion_runtime import get_service_container
from app.schemas.ingestion.base import IngestionModel
from app.schemas.ingestion.semantic_patch import (
    SemanticGraphPatchFragment,
    SemanticGraphRepairDelta,
)
from app.services.ingestion.engine import operations
from app.utils.ingestion_helpers import stable_entity_key


class EvidenceVerdict(str, Enum):
    EQUIVALENT = "EQUIVALENT"
    REPAIRABLE = "REPAIRABLE"
    UNSUPPORTED = "UNSUPPORTED"
    UNCERTAIN = "UNCERTAIN"


class SemanticEvidenceDecision(IngestionModel):
    issue_id: str = Field(description="ID issue được cấp trong input")
    verdict: EvidenceVerdict
    source_quote: str | None = Field(
        default=None,
        description="Đoạn trích NGUYÊN VĂN từ chunk nguồn; không paraphrase",
    )
    explanation: str = Field(
        description="Giải thích ngắn gọn kết quả, tập trung claim và source"
    )


class SemanticEvidenceValidationResult(IngestionModel):
    decisions: list[SemanticEvidenceDecision] = Field(default_factory=list)


class ScopeSelectionOutput(IngestionModel):
    """The only decision needed before a schema-specific extraction."""

    scope_keys: list[str] = Field(min_length=1)


class BatchExtractionOutput(IngestionModel):
    """Structured result from the single-turn extraction model."""

    scope_keys: list[str] = Field(default_factory=list)
    extraction: SemanticGraphPatchFragment


class BatchModel(Protocol):
    """External seam for the bounded model phases."""

    async def select_scopes(
        self, payload: dict[str, Any], tool_context: Any
    ) -> ScopeSelectionOutput: ...

    async def extract(
        self, payload: dict[str, Any], tool_context: Any
    ) -> BatchExtractionOutput: ...

    async def repair(
        self, payload: dict[str, Any], tool_context: Any
    ) -> SemanticGraphRepairDelta: ...

    async def validate_evidence(
        self, payload: dict[str, Any], tool_context: Any
    ) -> SemanticEvidenceValidationResult: ...


_SELECTOR_INSTRUCTION = """
Chọn tất cả các scope ontology cần thiết để trích xuất batch nạp tài liệu được cung cấp.
Chỉ sử dụng các scopeKeys có trong scopeCatalog. Trả về JSON khớp với output schema.
Không tự trích xuất dữ liệu đồ thị và không gọi bất kỳ công cụ nào.
""".strip()

_EXTRACTION_INSTRUCTION = """
Trích xuất chính xác một batch nạp tài liệu vào structured output schema.

NGUYÊN TẮC:
1. Chỉ sử dụng node, edge và property được định nghĩa trong ontology schema.
2. Trích xuất đầy đủ các atomic claims; không bỏ sót thông tin quan trọng.
3. Mọi dữ kiện phải có căn cứ từ đúng chunk nguồn.
4. Không suy diễn, bổ sung hoặc làm thay đổi ý nghĩa thông tin nguồn.

QUY TẮC EVIDENCE:
1. evidence.text phải được sao chép trực tiếp từ chunk gốc.
2. Ưu tiên trích dẫn đoạn ngắn nhất nhưng đủ chứng minh dữ kiện.
3. Không tự nối dòng, diễn đạt lại, thêm dấu câu hoặc chuẩn hóa văn bản.
4. Nếu bằng chứng trải dài nhiều dòng, giữ nguyên ký tự xuống dòng.
5. Khi có thể, sử dụng nhiều trích dẫn ngắn thay vì ghép các đoạn
   thành một câu mới.
6. Trước khi trả kết quả, tự kiểm tra mỗi evidence.text có xuất hiện
   nguyên văn trong chunk nguồn hay không.

Trả về đầy đủ scopeKeys áp dụng và SemanticGraphPatchFragment.
Không gọi công cụ hoặc mô tả công việc.
""".strip()

_REPAIR_INSTRUCTION = """
Sửa chính xác một batch dựa trên protectedBaseline và validationIssues.

NGUYÊN TẮC:
1. Chỉ trả về SemanticGraphRepairDelta với baselineFingerprint đã cung cấp.
2. Giữ nguyên toàn bộ dữ kiện baseline hợp lệ.
3. Chỉ sửa những dữ kiện liên quan trực tiếp đến validationIssues.
4. Không thêm, xóa hoặc thay đổi thông tin không liên quan đến lỗi.
5. Mọi dữ kiện sau sửa phải được hỗ trợ bởi chunk nguồn.

NẾU GẶP EVIDENCE_NOT_GROUNDED:
- Ưu tiên tìm và sao chép lại đoạn trích nguyên văn từ chunk.
- Giữ nguyên dấu câu, khoảng trắng và ký tự xuống dòng.
- Không tự viết lại nội dung evidence theo cách diễn đạt của mình.
- Nếu bằng chứng quá dài, chọn đoạn ngắn hơn nhưng vẫn đủ chứng minh.
- Không thay đổi dữ kiện đúng chỉ vì evidence bị sai định dạng.

Không gọi công cụ nào.
""".strip()

_SEMANTIC_VALIDATION_INSTRUCTION = """
Bạn là Semantic Evidence Validator trong hệ thống Knowledge Graph Ingestion.
NHIỆM VỤ:
Đánh giá các evidence không khớp nguyên văn với tài liệu nguồn.
Đánh giá đồng thời:
1. evidence do LLM tạo ra có giữ nguyên ngữ nghĩa của đoạn nguồn tương ứng không?
2. Đoạn nguồn có thực sự hỗ trợ node, edge hoặc property được trích xuất không?
QUY TẮC:
- Cho phép thay đổi xuống dòng, khoảng trắng, dấu câu và cách trình bày nếu không đổi ý nghĩa.
- Không chấp nhận thay đổi số liệu, tên riêng, địa điểm, ngày tháng, phủ định, điều kiện, quan hệ giữa các thực thể hoặc mức độ khẳng định.
- Không tự suy diễn những thông tin nguồn không nêu.
- Không tự trích xuất thêm dữ liệu đồ thị.
- Không gọi công cụ.
KẾT QUẢ:
- EQUIVALENT: Evidence khác hình thức nhưng giữ nguyên thông tin và đoạn nguồn hỗ trợ đúng claim.
- REPAIRABLE: Có sai lệch ngữ nghĩa nhưng nguồn có đủ thông tin để sửa dữ kiện.
- UNSUPPORTED: Claim không được tài liệu hỗ trợ.
- UNCERTAIN: Chưa thể xác định chắc chắn.
Nếu không đủ căn cứ, không được trả EQUIVALENT.
Trả về kết quả theo structured output schema.
""".strip()


def _single_turn_agent(
    name: str, instruction: str, output_schema: type[IngestionModel]
) -> Agent:
    return Agent(
        name=name,
        model=Gemini(
            model=os.getenv("GOOGLE_ADK_MODEL", "gemini-3.5-flash-lite"),
            retry_options=HttpRetryOptions(
                attempts=5,
                initial_delay=3.0,
                max_delay=30.0,
                http_status_codes=[429, 503],
            ),
        ),
        description="Internal structured ingestion phase.",
        instruction=instruction,
        output_schema=output_schema,
        mode="chat",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.HIGH
            )
        ),
    )


class AdkBatchModel:
    """Production adapter: isolated one-turn ADK agents with no function tools."""

    def __init__(self) -> None:
        self._selector = AgentTool(
            _single_turn_agent(
                "ingestion_scope_selector", _SELECTOR_INSTRUCTION, ScopeSelectionOutput
            ),
            include_plugins=True,
        )
        self._extractor = AgentTool(
            _single_turn_agent(
                "ingestion_batch_extractor",
                _EXTRACTION_INSTRUCTION,
                BatchExtractionOutput,
            ),
            include_plugins=True,
        )
        self._repairer = AgentTool(
            _single_turn_agent(
                "ingestion_batch_repairer",
                _REPAIR_INSTRUCTION,
                SemanticGraphRepairDelta,
            ),
            include_plugins=True,
        )
        self._semantic_validator = AgentTool(
            _single_turn_agent(
                "ingestion_evidence_validator",
                _SEMANTIC_VALIDATION_INSTRUCTION,
                SemanticEvidenceValidationResult,
            ),
            include_plugins=True,
        )

    @staticmethod
    async def _run(
        tool: AgentTool,
        payload: dict[str, Any],
        tool_context: Any,
        output: type[IngestionModel],
    ) -> Any:
        result = await tool.run_async(
            args={
                "request": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                )
            },
            tool_context=tool_context,
        )
        return result if isinstance(result, output) else output.model_validate(result)

    async def select_scopes(
        self, payload: dict[str, Any], tool_context: Any
    ) -> ScopeSelectionOutput:
        return await self._run(
            self._selector, payload, tool_context, ScopeSelectionOutput
        )

    async def extract(
        self, payload: dict[str, Any], tool_context: Any
    ) -> BatchExtractionOutput:
        return await self._run(
            self._extractor, payload, tool_context, BatchExtractionOutput
        )

    async def repair(
        self, payload: dict[str, Any], tool_context: Any
    ) -> SemanticGraphRepairDelta:
        return await self._run(
            self._repairer, payload, tool_context, SemanticGraphRepairDelta
        )

    async def validate_evidence(
        self, payload: dict[str, Any], tool_context: Any
    ) -> SemanticEvidenceValidationResult:
        return await self._run(
            self._semantic_validator,
            payload,
            tool_context,
            SemanticEvidenceValidationResult,
        )


@dataclass
class _PreparedBatch:
    container: Any
    workspace: Any
    batch: Any
    payload: dict[str, Any]
    catalog: list[dict[str, Any]]
    catalog_keys: list[str]
    version_id: str


class BatchExecutionModule:
    """Deep module that owns a batch lifecycle behind one execute interface."""

    def __init__(
        self, model: BatchModel | None = None, *, prompt_max_bytes: int | None = None
    ) -> None:
        self._model = model or AdkBatchModel()
        configured = os.getenv("INGESTION_BATCH_PROMPT_MAX_BYTES", "262144")
        self._prompt_max_bytes = (
            prompt_max_bytes
            if prompt_max_bytes is not None
            else max(1, int(configured))
        )

    async def execute(
        self, ingestion_id: str, batch_index: int, tool_context: Any
    ) -> dict[str, Any]:
        calls = 0
        strategy = ""
        prompt_bytes = 0
        requested_scope_keys: list[str] = []
        try:
            prepared = await self._prepare(ingestion_id, batch_index, tool_context)
            if isinstance(prepared, dict):
                self._log_metrics(
                    ingestion_id,
                    batch_index,
                    "guarded",
                    calls,
                    prompt_bytes,
                    [],
                    [],
                    prepared,
                )
                return prepared

            if prepared.batch.status == "STAGED":
                result = {
                    **batch_result_payload(prepared.workspace, batch_index),
                    "idempotent": True,
                }
                store_checkpoint(tool_context, result)
                scope_keys_val = result.get("scopeKeys")
                resolved_keys = (
                    [str(k) for k in scope_keys_val]
                    if isinstance(scope_keys_val, list)
                    else []
                )
                self._log_metrics(
                    ingestion_id,
                    batch_index,
                    "checkpoint",
                    calls,
                    prompt_bytes,
                    [],
                    resolved_keys,
                    result,
                )
                return result

            if prepared.batch.status == "REPAIR_REQUIRED":
                scope_keys = self._normalize_scope_keys(
                    prepared.batch.scope_keys, prepared.catalog_keys
                )
                rep_ctx = repair_context(prepared.batch)
                if rep_ctx is not None and bool(prepared.batch.validation_attempts < operations.MAX_BATCH_VALIDATION_ATTEMPTS):
                    strategy = "direct_repair"
                    repair_payload = {
                        "phase": "repair",
                        "ingestionId": ingestion_id,
                        "batchIndex": batch_index,
                        "scopeKeys": scope_keys,
                        "scope": (
                            await operations.load_scope(
                                prepared.container.ontology_cache,
                                scope_keys,
                                prepared.version_id,
                            )
                        )["scope"],
                        "batch": prepared.payload["batch"],
                        **rep_ctx,
                    }
                    calls += 1
                    delta = await self._model.repair(repair_payload, tool_context)
                    workspace = await operations.repair_batch(
                        prepared.container.repository,
                        prepared.container.ontology_cache,
                        ingestion_id,
                        batch_index,
                        scope_keys,
                        delta,
                    )
                    result = self._result_and_state(
                        workspace, ingestion_id, batch_index, tool_context
                    )
                    self._log_metrics(
                        ingestion_id,
                        batch_index,
                        strategy,
                        calls,
                        prompt_bytes,
                        scope_keys,
                        result.get("scopeKeys", scope_keys),
                        result,
                    )
                    return result

            selected = self._normalize_scope_keys(
                prepared.batch.scope_keys, prepared.catalog_keys
            )
            hint_selected = self._scope_hint_keys(
                prepared.workspace.job.scope_hint, prepared.catalog_keys
            )
            if selected:
                strategy = "checkpoint"
            elif hint_selected:
                strategy = "hint"
                selected = hint_selected

            if selected:
                selected_scope = await operations.load_scope(
                    prepared.container.ontology_cache, selected, prepared.version_id
                )
                extraction_payload = self._extraction_payload(
                    prepared, selected_scope["scope"], selected
                )
                prompt_bytes = self._payload_size(extraction_payload)
            else:
                all_scope = await operations.load_scope(
                    prepared.container.ontology_cache,
                    prepared.catalog_keys,
                    prepared.version_id,
                )
                inline_payload = self._extraction_payload(
                    prepared, all_scope["scope"], selected
                )
                prompt_bytes = self._payload_size(inline_payload)
                if prompt_bytes > self._prompt_max_bytes:
                    strategy = "selector_then_extract"
                    calls += 1
                    choice = await self._model.select_scopes(
                        self._selector_payload(prepared), tool_context
                    )
                    selected = self._normalize_scope_keys(
                        choice.scope_keys, prepared.catalog_keys
                    )
                    if not selected:
                        result = self._failure(
                            "scope_selection",
                            ingestion_id,
                            "MODEL_SELECTED_NO_VALID_SCOPE",
                        )
                        store_checkpoint(tool_context, result)
                        self._log_metrics(
                            ingestion_id,
                            batch_index,
                            strategy,
                            calls,
                            prompt_bytes,
                            [],
                            [],
                            result,
                        )
                        return result
                    selected_scope = await operations.load_scope(
                        prepared.container.ontology_cache,
                        selected,
                        prepared.version_id,
                    )
                    extraction_payload = self._extraction_payload(
                        prepared, selected_scope["scope"], selected
                    )
                else:
                    strategy = "inline_all"
                    extraction_payload = inline_payload

            try:
                calls += 1
                extraction_output = await self._model.extract(
                    extraction_payload, tool_context
                )
            except Exception:  # noqa: BLE001 - one bounded structured-output retry.
                # A malformed structured response gets one bounded retry, never a ReAct loop.
                calls += 1
                extraction_output = await self._model.extract(
                    extraction_payload, tool_context
                )

            requested_scope_keys = self._normalize_scope_keys(
                [*selected, *extraction_output.scope_keys], prepared.catalog_keys
            )
            scope_keys = await self._reconcile_scope_keys(
                prepared, requested_scope_keys, extraction_output.extraction
            )
            if not scope_keys:
                result = self._failure(
                    "scope_selection", ingestion_id, "NO_SCOPE_SELECTED"
                )
                store_checkpoint(tool_context, result)
                self._log_metrics(
                    ingestion_id,
                    batch_index,
                    strategy,
                    calls,
                    prompt_bytes,
                    requested_scope_keys,
                    [],
                    result,
                )
                return result

            result, workspace = await self._submit(
                prepared, scope_keys, extraction_output.extraction, tool_context
            )
            if result.get("stage") == "scope_reselection_required":
                expanded = self._scope_candidates(result, prepared.catalog_keys)
                expanded_scope_keys = self._normalize_scope_keys(
                    [*scope_keys, *expanded], prepared.catalog_keys
                )
                if set(expanded_scope_keys) > set(scope_keys):
                    scope_keys = expanded_scope_keys
                    result, workspace = await self._submit(
                        prepared, scope_keys, extraction_output.extraction, tool_context
                    )

            # Semantic Evidence Validation flow
            working_extraction = extraction_output.extraction
            if self._should_run_semantic_evidence(result, ingestion_id):
                (
                    result,
                    workspace,
                    working_extraction,
                    semantic_calls,
                ) = await self._handle_semantic_evidence_validation(
                    prepared=prepared,
                    scope_keys=scope_keys,
                    extraction=working_extraction,
                    result=result,
                    tool_context=tool_context,
                )
                calls += semantic_calls

            if self._should_repair(result):
                rep_context = result["repairContext"]
                repair_payload = {
                    "phase": "repair",
                    "ingestionId": ingestion_id,
                    "batchIndex": batch_index,
                    "scopeKeys": scope_keys,
                    "scope": (
                        await operations.load_scope(
                            prepared.container.ontology_cache,
                            scope_keys,
                            prepared.version_id,
                        )
                    )["scope"],
                    "batch": prepared.payload["batch"],
                    **rep_context,
                }
                calls += 1
                delta = await self._model.repair(repair_payload, tool_context)
                workspace = await operations.repair_batch(
                    prepared.container.repository,
                    prepared.container.ontology_cache,
                    ingestion_id,
                    batch_index,
                    scope_keys,
                    delta,
                )
                result = self._result_and_state(
                    workspace, ingestion_id, batch_index, tool_context
                )

            self._log_metrics(
                ingestion_id,
                batch_index,
                strategy,
                calls,
                prompt_bytes,
                requested_scope_keys,
                result.get("scopeKeys", scope_keys),
                result,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors.
            result = tool_exception("structured_batch_execution", ingestion_id, exc)
            store_checkpoint(tool_context, result)
            self._log_metrics(
                ingestion_id,
                batch_index,
                strategy or "failed",
                calls,
                prompt_bytes,
                requested_scope_keys,
                [],
                result,
            )
            return result

    async def _prepare(
        self, ingestion_id: str, batch_index: int, tool_context: Any
    ) -> _PreparedBatch | dict[str, Any]:
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        guarded = operations.workflow_guard_payload(workspace, "load_scopes")
        if guarded is not None:
            store_checkpoint(tool_context, guarded)
            return guarded
        batch, chunks, canonical_nodes = await operations.get_batch(
            container.repository, ingestion_id, batch_index
        )
        accumulator = tool_context.state.get("ingestion_batch_accumulator")
        canonical = [
            {
                "ref": f"entity:{stable_entity_key(node.class_name, node.identity)}",
                **node.model_dump(by_alias=True, mode="json"),
            }
            for node in canonical_nodes[:50]
        ]
        if (
            isinstance(accumulator, dict)
            and accumulator.get("ingestionId") == ingestion_id
            and isinstance(accumulator.get("entities"), dict)
        ):
            canonical = [
                {"ref": ref, **node}
                for ref, node in list(accumulator["entities"].items())[:50]
            ]
        version_id = str(workspace.job.ontology_version_id)
        catalog_result = await operations.list_scopes(
            container.ontology_cache, version_id
        )
        catalog = catalog_result["scopes"]
        catalog_keys = [item["scopeKey"] for item in catalog]
        payload = {
            "batch": {
                "batchIndex": batch.batch_index,
                "scopeHint": workspace.job.scope_hint,
                "selectedScopeKeys": batch.scope_keys,
                "chunks": [
                    item.model_dump(by_alias=True, mode="json") for item in chunks
                ],
                "canonicalGraphContext": canonical,
            },
            "scopeCatalog": catalog,
        }
        return _PreparedBatch(
            container, workspace, batch, payload, catalog, catalog_keys, version_id
        )

    @staticmethod
    def _payload_size(payload: dict[str, Any]) -> int:
        return len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )

    @staticmethod
    def _normalize_scope_keys(
        keys: list[str] | tuple[str, ...], catalog_keys: list[str]
    ) -> list[str]:
        catalog = {key.casefold(): key for key in catalog_keys}
        found = {
            catalog[key.strip().casefold()]
            for key in keys
            if key.strip().casefold() in catalog
        }
        return [key for key in catalog_keys if key in found]

    def _scope_hint_keys(self, hint: Any, catalog_keys: list[str]) -> list[str]:
        return self._normalize_scope_keys(
            [hint] if isinstance(hint, str) else [], catalog_keys
        )

    @staticmethod
    def _scope_candidates(result: dict[str, Any], catalog_keys: list[str]) -> list[str]:
        candidates = [
            candidate
            for issue in result.get("errors", [])
            if isinstance(issue, dict)
            for candidate in issue.get("candidateScopes", [])
            if isinstance(candidate, str)
        ]
        return [key for key in catalog_keys if key in candidates]

    @staticmethod
    def _selector_payload(prepared: _PreparedBatch) -> dict[str, Any]:
        return {
            "phase": "select_scopes",
            "ingestionId": str(prepared.workspace.job.id),
            **prepared.payload,
        }

    @staticmethod
    def _extraction_payload(
        prepared: _PreparedBatch, scope: dict[str, Any], selected: list[str]
    ) -> dict[str, Any]:
        return {
            "phase": "extract",
            "ingestionId": str(prepared.workspace.job.id),
            "requiredScopeKeys": selected,
            "scope": scope,
            **prepared.payload,
        }

    async def _reconcile_scope_keys(
        self,
        prepared: _PreparedBatch,
        selected: list[str],
        extraction: SemanticGraphPatchFragment,
    ) -> list[str]:
        # The model may choose a narrow set, but Python expands it deterministically
        # for every ontology technical name actually used in its structured output.
        by_entity: dict[str, set[str]] = {}
        by_property: dict[tuple[str, str], set[str]] = {}
        by_relationship: dict[str, set[str]] = {}
        relationship_endpoints: dict[str, list[tuple[str, str]]] = {}
        for key in prepared.catalog_keys:
            projection = await prepared.container.ontology_cache.get(
                key, prepared.version_id
            )
            for item in projection.entity_types:
                by_entity.setdefault(str(item["technicalName"]).casefold(), set()).add(
                    key
                )
            for item in projection.properties:
                by_property.setdefault(
                    (
                        str(item["entityType"]).casefold(),
                        str(item["technicalName"]).casefold(),
                    ),
                    set(),
                ).add(key)
            for item in projection.relationships:
                relationship_name = str(item["technicalName"]).casefold()
                by_relationship.setdefault(relationship_name, set()).add(key)
                relationship_endpoints.setdefault(relationship_name, []).append(
                    (str(item["sourceEntityType"]), str(item["targetEntityType"]))
                )
        required = set(selected)
        classes = {node.temp_id: node.class_name for node in extraction.nodes}
        for node in extraction.nodes:
            required.update(by_entity.get(node.class_name.casefold(), set()))
            for fact in node.properties:
                required.update(
                    by_property.get(
                        (node.class_name.casefold(), fact.property_name.casefold()),
                        set(),
                    )
                )
        for edge in extraction.edges:
            relationship_name = edge.edge_name.casefold()
            required.update(by_relationship.get(relationship_name, set()))
            for source_type, target_type in relationship_endpoints.get(
                relationship_name, []
            ):
                required.update(by_entity.get(source_type.casefold(), set()))
                required.update(by_entity.get(target_type.casefold(), set()))
            source_class = classes.get(edge.source_temp_id, "")
            for fact in edge.properties:
                required.update(
                    by_property.get(
                        (source_class.casefold(), fact.property_name.casefold()), set()
                    )
                )
        return self._normalize_scope_keys(list(required), prepared.catalog_keys)

    async def _submit(
        self,
        prepared: _PreparedBatch,
        scope_keys: list[str],
        extraction: SemanticGraphPatchFragment,
        tool_context: Any,
    ) -> tuple[dict[str, Any], Any]:
        workspace = await operations.submit_batch(
            prepared.container.repository,
            prepared.container.ontology_cache,
            str(prepared.workspace.job.id),
            prepared.batch.batch_index,
            scope_keys,
            extraction,
        )
        return self._result_and_state(
            workspace,
            str(prepared.workspace.job.id),
            prepared.batch.batch_index,
            tool_context,
        ), workspace

    @staticmethod
    def _should_run_semantic_evidence(
        result: dict[str, Any], ingestion_id: str
    ) -> bool:
        mode = getattr(settings, "INGESTION_SEMANTIC_EVIDENCE_MODE", "off").casefold()
        if mode == "off":
            return False
        if mode == "canary":
            percent = getattr(settings, "INGESTION_SEMANTIC_EVIDENCE_CANARY_PERCENT", 5)
            import hashlib

            h = int(hashlib.md5(ingestion_id.encode("utf-8")).hexdigest(), 16)
            if (h % 100) >= percent:
                return False

        errors = result.get("errors", [])
        return any(
            isinstance(e, dict) and e.get("code") == "EVIDENCE_NOT_GROUNDED"
            for e in errors
        )

    async def _handle_semantic_evidence_validation(
        self,
        prepared: _PreparedBatch,
        scope_keys: list[str],
        extraction: SemanticGraphPatchFragment,
        result: dict[str, Any],
        tool_context: Any,
    ) -> tuple[dict[str, Any], Any, SemanticGraphPatchFragment, int]:
        errors = result.get("errors", [])
        evidence_issues = [
            e
            for e in errors
            if isinstance(e, dict) and e.get("code") == "EVIDENCE_NOT_GROUNDED"
        ]
        if not evidence_issues:
            workspace = await operations.required_workspace(
                prepared.container.repository, str(prepared.workspace.job.id)
            )
            return result, workspace, extraction, 0

        chunks = prepared.payload.get("batch", {}).get("chunks", [])
        chunks_by_index = {
            c.get("chunkIndex"): c.get("text", "")
            for c in chunks
            if isinstance(c, dict) and "chunkIndex" in c
        }

        # Build issue items
        issues_payload: list[dict[str, Any]] = []
        issues_by_id: dict[str, dict[str, Any]] = {}
        for _, issue in enumerate(evidence_issues):
            loc = issue.get("location", "")
            target_chunk_idx, evidence_text, claim_info = (
                self._extract_claim_and_evidence(extraction, loc)
            )
            issue_id = f"{prepared.batch.batch_index}:{target_chunk_idx}:{loc}"
            chunk_text = chunks_by_index.get(target_chunk_idx, "")
            item = {
                "issue_id": issue_id,
                "chunk_id": f"chunk-{target_chunk_idx}",
                "chunk_index": target_chunk_idx,
                "location": loc,
                "claim": claim_info,
                "evidence_text": evidence_text,
                "candidate_source_excerpt": issue.get("message", ""),
                "source_chunk_text": chunk_text,
            }
            issues_payload.append(item)
            issues_by_id[issue_id] = item

        semantic_payload = {
            "batch_index": prepared.batch.batch_index,
            "ingestionId": str(prepared.workspace.job.id),
            "issues": issues_payload,
        }

        calls = 1
        try:
            val_res = await self._model.validate_evidence(
                semantic_payload, tool_context
            )
        except Exception:
            # On validator error, fail closed
            workspace = await operations.required_workspace(
                prepared.container.repository, str(prepared.workspace.job.id)
            )
            return result, workspace, extraction, calls

        # Validate decisions
        decisions = val_res.decisions
        dec_ids = [d.issue_id for d in decisions]
        if len(dec_ids) != len(set(dec_ids)) or set(dec_ids) != set(issues_by_id):
            # Missing or duplicated issues -> fail closed
            workspace = await operations.required_workspace(
                prepared.container.repository, str(prepared.workspace.job.id)
            )
            return result, workspace, extraction, calls

        working_copy = extraction.model_copy(deep=True)
        has_unsupported = False
        has_uncertain = False
        repairable_issues: list[dict[str, Any]] = []

        for decision in decisions:
            issue_item = issues_by_id[decision.issue_id]
            chunk_text = chunks_by_index.get(issue_item["chunk_index"], "")

            if decision.verdict == EvidenceVerdict.EQUIVALENT:
                quote = decision.source_quote or ""
                # Strict verbatim verification
                if quote and quote in chunk_text:
                    self._apply_source_quote_at_location(
                        working_copy, issue_item["location"], quote
                    )
                else:
                    has_uncertain = True
            elif decision.verdict == EvidenceVerdict.REPAIRABLE:
                repairable_issues.append(issue_item)
            elif decision.verdict == EvidenceVerdict.UNSUPPORTED:
                has_unsupported = True
            else:
                has_uncertain = True

        # If any unsupported, make batch terminal fail-closed
        if has_unsupported:
            fail_result = self._failure(
                "explicit_extraction_failure",
                str(prepared.workspace.job.id),
                "EVIDENCE_NOT_GROUNDED_UNSUPPORTED",
            )
            store_checkpoint(tool_context, fail_result)
            return fail_result, prepared.workspace, working_copy, calls

        # Re-submit with updated exact quotes
        new_result, workspace = await self._submit(
            prepared, scope_keys, working_copy, tool_context
        )

        # Log structured semantic validation event
        log_ingestion_event(
            "INGESTION_SEMANTIC_EVIDENCE_VALIDATION",
            request={
                "ingestion_id": str(prepared.workspace.job.id),
                "batch_index": prepared.batch.batch_index,
            },
            payload={
                "issue_count": len(issues_payload),
                "semantic_validator_calls": 1,
                "decisions": {
                    "EQUIVALENT": sum(
                        1 for d in decisions if d.verdict == EvidenceVerdict.EQUIVALENT
                    ),
                    "REPAIRABLE": len(repairable_issues),
                    "UNSUPPORTED": sum(
                        1 for d in decisions if d.verdict == EvidenceVerdict.UNSUPPORTED
                    ),
                    "UNCERTAIN": sum(
                        1 for d in decisions if d.verdict == EvidenceVerdict.UNCERTAIN
                    ),
                },
                "final_stage": new_result.get("stage"),
                "success": new_result.get("success"),
            },
        )

        if has_uncertain and not new_result.get("success"):
            # Mark needs human review if uncertain
            new_result["needsReview"] = True
            store_checkpoint(tool_context, new_result)

        return new_result, workspace, working_copy, calls

    @staticmethod
    def _extract_claim_and_evidence(
        extraction: SemanticGraphPatchFragment, location: str
    ) -> tuple[int, str, dict[str, Any]]:
        parts = location.split(".")
        chunk_idx = 0
        ev_text = ""
        claim_info: dict[str, Any] = {"location": location}

        try:
            if parts[0] == "nodes" and len(parts) >= 2 and parts[1].isdigit():
                node_idx = int(parts[1])
                if node_idx < len(extraction.nodes):
                    node = extraction.nodes[node_idx]
                    claim_info = {
                        "kind": "node",
                        "className": node.class_name,
                        "tempId": node.temp_id,
                    }
                    if "properties" in parts:
                        p_pos = parts.index("properties")
                        if p_pos + 1 < len(parts) and parts[p_pos + 1].isdigit():
                            prop_idx = int(parts[p_pos + 1])
                            if prop_idx < len(node.properties):
                                prop = node.properties[prop_idx]
                                claim_info["property"] = prop.property_name
                                claim_info["value"] = prop.value
                                ev_list = prop.evidence
                                ev_idx = 0
                                if "evidence" in parts:
                                    e_pos = parts.index("evidence")
                                    if (
                                        e_pos + 1 < len(parts)
                                        and parts[e_pos + 1].isdigit()
                                    ):
                                        ev_idx = int(parts[e_pos + 1])
                                if ev_idx < len(ev_list):
                                    ev_text = ev_list[ev_idx].text
                                    chunk_idx = ev_list[ev_idx].chunk_index
                    elif "evidence" in parts:
                        e_pos = parts.index("evidence")
                        ev_idx = 0
                        if e_pos + 1 < len(parts) and parts[e_pos + 1].isdigit():
                            ev_idx = int(parts[e_pos + 1])
                        if ev_idx < len(node.evidence):
                            ev_text = node.evidence[ev_idx].text
                            chunk_idx = node.evidence[ev_idx].chunk_index
            elif parts[0] == "edges" and len(parts) >= 2 and parts[1].isdigit():
                edge_idx = int(parts[1])
                if edge_idx < len(extraction.edges):
                    edge = extraction.edges[edge_idx]
                    claim_info = {
                        "kind": "edge",
                        "edgeName": edge.edge_name,
                        "source": edge.source_temp_id,
                        "target": edge.target_temp_id,
                    }
                    if "properties" in parts:
                        p_pos = parts.index("properties")
                        if p_pos + 1 < len(parts) and parts[p_pos + 1].isdigit():
                            prop_idx = int(parts[p_pos + 1])
                            if prop_idx < len(edge.properties):
                                prop = edge.properties[prop_idx]
                                claim_info["property"] = prop.property_name
                                claim_info["value"] = prop.value
                                ev_list = prop.evidence
                                ev_idx = 0
                                if "evidence" in parts:
                                    e_pos = parts.index("evidence")
                                    if (
                                        e_pos + 1 < len(parts)
                                        and parts[e_pos + 1].isdigit()
                                    ):
                                        ev_idx = int(parts[e_pos + 1])
                                if ev_idx < len(ev_list):
                                    ev_text = ev_list[ev_idx].text
                                    chunk_idx = ev_list[ev_idx].chunk_index
                    elif "evidence" in parts:
                        e_pos = parts.index("evidence")
                        ev_idx = 0
                        if e_pos + 1 < len(parts) and parts[e_pos + 1].isdigit():
                            ev_idx = int(parts[e_pos + 1])
                        if ev_idx < len(edge.evidence):
                            ev_text = edge.evidence[ev_idx].text
                            chunk_idx = edge.evidence[ev_idx].chunk_index
        except Exception:
            pass

        return chunk_idx, ev_text, claim_info

    @staticmethod
    def _apply_source_quote_at_location(
        extraction: SemanticGraphPatchFragment, location: str, quote: str
    ) -> None:
        parts = location.split(".")
        try:
            if parts[0] == "nodes" and len(parts) >= 2 and parts[1].isdigit():
                node_idx = int(parts[1])
                if node_idx < len(extraction.nodes):
                    node = extraction.nodes[node_idx]
                    if "properties" in parts:
                        p_pos = parts.index("properties")
                        if p_pos + 1 < len(parts) and parts[p_pos + 1].isdigit():
                            prop_idx = int(parts[p_pos + 1])
                            if prop_idx < len(node.properties):
                                prop = node.properties[prop_idx]
                                ev_idx = 0
                                if "evidence" in parts:
                                    e_pos = parts.index("evidence")
                                    if (
                                        e_pos + 1 < len(parts)
                                        and parts[e_pos + 1].isdigit()
                                    ):
                                        ev_idx = int(parts[e_pos + 1])
                                if ev_idx < len(prop.evidence):
                                    prop.evidence[ev_idx].text = quote
                    elif "evidence" in parts:
                        e_pos = parts.index("evidence")
                        ev_idx = 0
                        if e_pos + 1 < len(parts) and parts[e_pos + 1].isdigit():
                            ev_idx = int(parts[e_pos + 1])
                        if ev_idx < len(node.evidence):
                            node.evidence[ev_idx].text = quote
            elif parts[0] == "edges" and len(parts) >= 2 and parts[1].isdigit():
                edge_idx = int(parts[1])
                if edge_idx < len(extraction.edges):
                    edge = extraction.edges[edge_idx]
                    if "properties" in parts:
                        p_pos = parts.index("properties")
                        if p_pos + 1 < len(parts) and parts[p_pos + 1].isdigit():
                            prop_idx = int(parts[p_pos + 1])
                            if prop_idx < len(edge.properties):
                                prop = edge.properties[prop_idx]
                                ev_idx = 0
                                if "evidence" in parts:
                                    e_pos = parts.index("evidence")
                                    if (
                                        e_pos + 1 < len(parts)
                                        and parts[e_pos + 1].isdigit()
                                    ):
                                        ev_idx = int(parts[e_pos + 1])
                                if ev_idx < len(prop.evidence):
                                    prop.evidence[ev_idx].text = quote
                    elif "evidence" in parts:
                        e_pos = parts.index("evidence")
                        ev_idx = 0
                        if e_pos + 1 < len(parts) and parts[e_pos + 1].isdigit():
                            ev_idx = int(parts[e_pos + 1])
                        if ev_idx < len(edge.evidence):
                            edge.evidence[ev_idx].text = quote
        except Exception:
            pass

    @staticmethod
    def _result_and_state(
        workspace: Any, ingestion_id: str, batch_index: int, tool_context: Any
    ) -> dict[str, Any]:
        result = batch_result_payload(workspace, batch_index)
        tool_context.state["active_ingestion_id"] = ingestion_id
        if result.get("submittedBatchIndex") == batch_index and result.get("success"):
            update_batch_accumulator(
                tool_context, ingestion_id, batch_index, result, workspace
            )
        store_checkpoint(tool_context, result)
        return result

    @staticmethod
    def _should_repair(result: dict[str, Any]) -> bool:
        return (
            result.get("stage") == "repair_required"
            and bool(result.get("retryRequired"))
            and isinstance(result.get("repairContext"), dict)
        )

    @staticmethod
    def _failure(stage: str, ingestion_id: str, code: str) -> dict[str, Any]:
        return {
            "success": False,
            "stage": stage,
            "terminal": True,
            "retryRequired": False,
            "ingestionId": ingestion_id,
            "nextAction": None,
            "errors": [{"code": code, "message": code, "retryable": False}],
        }

    @staticmethod
    def _log_metrics(
        ingestion_id: str,
        batch_index: int,
        strategy: str,
        calls: int,
        prompt_bytes: int,
        requested: list[str],
        resolved: list[str],
        result: dict[str, Any],
    ) -> None:
        log_ingestion_event(
            "STRUCTURED_BATCH_EXECUTION",
            request={"ingestion_id": ingestion_id, "batch_index": batch_index},
            payload={
                "strategy": strategy,
                "logicalModelCalls": calls,
                "providerRetryAttempts": None,
                "promptBytes": prompt_bytes,
                "requestedScopeKeys": requested,
                "resolvedScopeKeys": resolved,
                "stage": result.get("stage"),
                "success": result.get("success"),
                "validationAttempts": result.get(
                    "validationAttempts", result.get("attempt")
                ),
            },
        )


__all__ = [
    "AdkBatchModel",
    "BatchExecutionModule",
    "BatchExtractionOutput",
    "BatchModel",
    "EvidenceVerdict",
    "ScopeSelectionOutput",
    "SemanticEvidenceDecision",
    "SemanticEvidenceValidationResult",
]
