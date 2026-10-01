---
name: ingestion
description: >
  Ingest PDF, DOCX, Markdown, and TXT documents for a Taekwondo center into a
  source-grounded knowledge graph using durable batches, ontology validation,
  explicit persistence, incremental source lifecycle, deletion, and rollback.
metadata:
  adk_additional_tools:
    - begin_ingestion
    - get_ingestion_batch
    - submit_ingestion_batch
    - finalize_ingestion
    - fill_ingestion
    - get_ingestion_status
    - load_ontology_scope
    - validate_graph_patch
    - fill_graph_patch
    - delete_document
    - rollback_document_version
---

# Taekwondo document ingestion

You own the semantic mapping workflow. Python tools own deterministic parsing, validation,
identity, staging, persistence, provenance, and read-back verification. Never invent SQL or Cypher.

## Safety rules

- Never call `fill_ingestion` unless the user's request explicitly asks to save, persist, import,
  load, or write the extracted knowledge.
- Never call `delete_document` or `rollback_document_version` unless explicitly requested.
- Never describe staged candidates as committed domain graph data.
- Never invent belt ranks, coaches, schedules, fees, dates, facilities, policies, or relationships.
- Evidence text must be copied verbatim from the referenced chunk.
- Do not move to another batch until the current batch is staged successfully.

## Required workflow

1. Call `begin_ingestion(artifact_name, document_key?, scope_hint?)`.
   Keep the returned `ingestionId`. A resumed result continues the existing durable workspace.
2. For `nextBatch.batchIndex`, call `get_ingestion_batch` and read every returned chunk.
3. Select the smallest relevant ontology scope and call `load_ontology_scope`. Valid scopes are
   `core`, `course`, `training`, `belt`, `facility`, `finance`, and `event`.
4. Create exactly one `GraphPatchFragment` for the batch:
   - `ontologyVersion` must match the loaded projection;
   - every node uses an ontology `className` and identity fields;
   - every property has its own evidence;
   - every edge's evidence must prove the relationship, not merely co-occurrence;
   - coverage has one `MAPPED` or `NOT_RELEVANT` decision for every chunk.
5. Call `submit_ingestion_batch`. If it returns structured issues, repair only the affected facts
   and resubmit the same batch. Do not restart ingestion during repair.
6. Repeat steps 2–5 until `stage=ready_to_finalize`, then call `finalize_ingestion`.
7. If finalization returns `repair_required`, retrieve and repair only the listed batches, then
   finalize again.
8. If `stage=ready_to_fill`:
   - for extract-only requests, stop and report readiness;
   - for explicit persistence requests, call `fill_ingestion` and report its actual commit and
     read-back fields.

## GraphPatchFragment shape

```json
{
  "ontologyVersion": "v1",
  "nodes": [{
    "tempId": "course-1",
    "className": "Course",
    "identity": {"name": "Lớp Taekwondo nâng cao"},
    "properties": [{
      "propertyName": "monthlyTuition",
      "value": 1000000,
      "evidence": [{
        "source": "chuong-trinh.docx",
        "chunkIndex": 0,
        "section": "Học phí",
        "text": "Học phí: 1.000.000 đồng/tháng"
      }]
    }],
    "evidence": [{
      "source": "chuong-trinh.docx",
      "chunkIndex": 0,
      "text": "Lớp Taekwondo nâng cao"
    }]
  }],
  "edges": [],
  "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "Course and fee"}],
  "warnings": []
}
```

## Final response

Report only tool-confirmed facts: ingestion ID, staged batch count, readiness issues, commit status,
persisted node/edge counts, and read-back result. If persistence was not explicitly requested, say
that extraction is ready but the domain graph was not changed.
