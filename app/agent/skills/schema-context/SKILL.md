---
name: schema-context
description: >
  Chọn scope phù hợp từ catalog và tải compiled schema context đã ghim phiên bản,
  dùng chung cho ingestion và Graph QA.
metadata:
  adk_additional_tools:
    - list_active_schema_scopes
    - load_active_schema_context
---

# Schema Context

Skill này chỉ điều phối schema context; service quyết định catalog, version,
merge và digest. Không tạo hoặc thay đổi ontology.

1. Tải catalog scope của caller.
2. Chọn scope chỉ từ catalog theo policy của caller.
3. Tải schema context theo version đã ghim và các scope đã chọn.
4. Lưu `ontologyVersionId`, `scopeKeys`, `schemaDigest` trong state caller.

## Policy caller

- Ingestion dùng ontology DRAFT đã ghim ở job và ưu tiên bao phủ đầy đủ. Dùng
  wrappers ingestion để service lấy version của job; schema gap vẫn theo review,
  apply và rebase.
- Graph QA chỉ dùng `list_active_schema_scopes` và `load_active_schema_context`.
  Chọn scope hẹp khi câu hỏi rõ miền; nếu mơ hồ thì không lọc retrieval.
- Skill không có quyền proposal, rebase, commit hoặc bất kỳ thao tác ghi nào.
