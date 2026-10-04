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
    - create_schema_proposal
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

## 1. Mệnh Lệnh Bất Biến (Top Invariants)

1. **Một Semantic Extraction Submit duy nhất cho mỗi batch:**
   LLM đọc toàn bộ các chunk trong batch và gửi trích xuất một lần duy nhất qua `submit_ingestion_batch`. Không gọi LLM riêng theo từng chunk.
2. **Single Source of Truth (Claim Ledger):**
   Không gửi đồng thời `nodes[]`/`edges[]` và `coverage[]`. Mọi thông tin ngữ nghĩa được khai báo dưới dạng atomic claims trong `chunks[].claims`. Backend sẽ tự động compile đồ thị và tổng hợp canonical coverage.
3. **Thực thể đã Staged từ batch trước (`canonicalGraphContext`):**
   Nếu quan hệ trỏ tới thực thể đã có ở các batch trước (trong `canonicalGraphContext`), dùng `entityRef` (ví dụ `entity:<stableKey>`). Không khai báo lại thực thể cũ.
4. **Schema Gap là kết quả trích xuất hợp lệ:**
   Nếu tài liệu chứa thông tin rõ ràng nhưng ontology chưa có thuộc tính hoặc quan hệ tương ứng, đánh dấu claim là `SCHEMA_GAP` kèm định nghĩa có cấu trúc. Không ép fact vào các trường generic (`description`, `notes`) và không xem đây là lỗi trích xuất.
5. **Không tự động Repair:**
   Không có vòng lặp repair tự động trong quy trình chuẩn. Nếu submit bị `EXTRACTION_REJECTED` (lỗi hard validation), dừng lại và báo cáo nguyên nhân.
6. **Xác nhận người dùng trước khi ghi Neo4j:**
   Chỉ gọi `fill_ingestion` khi người dùng đã duyệt và xác nhận ghi tri thức.
7. **Cổng Duyệt Schema Proposal (Human-in-the-loop):**
   Agent tuyệt đối không tự approve schema proposal. Chỉ sau trạng thái `APPROVED` từ người dùng mới được rebase và tiếp tục.

---

## 2. Quy Trình Bắt Buộc Khi Xử Lý Batch

Với mỗi batch được lấy từ `get_ingestion_batch`:

1. **Chọn Ontology Scopes:** Gọi `list_ontology_scopes()` và `load_ontology_scopes()` để chọn một hoặc nhiều scope phù hợp với nội dung batch.
2. **Đọc toàn bộ chunks** trong batch.
3. **Kiểm kê tất cả atomic claims** trong từng chunk (mỗi thông tin có nghĩa là 1 claim độc lập).
3. **Không map ontology vội** trước khi hoàn tất kiểm kê claim của chunk.
4. **Chọn đúng một outcome** cho mỗi claim:
   - `MAPPED`: Ontology đã hỗ trợ đầy đủ.
   - `DUPLICATE`: Fact lặp lại một logical fact đã có (dùng `factRef`).
   - `SCHEMA_GAP`: Fact rõ ràng nhưng ontology chưa hỗ trợ.
   - `AMBIGUOUS`: Văn bản mơ hồ, không đủ căn cứ xác thực.
5. **Khai báo thực thể (`entities[]`):** Thực thể mới dùng `tempId` và `className`; thực thể cũ từ batch trước dùng `entityRef`.
6. **Map các claim được ontology hỗ trợ** vào `mapping` (`PROPERTY` hoặc `EDGE`).
7. **Tạo structured schema gap** cho các claim chưa được hỗ trợ (`schemaGap`).
8. **Kiểm tra identity claims (BẮT BUỘC):** Mọi thực thể mới trong `entities[]` (ví dụ `org_1`, `coach_1`) **bắt buộc phải có claim trích xuất thuộc tính định danh** (thường là `propertyName: "name"`) theo yêu cầu `identityStrategy.required` của ontology. Không được khai báo entity để nối edge mà quên trích xuất claim `name` của entity đó.
9. **Chọn evidence bằng `evidenceRef` (BẮT BUỘC cho payload mới):** `get_ingestion_batch` trả về `evidenceUnits[]` cho từng chunk. Mỗi claim phải trỏ tới một `evidenceRef` có sẵn trong chính chunk đó. **Không tự viết, rút gọn, paraphrase hoặc copy lại `evidence.text`.** Backend sẽ lấy nguyên văn từ source theo `evidenceRef`.
10. **Property value phải có trong evidence:** Với `PROPERTY` hoặc Property `SCHEMA_GAP`, giá trị phải có thể được backend khôi phục deterministic từ evidence đã chọn (cho phép chuẩn hóa hình thức như `15/05/2012` → `2012-05-15`). Không suy đoán giá trị ngoài evidence.
11. **Kiểm tra completeness:** Mỗi chunk trong batch bắt buộc có đúng một mục trong `chunks[]`. Nếu chunk không có fact nào, bắt buộc điền `noRelevantFactReason`.
12. **Gọi `submit_ingestion_batch` một lần duy nhất.**

---

## 3. Cấu Trúc Payload `SemanticBatchExtraction`

```json
{
  "entities": [
    {
      "tempId": "org_1",
      "className": "organization"
    },
    {
      "tempId": "coach_1",
      "className": "person"
    }
  ],
  "chunks": [
    {
      "chunkIndex": 0,
      "claims": [
        {
          "claimId": "c_1",
          "statement": "Tên tổ chức là Hệ Thống Taekwondo Văn Quán",
          "evidence": {
            "chunkIndex": 0,
            "evidenceRef": "chunk:0:line:1"
          },
          "outcome": "MAPPED",
          "mapping": {
            "kind": "PROPERTY",
            "entityRef": "org_1",
            "propertyName": "name",
            "value": "Hệ Thống Taekwondo Văn Quán"
          }
        },
        {
          "claimId": "c_2",
          "statement": "Câu lạc bộ thành lập ngày 15/05/2012",
          "evidence": {
            "chunkIndex": 0,
            "evidenceRef": "chunk:0:line:2"
          },
          "outcome": "MAPPED",
          "mapping": {
            "kind": "PROPERTY",
            "entityRef": "org_1",
            "propertyName": "founded_date",
            "value": "2012-05-15"
          }
        },
        {
          "claimId": "c_3",
          "statement": "Võ sư sáng lập tên là Phùng Thế Lịch",
          "evidence": {
            "chunkIndex": 0,
            "evidenceRef": "chunk:0:line:3"
          },
          "outcome": "MAPPED",
          "mapping": {
            "kind": "PROPERTY",
            "entityRef": "coach_1",
            "propertyName": "name",
            "value": "Phùng Thế Lịch"
          }
        },
        {
          "claimId": "c_4",
          "statement": "Do Phùng Thế Lịch sáng lập",
          "evidence": {
            "chunkIndex": 0,
            "evidenceRef": "chunk:0:line:4"
          },
          "outcome": "MAPPED",
          "mapping": {
            "kind": "EDGE",
            "edgeName": "founded_by",
            "sourceRef": "org_1",
            "targetRef": "coach_1"
          }
        },
        {
          "claimId": "c_5",
          "statement": "Đến năm 2019 có 6 cơ sở hoạt động",
          "evidence": {
            "chunkIndex": 0,
            "evidenceRef": "chunk:0:line:5"
          },
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
      "noRelevantFactReason": "Đoạn văn bản chỉ chứa tiêu đề phân đoạn và lời chào mở đầu"
    }
  ],
  "warnings": []
}
```

---

## 4. Các Ví Dụ Mẫu Trọng Yếu

### 4.1 Mixed Chunk (Vừa Mapped vừa Schema Gap)
- Chunk chứa cả ngày thành lập (hỗ trợ), người sáng lập (hỗ trợ) và số lượng phòng tập (ontology chưa có).
- Khai báo 2 claims `MAPPED` và 1 claim `SCHEMA_GAP`.
- Backend tự động tổng hợp coverage canonical thành `PARTIALLY_MAPPED` mà không gây conflict.

### 4.2 Fact Lặp Lại Giữa Các Chunk (`DUPLICATE`)
- Khi một chunk nhắc lại thông tin đã được trích xuất ở chunk trước:
  ```json
  {
    "claimId": "c_dup_1",
    "statement": "Phùng Thế Lịch là huấn luyện viên trưởng",
    "evidence": {"chunkIndex": 2, "evidenceRef": "chunk:2:line:1"},
    "outcome": "DUPLICATE",
    "factRef": "claim:c_founder"
  }
  ```
- Backend gán evidence bổ sung vào fact đã có, không tạo node/edge trùng lặp.

### 4.3 Thực Thể Staged từ Batch Trước (`entityRef`)
- Khi nối quan hệ tới thực thể đã staged ở batch trước:
  ```json
  "entities": [
    {
      "className": "organization",
      "entityRef": "entity:9f8a7c2b..."
    }
  ]
  ```

### 4.4 Quan Hệ Thiếu trong Ontology (Relationship Schema Gap)
- Khi phát hiện mối quan hệ rõ ràng nhưng ontology chưa có:
  ```json
  "schemaGap": {
    "kind": "RELATIONSHIP",
    "technicalName": "operates_facility",
    "displayName": "Vận hành cơ sở",
    "sourceRef": "org_1",
    "targetRef": "facility_1",
    "cardinality": "ONE_TO_MANY",
    "reason": "Tài liệu mô tả tổ chức trực tiếp quản lý cơ sở đào tạo"
  }
  ```

---

## 5. Xử Lý Kết Quả Sau Khi Submit

- **`FIRST_PASS_STAGED` (`stage: batch_staged`):**
  Batch hợp lệ và đã được staged. Kiểm tra `nextAction`:
  - `process_next_batch`: Tiếp tục gọi `get_ingestion_batch` cho batch kế tiếp.
  - `finalize`: Tất cả các batch đã hoàn tất, tiến hành gọi `finalize_ingestion`.

- **`SCHEMA_REVIEW_REQUIRED` (`stage: schema_review_required`):**
  Trích xuất thành công và backend đã lưu trữ claim ledger cùng các đề xuất schema gaps.
  Agent thông báo cho người dùng danh sách đề xuất mở rộng schema kèm `proposalId` và chờ người dùng quyết định.
  Khi người dùng phản hồi **"Đồng ý duyệt đề xuất"** / **"Duyệt đề xuất"**:
  1. Gọi `review_schema_proposal(proposal_id, approved=True, reviewed_by="user")` (nếu không có proposal_id cụ thể, truyền `ingestion_id` hoặc để trống để hệ thống tự động tìm).
  2. Gọi `apply_schema_proposal(proposal_id, new_version_code="vX.X", applied_by="user")`.
  3. Gọi `rebase_ingestion(ingestion_id, target_ontology_version_id=version_id)` (hệ thống tự động biên dịch lại batch từ Claim Ledger mà không gọi lại LLM).
  4. Tiếp tục gọi `get_ingestion_batch` cho batch tiếp theo.

- **`EXTRACTION_REJECTED` (`stage: extraction_rejected`):**
  Payload vi phạm các quy tắc deterministic bắt buộc (ví dụ `evidenceRef` không tồn tại, legacy evidence không grounded, property value không được evidence hỗ trợ, datatype sai, thiếu chunk ledger). Dừng workflow và báo cáo lỗi cho người dùng.

---

## 6. Hoàn Tất và Nạp Dữ Liệu (Finalize & Fill)

1. Khi tất cả batch đã `STAGED`, gọi `finalize_ingestion(ingestion_id)`.
2. Báo cáo bản tóm tắt tri thức cho người dùng và xin xác nhận.
3. Chỉ gọi `fill_ingestion(ingestion_id)` khi nhận được xác nhận đồng ý từ người dùng.

---

## 7. Quy Chuẩn Báo Cáo Minh Bạch Cho Người Dùng (BẮT BUỘC)

Tuyệt đối không trả lời chung chung hoặc chỉ nêu trạng thái kỹ thuật (`awaiting_schema_approval`, `ready_to_fill`). Khi tương tác với người dùng tại các cổng duyệt, phải trình bày theo đúng định dạng sau:

### 7.1 Khi Dừng Chờ Duyệt Schema (`SCHEMA_REVIEW_REQUIRED` / `awaiting_schema_approval`)

Bắt buộc xuất bản thông báo gồm 4 phần:

1. **Tiến độ xử lý:** Nêu rõ số batch đã xử lý thành công / tổng số batch (ví dụ: *Đã hoàn thành Batch 0, đang xử lý Batch 1*).
2. **Tri thức đã trích xuất thành công (Staged Entities):** Liệt kê các thực thể đã được gom từ các batch trước (Tên, lớp thực thể, các thuộc tính chính).
3. **Chi tiết các Đề xuất mở rộng Schema (Schema Gaps) cần duyệt:** Trình bày dạng bảng hoặc danh sách chi tiết:
   - **Tên đề xuất / Thuộc tính:** (ví dụ: `facility_count`)
   - **Áp dụng cho thực thể:** (ví dụ: `organization` - CLB Taekwondo Văn Quán)
   - **Kiểu dữ liệu & Giá trị:** (ví dụ: `INTEGER`, giá trị: `6`)
   - **Bằng chứng trích dẫn:** (nguyên văn trong tài liệu)
   - **Lý do đề xuất:** (ví dụ: *Tài liệu mô tả số cơ sở nhưng bản thể hiện tại chưa có trường lưu trữ*)
4. **Hướng dẫn người dùng:** Thông báo rõ: *"Bạn chỉ cần phản hồi 'Đồng ý duyệt đề xuất' để hệ thống tự động cập nhật bản thể và tiếp tục nạp dữ liệu."*

### 7.2 Khi Hoàn Tất và Xin Phép Nạp Vào Neo4j (`finalize_ingestion` / `ready_to_fill`)

Bắt buộc xuất bản báo cáo tổng kết đồ thị tri thức:

1. **Tổng quan đồ thị:** Tổng số thực thể (Nodes), tổng số thuộc tính (Properties), tổng số quan hệ (Edges).
2. **Danh sách chi tiết các thực thể & quan hệ chính:**
   - Tổ chức, Huấn luyện viên, Địa điểm / Cơ sở, Chương trình học, Lịch học, Học phí...
3. **Câu hỏi xin xác nhận:** *"Toàn bộ tri thức trên đã sẵn sàng. Bạn có đồng ý nạp chính thức vào cơ sở dữ liệu Neo4j không?"*
