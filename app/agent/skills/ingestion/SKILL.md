---
name: ingestion
description: >
  Nạp tài liệu vào Knowledge Graph theo từng mẻ, chọn phạm vi bản thể động,
  trích xuất tri thức theo semantic contract tối giản, phát hiện schema gap,
  điều phối phê duyệt thay đổi bản thể và chỉ ghi Neo4j khi được yêu cầu.
metadata:
  adk_additional_tools:
    - begin_ingestion
    - create_schema_proposal
    - get_schema_proposal
    - review_schema_proposal
    - apply_schema_proposal
    - rebase_ingestion
    - finalize_ingestion
    - fill_ingestion
    - get_ingestion_status
    - ingestion_batch_agent
---

# Ingestion workflow

Load this skill before starting ingestion and retain its instructions until the
workflow ends. The root agent coordinates the document workflow; it does not
retrieve chunks, choose scopes, extract a graph, submit a batch, or repair a
batch itself.

1. Call `begin_ingestion` for the supplied artifact. Read `ingestionId` and
   `nextBatch` from its result.
2. For each pending batch, call `ingestion_batch_agent` once with exactly this
   argument shape: `{ "request": "{\\"ingestionId\\":\\"...\\",\\"batchIndex\\":0}" }`.
   The batch agent owns exactly that batch:
   it retrieves the batch once, loads ontology scopes, creates a
   `SemanticGraphPatchFragment`, and submits or repairs it until it is staged,
   terminal, or blocked by schema review.
3. If the batch result reports a schema blocker, coordinate the existing
   proposal/review/apply/rebase tools at the root. After rebase, call the same
   batch agent again for that batch. Ontology scopes must be loaded again;
   cached document chunks remain valid.
4. When a batch is staged, use its returned `nextBatch` and call the batch
   agent for the next pending batch. Do not fetch completed batches again.
5. Once all batches are staged, call `finalize_ingestion`. Only call
   `fill_ingestion` after finalization reports `ready_to_fill` and the user
   has requested persistence.

The batch agent receives only batch primitives. Service tools enforce identity,
validation, canonicalization, and persistence invariants; do not restate or
override them in prompts. Returned tool payloads keep the established JSON
field names. The root's cross-batch context contains canonical entities only,
never old chunks or raw model output.
