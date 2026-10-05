---
name: ingestion
description: >
  Nạp tài liệu vào Knowledge Graph theo First-Pass Batch Ingestion bằng Claim Ledger:
  kiểm kê atomic claims, ánh xạ semantic đơn nhất, phát hiện schema gap có cấu trúc,
  và nạp đồ thị tri thức Neo4j khi người dùng xác nhận.
metadata:
  adk_additional_tools:
    - begin_ingestion
    - get_ingestion_batch
    - list_ontology_scopes
    - load_ontology_scopes
    - submit_ingestion_batch
    - get_schema_proposal
    - review_schema_proposal
    - apply_schema_proposal
    - rebase_ingestion
    - finalize_ingestion
    - fill_ingestion
    - get_ingestion_status
    - delete_document
    - rollback_document_version
---

# Ingestion Skill — First-Pass Batch Ingestion bằng Claim Ledger

## 1. Mệnh Lệnh Bất Biến (Top Invariants & Hard Stops)

1. **Một Submit duy nhất cho mỗi batch:** LLM đọc toàn bộ chunks trong batch và gửi qua `submit_ingestion_batch` duy nhất một lần.
2. **Single Source of Truth (Claim Ledger):** Mọi thông tin ngữ nghĩa được khai báo dưới dạng atomic claims trong `chunks[].claims`.
3. **Khai báo Entity & Claim định danh bắt buộc:** Mọi thực thể mới trong `entities[]` (ví dụ `org_1`, `person_1`) **bắt buộc phải có claim trích xuất thuộc tính định danh** (thường là `propertyName: "name"`).
4. **Grounding Policy:** Mỗi claim bắt buộc chọn `evidenceRef` từ `evidenceUnits[]` của chunk. Giá trị định danh (`name`, mã số, literal) phải xuất hiện nguyên văn (`VERBATIM`) trong evidence nguồn.
5. **Schema Gap là kết quả trích xuất hợp lệ:** Fact rõ ràng mà ontology chưa có thì đánh dấu `outcome: "SCHEMA_GAP"`. Tuyệt đối không nhồi fact có cấu trúc vào `notes` / `description`.
6. **🛑 ĐIỂM DỪNG BẮT BUỘC 1 (Cổng duyệt Schema):** Khi `submit_ingestion_batch` trả về `stage: "schema_review_required"`, **Agent không tự approve schema proposal. Chỉ sau trạng thái `APPROVED` từ người dùng mới được rebase và tiếp tục.** Agent PHẢI DỪNG GỌI TOOL NGAY LẬP TỨC, xuất bản báo cáo tóm tắt đề xuất cho người dùng và CHỜ phản hồi. **TUYỆT ĐỐI KHÔNG TỰ Ý GỌI `review_schema_proposal` HAY `apply_schema_proposal` TRONG LƯỢT NÀY.**
7. **🛑 ĐIỂM DỪNG BẮT BUỘC 2 (Cấm tự Restart khi Lỗi):** Nếu submit bị `EXTRACTION_REJECTED` hoặc lỗi tool, dừng lại báo cáo cho người dùng. **TUYỆT ĐỐI KHÔNG GỌI LẠI `begin_ingestion` ĐỂ RESTART TỰ ĐỘNG.**
8. **🛑 ĐIỂM DỪNG BẮT BUỘC 3 (Cổng ghi DB):** Chỉ gọi `fill_ingestion` sau khi người dùng xác nhận đồng ý nạp vào Neo4j.

---

## 2. Quy Trình Vận Hành (State Machine)

### 🔹 Giai đoạn 1: Xử lý Batch thông thường (Extraction)
1. Gọi `list_ontology_scopes()` và `load_ontology_scopes()` để chọn một hoặc nhiều scope phù hợp.
2. Gọi `get_ingestion_batch(ingestion_id, batch_index)`.
3. Kiểm kê atomic claims cho từng chunk trong batch:
   - `MAPPED`: Khai báo `mapping` (`kind: "PROPERTY"` hoặc `"EDGE"`).
   - `SCHEMA_GAP`: Khai báo `schemaGap` (`technicalName`, `dataType`, `value`, `reason`).
   - `DUPLICATE`: Trùng lặp fact đã trích xuất (dùng `factRef`).
   - `noRelevantFactReason`: Nếu chunk không chứa fact nào.
4. Gọi `submit_ingestion_batch(ingestion_id, batch_index, scope_keys, extraction)`.

### 🔹 Giai đoạn 2: Điều hướng theo kết quả Submit
- **Nếu `stage == "batch_staged"`:**
  - Nếu `nextAction == "process_next_batch"`: Tăng `batch_index` và tiếp tục Giai đoạn 1.
  - Nếu `nextAction == "finalize"`: Gọi `finalize_ingestion(ingestion_id)`, sau đó xuất bản báo cáo tổng kết đồ thị (Mục 4.2) và xin xác nhận nạp Neo4j.
- **Nếu `stage == "schema_review_required"`:**
  - **DỪNG LẠI NGAY.** Xuất bản thông báo chờ duyệt schema (Mục 4.1) và kết thúc lượt trò chuyện để chờ người dùng.
- **Nếu `stage == "extraction_rejected"`:**
  - **DỪNG LẠI NGAY.** Báo cáo lỗi hard validation cho người dùng. Không thử lại.

### 🔹 Giai đoạn 3: Khi Người Dùng Phản Hồi Duyệt Schema ("Đồng ý duyệt đề xuất" / "Duyệt đề xuất")
CHỈ KHI nhận được tin nhắn duyệt từ người dùng, Agent mới thực thi tuần tự:
1. `review_schema_proposal(proposal_id="<proposalId hoặc technicalName>", approved=True, reviewed_by="user")`
2. `apply_schema_proposal(proposal_id="<proposalId hoặc technicalName>", new_version_code="v1.1", applied_by="user")` -> Nhận `ontologyVersionId`.
3. `rebase_ingestion(ingestion_id="<ingestionId>", target_ontology_version_id="<ontologyVersionId>")`
4. Tiếp tục gọi `get_ingestion_batch` cho batch tiếp theo (hoặc batch hiện tại) để tiếp tục Giai đoạn 1.

### 🔹 Giai đoạn 4: Khi Người Dùng Đồng Ý Nạp Dữ Liệu ("Đồng ý nạp" / "Ghi vào Neo4j")
Gọi `fill_ingestion(ingestion_id)` và thông báo hoàn tất.

---

## 3. Cấu Trúc Payload Mẫu (`SemanticBatchExtraction`)

```json
{
  "entities": [
    {"tempId": "org_1", "className": "organization"},
    {"tempId": "person_1", "className": "person"}
  ],
  "chunks": [
    {
      "chunkIndex": 0,
      "claims": [
        {
          "claimId": "c_0_1",
          "statement": "Tên tổ chức là Hệ Thống Taekwondo Văn Quán",
          "evidence": {"chunkIndex": 0, "evidenceRef": "chunk:0:line:1"},
          "outcome": "MAPPED",
          "mapping": {
            "kind": "PROPERTY",
            "entityRef": "org_1",
            "propertyName": "name",
            "value": "Hệ Thống Taekwondo Văn Quán"
          }
        },
        {
          "claimId": "c_0_2",
          "statement": "Người sáng lập là Phùng Thế Lịch",
          "evidence": {"chunkIndex": 0, "evidenceRef": "chunk:0:line:2"},
          "outcome": "MAPPED",
          "mapping": {
            "kind": "PROPERTY",
            "entityRef": "person_1",
            "propertyName": "name",
            "value": "Phùng Thế Lịch"
          }
        },
        {
          "claimId": "c_0_3",
          "statement": "Đến năm 2019 có 6 cơ sở hoạt động",
          "evidence": {"chunkIndex": 0, "evidenceRef": "chunk:0:line:3"},
          "outcome": "SCHEMA_GAP",
          "schemaGap": {
            "kind": "PROPERTY",
            "entityRef": "org_1",
            "technicalName": "facility_count",
            "displayName": "Số lượng cơ sở",
            "dataType": "INTEGER",
            "value": 6,
            "reason": "Ontology chưa có thuộc tính lưu số cơ sở của tổ chức"
          }
        }
      ]
    },
    {
      "chunkIndex": 1,
      "claims": [],
      "noRelevantFactReason": "Đoạn văn bản chỉ chứa lời ngỏ chào mừng mở đầu"
    }
  ]
}
```

---

## 4. Quy Chuẩn Báo Cáo Người Dùng Tại Các Cổng Duyệt

### 4.1 Khi Dừng Chờ Duyệt Schema (`schema_review_required`)
Xuất bản thông báo gồm 4 phần:
1. **Tiến độ:** Số batch đã xử lý / tổng số batch.
2. **Tri thức đã trích xuất:** Tóm tắt thực thể đã staged.
3. **Danh sách Schema Gaps cần duyệt:**
   - **Tên thuộc tính / quan hệ:** (ví dụ `predecessor_name` hoặc `facility_count`)
   - **Thực thể áp dụng:** (ví dụ `organization`)
   - **Kiểu dữ liệu & Giá trị trích xuất:** (ví dụ `STRING` / `CLB Taekwondo Văn Quán`)
   - **Lý do đề xuất:** (ví dụ: *Tài liệu nêu tên tiền thân của CLB nhưng ontology chưa có trường lưu trữ*)
4. **Hướng dẫn:** *"Bạn chỉ cần phản hồi **'Đồng ý duyệt đề xuất'** để hệ thống tự động cập nhật ontology và tiếp tục nạp dữ liệu."*

### 4.2 Khi Hoàn Tất Chờ Nạp DB (`finalize_ingestion` / `ready_to_fill`)
Xuất bản báo cáo tổng kết đồ thị:
1. **Tổng quan:** Tổng số Nodes, Properties, Edges.
2. **Chi tiết:** Danh sách các thực thể chính (Tổ chức, Huấn luyện viên, Cơ sở, Lịch học...).
3. **Xin xác nhận:** *"Toàn bộ tri thức đã sẵn sàng. Bạn có đồng ý nạp chính thức vào cơ sở dữ liệu Neo4j không?"*
