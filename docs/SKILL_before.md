---
name: ingestion
description: >
  Nạp tài liệu vào Knowledge Graph theo từng mẻ, chọn phạm vi bản thể động,
  trích xuất tri thức theo semantic contract tối giản, phát hiện schema gap,
  điều phối phê duyệt thay đổi bản thể và chỉ ghi Neo4j khi được yêu cầu.
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

# Ingestion Skill - Quy Chuẩn Điều Phối & Trích Xuất Toàn Diện

## 1. Mệnh Lệnh Bất Biến (Top Invariant Rules)
1. **Quy tắc `MAPPED` (Sống còn):** CHỈ đánh dấu `MAPPED` khi có ít nhất 1 `property` hoặc 1 `edge` mang `evidence.chunkIndex` trỏ đúng vào chunk đó. `node.evidence` hoặc việc chunk chỉ nhắc tên entity KHÔNG TÍNH là đã map fact. Nếu thông tin không có thuộc tính trong Ontology để điền -> BẮT BUỘC chọn `NO_RELEVANT_FACT` (hoặc `UNSUPPORTED_BY_ONTOLOGY`).
2. **Thực Thể Xuyên Batch (`canonicalGraphContext`):** Khi bắt đầu một batch mới, kiểm tra danh sách thực thể đã `STAGED` trong `canonicalGraphContext` từ `get_ingestion_batch`. Nếu quan hệ trỏ tới thực thể đã có (ví dụ Tổ chức, HLV), **BẮT BUỘC dùng reference `entity:<stableKey>`** làm `sourceTempId` hoặc `targetTempId`. **KHÔNG tạo lại node cũ** trong batch mới chỉ để nối quan hệ.
3. **Bảo Toàn Tri Thức Khi Sửa Lỗi (Repair Guard):** Khi Repair, **BẮT BUỘC giữ nguyên danh sách các Node và trường `name`** (định danh tự nhiên) đã hợp lệ ở lượt trước. Tuyệt đối không xóa bỏ node hoặc tự ý xóa bớt property cũ (tránh lỗi `REPAIR_DROPPED_VALID_NODE` và `REPAIR_DROPPED_VALID_FACT`).
4. **Dừng lại khi Terminal:** Nếu tool trả về `terminal=true` hoặc `nextAction` là `explicit_extraction_failure` / `report_tool_failure` -> Dừng workflow ngay lập tức, báo cáo lỗi cho người dùng; không cố thử lại.
5. **Không tự quyết định ghi/xóa:** Chỉ gọi `fill_ingestion`, xóa, rollback hoặc duyệt schema proposal khi có yêu cầu tường minh từ người dùng.
6. **Đơn nhiệm LLM:** Sau khi tải scope qua `load_ontology_scopes`, LLM trực tiếp tạo JSON và gọi `submit_ingestion_batch`; không ủy quyền cho sub-agent trung gian khác.

---

## 2. Quy Trình Chuẩn Từng Batch (SOP 5 Bước)

Với mỗi `batchIndex` cần xử lý:

```
[1. get_ingestion_batch] ──> Lấy chunks nguồn và canonicalGraphContext (các thực thể đã staged)
           │
           ▼
[2. load_ontology_scopes] ──> Nạp tập scope nhỏ nhất đủ bao phủ khái niệm trong batch
           │
           ▼
[3. Trích xuất Semantic] ──> Tạo SemanticGraphPatchFragment (Nodes, Edges, Coverage)
           │
           ▼
[4. submit_ingestion_batch] ──> Gửi fragment để hệ thống xác thực, guard và staging
           │
           ▼
[5. Đọc kết quả & Thực thi Next Action]:
     ├─ STAGED: Chuyển sang batch tiếp theo.
     ├─ REPAIR: Thực hiện Quy trình Sửa lỗi (Mục 5).
     └─ SCHEMA_GAP: Tạo Proposal và chờ người dùng phê duyệt (Mục 6).
```

---

## 3. Bản Đặc Tả Trích Xuất Semantic (Extraction Contract)

### 3.1 Node
- `className`: Tên kiểu thực thể chính xác từ ontology đã nạp.
- `tempId`: Chuỗi định danh tạm thời trong batch (ví dụ: `loc_1`, `sch_1`). **Khi repair, phải giữ nguyên chuỗi tempId này cho cùng một thực thể.**
- `properties`: Danh sách thuộc tính của thực thể. Bắt buộc điền các thuộc tính định danh (như `name`).
- **Không tự tạo trường `identity`**: Backend sẽ tự động sinh natural identity và stable key dựa trên các trường định danh.

### 3.2 Property Fact (Nơi chứa Fact)
Mỗi property bắt buộc có:
- `propertyName`: Thuộc tính tồn tại trong ontology đã nạp.
- `value`: Giá trị ngữ nghĩa trích xuất được từ văn bản.
- `evidence`: Danh sách bằng chứng `[{"chunkIndex": X, "text": "cụm từ gợi ý ngắn"}]`. Python Evidence Resolver sẽ tự động căn chỉnh (alignment) với văn bản nguồn verbatim.

### 3.3 Relationship (Edge) & Tham Chiếu Xuyên Batch (`ref`)
- `edgeName`: Tên quan hệ hợp lệ theo ontology (đúng domain và range).
- `sourceTempId` & `targetTempId`:
  - Nếu đầu mút là thực thể mới trong batch: Dùng `tempId` cục bộ (ví dụ: `loc_1`).
  - Nếu đầu mút là thực thể đã có từ batch trước (nằm trong `canonicalGraphContext`): Dùng reference dạng `entity:<stable-key>` (ví dụ: `entity:26b5331b...`).
- `evidence`: Bắt buộc có `chunkIndex` và `text` chứng minh quan hệ.

---

## 4. Quy Chuẩn Coverage (Phân Định Rạch Ròi 1-1)

Mỗi chunk trong batch bắt buộc có đúng 1 mục phân loại:

| Quyết định | Khi nào sử dụng? (Ràng buộc bắt buộc) |
| :--- | :--- |
| `MAPPED` | Có ít nhất 1 `property` hoặc 1 `edge` mang `evidence.chunkIndex` trỏ đúng vào chunk này. |
| `NO_RELEVANT_FACT` | Chunk là slogan, giới thiệu chung, lời mở đầu, hoặc thông tin (lịch sử, giải thưởng, quy mô) mà **Ontology không có thuộc tính để lưu**. |
| `DUPLICATE_EVIDENCE` | Chunk chứa fact đã được trích xuất ở các batch trước / canonical context và chunk hiện tại không tạo ra fact mới. |
| `UNSUPPORTED_BY_ONTOLOGY` *(hoặc `SCHEMA_GAP`)* | Chunk chứa tri thức cấu trúc cốt lõi mà Ontology hoàn toàn thiếu entity/property/relationship để biểu diễn -> Ghi rõ lý do vào `reason` để tạo Schema Proposal. |
| `NOT_RELEVANT` | Nội dung hoàn toàn nằm ngoài phạm vi tài liệu cần nạp. |
| `AMBIGUOUS` | Nội dung nguồn quá mơ hồ, không đủ căn cứ để trích xuất chắc chắn. |
| `FAILED` | Lỗi trích xuất ngữ nghĩa thực sự từ LLM khiến không thể xử lý chunk. |

> ⚠️ **Quy tắc kiểm tra chéo (Sanity Check):** Trước khi submit, lướt qua toàn bộ chunk đánh dấu `MAPPED`. Nếu không chỉ ra được `property` hoặc `edge` cụ thể nào mang `chunkIndex` đó -> **BẮT BUỘC chuyển thành `NO_RELEVANT_FACT`**.

---

## 5. Quy Trình Sửa Lỗi Batch (Repair Checklist 3 Bước)

Khi `submit_ingestion_batch` trả về lỗi (`success=false`, `terminal=false`):

1. **Bước 1 (Giữ nguyên nền tảng & Không xóa Node):** 
   - Sao chép toàn bộ các node, properties và edges đã hợp lệ ở lượt trước. 
   - Giữ nguyên chuỗi `tempId` và thuộc tính định danh `name` của các node cũ. Tuyệt đối không xóa bỏ node đã được chấp nhận ở lượt trước.
2. **Bước 2 (Khắc phục lỗi cục bộ theo chẩn đoán):**
   - Lỗi `EVIDENCE_NOT_GROUNDED`: Giữ nguyên fact/value, chỉ rút ngắn `evidence.text` thành cụm từ nguyên văn xuất hiện chính xác trong chunk nguồn.
   - Lỗi `MAPPED_WITHOUT_MAPPING`: Chuyển chunk bị phạt sang `NO_RELEVANT_FACT` (nếu không có fact mới) hoặc bổ sung property/edge có chứa chunk đó.
3. **Bước 3 (Kiểm tra chéo và Submit):** Đảm bảo giữ đủ các properties cũ và mọi chunk `MAPPED` đều có fact bảo chứng. Sau đó gọi lại `submit_ingestion_batch`.

---

## 6. Xử Lý Lỗi Ontology & Schema Proposal

Khi gặp lỗi ontology (`UNKNOWN_ENTITY_TYPE`, `UNKNOWN_PROPERTY`, `UNKNOWN_RELATIONSHIP`):
1. **Kiểm tra mã chẩn đoán:**
   - `RELATIONSHIP_MAPPING_MISMATCH`: Sửa lại `edgeName` theo danh sách quan hệ tương thích, không tạo proposal.
   - `MISSING_SCOPE`: Nạp thêm scope bị thiếu qua `load_ontology_scopes` rồi submit lại.
   - `SCHEMA_GAP_CANDIDATE`: Gọi `create_schema_proposal` với `proposal_type` hợp lệ (`NEW_RELATIONSHIP`, `NEW_ENTITY_TYPE`, `NEW_PROPERTY`...).
2. **Dừng batch & Chờ phê duyệt từ người dùng:**
   - **Người dùng từ chối:** Gọi `review_schema_proposal(approved=false)`, đổi chunk liên quan sang `NO_RELEVANT_FACT` hoặc `UNSUPPORTED_BY_ONTOLOGY` và trích xuất tiếp.
   - **Người dùng đồng ý:** Gọi `review_schema_proposal(approved=true)` -> `apply_schema_proposal(version)` -> `rebase_ingestion` -> Tiếp tục xử lý batch với ontology mới.

---

## 7. Hoàn Tất (Finalize & Fill)
1. **Finalize:** Khi tất cả các batch đã `STAGED`, gọi `finalize_ingestion(ingestion_id)`.
2. **Fill (Persistence):** Chỉ gọi `fill_ingestion(ingestion_id)` khi người dùng yêu cầu lưu/nhập dữ liệu vào Knowledge Graph Neo4j.
3. **Báo cáo tổng kết:** Báo cáo `ingestionId`, phiên bản Ontology, số node, edges, batches và trạng thái commit (`readbackVerified`).
