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
1. **Quy tắc `MAPPED` (Sống còn):** CHỈ đánh dấu `MAPPED` khi có ít nhất 1 `property` hoặc 1 `edge` mang `evidence.chunkIndex` trỏ đúng vào chunk đó. `node.evidence` hoặc việc chunk chỉ nhắc tên entity KHÔNG TÍNH là đã map fact. Nếu chunk có tri thức nhưng Ontology không biểu diễn được -> BẮT BUỘC chọn `UNSUPPORTED_BY_ONTOLOGY`.
2. **Thực Thể Xuyên Batch (`canonicalGraphContext`):** Khi bắt đầu một batch mới, kiểm tra danh sách thực thể đã `STAGED` trong `canonicalGraphContext` từ `get_ingestion_batch`. Nếu quan hệ trỏ tới thực thể đã có (ví dụ Tổ chức, HLV), **BẮT BUỘC dùng reference `entity:<stableKey>`** làm `sourceTempId` hoặc `targetTempId`. **KHÔNG tạo lại node cũ** trong batch mới chỉ để nối quan hệ.
3. **Bảo Toàn Tri Thức Khi Sửa Lỗi (Repair Guard):** Khi Repair, **BẮT BUỘC giữ nguyên toàn bộ node, identity, property value/evidence, edge và coverage đã hợp lệ**. Chỉ semantic item bị tool báo lỗi mới được sửa.
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
[2. load_ontology_scopes] ──> Nạp một hoặc nhiều scope, dùng tập nhỏ nhất đủ bao phủ batch
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

## 4. Quy Chuẩn Coverage (Thứ Tự Ưu Tiên Phân Loại 1-1)

Mỗi chunk trong batch bắt buộc có đúng 1 quyết định coverage. Việc phân loại phải xét TẤT CẢ semantic facts có ý nghĩa trong chunk, không chỉ một fact đại diện.

| Thứ tự / Quyết định | Khi nào sử dụng? (Ràng buộc bắt buộc) |
| :--- | :--- |
| **1. UNSUPPORTED_BY_ONTOLOGY** *(hoặc SCHEMA_GAP)* | **Ưu tiên cao nhất nếu còn bất kỳ relevant semantic fact nào chưa thể biểu diễn bằng ontology hiện hành.** Nếu chunk chứa thông tin hữu ích nhưng thiếu Entity/Property/Relationship phù hợp để biểu diễn một hoặc nhiều fact, bắt buộc chọn quyết định này và nêu rõ các fact chưa được hỗ trợ trong `reason`. Điều này vẫn áp dụng ngay cả khi một số fact khác trong cùng chunk đã map thành công. Tuyệt đối không dùng MAPPED hoặc NO_RELEVANT_FACT để che phần tri thức chưa được ontology hỗ trợ. |
| **2. DUPLICATE_EVIDENCE** | Chỉ dùng khi toàn bộ relevant semantic facts của chunk đã tồn tại. Fact từ batch trước/baseline dùng ref trong `canonicalFactContext`; fact khai báo ngay payload hiện tại dùng `local-property:<tempId>:<propertyName>` hoặc `local-edge:<edgeIndex>`. Luôn gửi evidence quote grounded từ chunk hiện tại. |
| **3. AMBIGUOUS** | Nội dung chứa tri thức tiềm năng nhưng nguồn quá mơ hồ hoặc không đủ căn cứ để xác định fact một cách chắc chắn. |
| **4. NO_RELEVANT_FACT / NOT_RELEVANT** | Chỉ dùng khi chunk thực sự không chứa relevant semantic fact: tiêu đề đơn thuần, ký tự phân cách, boilerplate whitelist hoặc nội dung hoàn toàn ngoài phạm vi. Không được sử dụng khi chunk có fact nhưng ontology chưa hỗ trợ. |
| **5. MAPPED** | Chỉ được chọn khi **mọi relevant semantic fact cần accounting trong chunk đều đã được ontology hỗ trợ và xử lý hợp lệ**, và chunk có ít nhất 1 property hoặc edge mang `evidence.chunkIndex` trỏ đúng vào chunk này. Việc chỉ nhắc tên entity không đủ để coi là MAPPED. |
| **6. FAILED** | Chỉ dùng khi xảy ra lỗi trích xuất ngữ nghĩa thực sự khiến chunk không thể được xử lý. |

### Quy tắc bắt buộc

1. **Coverage xét toàn bộ semantic facts của chunk, không xét theo kiểu “map được ít nhất một fact là đủ”.**
   Ví dụ một chunk chứa:
   - `founding_date` → ontology hỗ trợ
   - `founder` → ontology chưa hỗ trợ
   - `facility_count` → ontology chưa hỗ trợ
   thì quyết định cuối cùng của chunk vẫn phải là:
   `UNSUPPORTED_BY_ONTOLOGY`
   Không được chọn `MAPPED` chỉ vì `founding_date` đã được map.

2. **Sau khi Schema Proposal được apply/rebase, phải đánh giá lại từng semantic fact độc lập.**
   Việc một property mới được bổ sung chỉ giải quyết đúng semantic fact mà property đó biểu diễn. Không được suy luận rằng toàn bộ schema gap của chunk đã được giải quyết.

3. **Không dùng generic properties để né Schema Gap.**
   Các field tổng quát như `description`, `notes` không được dùng để chứa một structured fact chỉ vì ontology đang thiếu property/relationship chuyên biệt tương ứng.
   Ví dụ:
   - thiếu `facility_count` → không nhét `"phát triển lên 6 cơ sở"` vào `organization.notes` để đánh MAPPED;
   - thiếu `founder/founded_by` → không nhét `"Phùng Thế Lịch là người sáng lập"` vào `person.description` để đánh MAPPED.
   Trong các trường hợp đó phải dùng `UNSUPPORTED_BY_ONTOLOGY`.

4. **MAPPED phải có mapping thật.**
   Nếu chọn `MAPPED`, phải tồn tại ít nhất một property hoặc edge có `evidence.chunkIndex` trỏ tới chính chunk đó. Nếu không có, phải trích xuất mapping hợp lệ hoặc phân loại lại thành `DUPLICATE_EVIDENCE`, `UNSUPPORTED_BY_ONTOLOGY`, `AMBIGUOUS` hoặc `NO_RELEVANT_FACT` theo đúng semantic của chunk.

5. **Không được đổi sang NO_RELEVANT_FACT để né validator.**
   Lịch sử, ngày/mốc thành lập, đổi tên, người sáng lập, quá trình phát triển, quy mô, thành tích, kỷ niệm và tiểu sử đều được coi là relevant knowledge nếu xuất hiện trong tài liệu.
---

## 5. Quy Trình Sửa Lỗi Batch (Repair Checklist 3 Bước)

Khi `submit_ingestion_batch` trả về lỗi (`success=false`, `terminal=false`):

1. **Bước 1 (Giữ nguyên nền tảng & Không xóa Node):** 
   - Gọi lại `get_ingestion_batch` và đọc `repairContext`.
   - Nếu có `repairContext.repairTemplate`, dùng nguyên template này làm payload khởi đầu; không tái dựng batch từ trí nhớ hoặc từ rejected payload gần nhất.
   - Giữ nguyên tuyệt đối mọi `tempId`, property value/evidence, edge/evidence và coverage có sẵn trong template.
2. **Bước 2 (Khắc phục lỗi cục bộ theo chẩn đoán):**
   - Lỗi `EVIDENCE_NOT_GROUNDED`: Giữ nguyên fact/value, chỉ rút ngắn `evidence.text` thành cụm từ nguyên văn xuất hiện chính xác trong chunk nguồn.
   - Lỗi `MAPPED_WITHOUT_MAPPING`: Bổ sung property/edge; nếu fact đã có thì dùng structured duplicate claim; nếu ontology thiếu thì dùng `UNSUPPORTED_BY_ONTOLOGY`.
   - Với `DUPLICATE_EVIDENCE`, dùng `factRef` trong `canonicalFactContext` hoặc local ref deterministic cho fact nằm trong payload hiện tại; mỗi claim phải có quote nguyên văn từ đúng chunk.
   - Nếu `retryRequired=false` hoặc `nextAction=request_coverage_review`, dừng repair và báo người dùng; không submit lại cùng nhãn.
3. **Bước 3 (Kiểm tra chéo và Submit):** Đảm bảo giữ đủ các properties cũ và mọi chunk `MAPPED` đều có fact bảo chứng. Sau đó gọi lại `submit_ingestion_batch`.

---

## 6. Xử Lý Lỗi Ontology & Schema Proposal

Khi gặp lỗi ontology (`UNKNOWN_ENTITY_TYPE`, `UNKNOWN_PROPERTY`, `UNKNOWN_RELATIONSHIP`):
1. **Kiểm tra mã chẩn đoán:**
   - `RELATIONSHIP_MAPPING_MISMATCH`: Sửa lại `edgeName` theo danh sách quan hệ tương thích, không tạo proposal.
   - `MISSING_SCOPE`: Nạp thêm scope bị thiếu qua `load_ontology_scopes` rồi submit lại.
   - `SCHEMA_GAP_CANDIDATE`: Gọi `create_schema_proposal` với `proposal_type` hợp lệ (`NEW_RELATIONSHIP`, `NEW_ENTITY_TYPE`, `NEW_PROPERTY`...).
2. **Dừng batch & Chờ phê duyệt từ người dùng:**
   - Agent **không tự approve** schema proposal. Chỉ sau trạng thái `APPROVED` mới được apply proposal và rebase ontology.
   - **Người dùng từ chối:** Gọi `review_schema_proposal(approved=false)` và dừng ingestion ở trạng thái block. Không đổi coverage label để commit phần còn lại.
   - **Người dùng đồng ý:** Gọi `review_schema_proposal(approved=true)` -> `apply_schema_proposal(version)` -> `rebase_ingestion` -> Tiếp tục xử lý batch với ontology mới.

---

## 7. Hoàn Tất (Finalize & Fill)
1. **Finalize:** Khi tất cả các batch đã `STAGED`, gọi `finalize_ingestion(ingestion_id)`.
2. **Fill (Persistence):** Chỉ gọi `fill_ingestion(ingestion_id)` khi người dùng yêu cầu lưu/nhập dữ liệu vào Knowledge Graph Neo4j.
3. **Báo cáo tổng kết:** Báo cáo `ingestionId`, phiên bản Ontology, số node, edges, batches và trạng thái commit (`readbackVerified`).
