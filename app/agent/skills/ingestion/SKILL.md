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

# Ingestion với bản thể động

Skill này là nguồn điều khiển thứ tự nghiệp vụ của toàn bộ quá trình ingestion tài liệu vào Knowledge Graph.

## Kiến trúc cốt lõi

**Agent/LLM chỉ chịu trách nhiệm semantic intelligence:**
- Hiểu nội dung ngữ nghĩa từng chunk;
- Xác định các knowledge facts (thuộc tính, sự kiện, mối quan hệ);
- Chọn entity type, property hoặc relationship phù hợp trong ontology đã nạp;
- Trích xuất semantic value và chỉ ra chunk nguồn (provenance);
- Phân loại độ phủ (Coverage decision) cho từng chunk;
- Phát hiện tri thức mới không thể biểu diễn bằng ontology hiện tại (Schema Gap).

**Python/Backend chịu trách nhiệm deterministic bookkeeping:**
- Chuẩn hóa evidence thành văn bản nguồn nguyên vẹn (verbatim canonical text);
- Dựng natural identity dựa trên `identityStrategy.required` của ontology;
- Canonicalize entity, sinh/quản lý local handle và stable entity key (`ref`);
- Quản lý phiên bản ontology, deduplicate, validate grounding;
- Bảo vệ baseline đã hợp lệ (repair guard), chống mất mát dữ liệu;
- Quản lý trạng thái workspace (RAM in-memory), staging, và persistence vào Neo4j.

> **Nguyên tắc vàng:** Tuyệt đối không đẩy các nhiệm vụ bookkeeping tất định sang LLM. Nếu một thao tác có thể thực hiện chính xác, lặp lại và tất định bằng Python, hệ thống sẽ do Python đảm nhiệm.

---

# 1. Quy tắc bất biến

1. `scope_hint` chỉ là gợi ý ban đầu; không coi là lựa chọn duy nhất hoặc cuối cùng.
2. Không mặc định phạm vi và không hiểu `core` là toàn bộ ontology.
3. Mỗi batch có thể chọn một hoặc nhiều scope (`scope_keys`).
4. Chỉ sử dụng bản chụp lược đồ thuộc `ontologyVersionId` đã ghim cho phiên ingestion hiện tại.
5. Không tạo hoặc sử dụng schema mới trước khi người dùng phê duyệt tường minh.
6. Không gọi `fill_ingestion` nếu người dùng chưa yêu cầu ghi/lưu/import tri thức vào cơ sở dữ liệu.
7. Không gọi `review_schema_proposal`, `apply_schema_proposal`, xóa hoặc rollback nếu người dùng chưa quyết định tường minh.
8. Chỉ chuyển sang batch tiếp theo sau khi batch hiện tại đã chuyển trạng thái `STAGED` hoặc workflow chủ động dừng chờ phê duyệt proposal.
9. Kết quả trả về từ Tool là nguồn sự thật (single source of truth) của workflow.
10. Nếu tool trả `terminal=true`, hoặc `nextAction` là `explicit_extraction_failure` / `report_tool_failure`, dừng ingestion ngay lập tức và báo cáo lỗi; không cố retry thêm.
11. Conversation history không phải bộ nhớ ingestion. Workspace Python (RAM) là nguồn state tin cậy.
12. Agent không tự viết SQL/Cypher, không tự tạo `identity` hoặc stable key, không tự gắn `ontologyVersion` vào semantic draft.
13. Agent không cần phải duy trì cùng một `tempId` cố định qua các lần sửa lỗi (repair).
14. Agent không cần copy nguyên văn tuyệt đối từng khoảng trắng trong `evidence.text`; Python Evidence Resolver sẽ tự động căn chỉnh (alignment) với văn bản nguồn verbatim.

---

# 2. Semantic Extraction Contract

Sau khi nhận chunks từ `get_ingestion_batch` và ontology projection từ `load_ontology_scopes`, Agent trả lời các câu hỏi ngữ nghĩa:
1. Chunk đang nói những fact nào?
2. Fact thuộc entity nào?
3. Ontology đã load có property/relationship phù hợp không?
4. Nếu có, semantic value là gì và đến từ chunk nào (`chunkIndex`)?
5. Nếu không biểu diễn được hoặc chunk là bối cảnh chung, coverage tương ứng là gì?

## 2.1 Node
- Chọn `className` đúng với ontology projection.
- Điền các thuộc tính (properties) phù hợp.
- **Không tự tạo trường `identity`**: Python sẽ tự động trích xuất các trường định danh dựa trên `identityStrategy.required` để sinh natural identity và canonical key.
- `tempId`: Chuỗi định danh tạm thời trong nội bộ batch (ví dụ: `node_1`, `coach_kim`). Không cần giữ cố định chuỗi này giữa các lần repair.

## 2.2 Property
Mỗi property phải:
- Tồn tại trong ontology projection đã nạp;
- Thuộc đúng entity type;
- Mang giá trị ngữ nghĩa (semantic value) có căn cứ từ văn bản;
- Có `chunkIndex` trỏ tới chunk chứa fact đó.

Về trường `evidence.text`: Agent chỉ cần cung cấp cụm từ khóa hoặc câu ngắn làm gợi ý (evidence hint). Python Evidence Resolver sẽ tự động đối chiếu và lấy chính xác câu nguyên văn từ `chunks[*].text`.

## 2.3 Relationship
Chỉ tạo relationship khi:
- Tên quan hệ (`edgeName`) tồn tại trong ontology đã load;
- Source/Target phù hợp với domain và range của quan hệ;
- Quan hệ thực sự được văn bản nguồn hỗ trợ và có `chunkIndex` chứng minh.

**Tham chiếu thực thể xuyên batch (`ref`):**
Nếu một đầu mút (source/target) đã tồn tại trong `canonicalGraphContext` (đã `STAGED` ở batch trước), sử dụng reference dạng `entity:<stable-key>` (ví dụ: `entity:person:kim_chul_soo`) làm `sourceTempId` hoặc `targetTempId`. Không phát lại node cũ chỉ để tạo edge.

## 2.4 Evidence
Agent cung cấp **semantic provenance**:
- `chunkIndex`: Số thứ tự chunk chứa dữ liệu (bắt buộc, 0-indexed);
- `text`: Cụm từ/câu gợi ý (evidence hint).

Python chịu trách nhiệm tìm kiếm trong `chunk.text`, giải quyết sai khác khoảng trắng/xuống dòng/Markdown, căn chỉnh về source verbatim và kiểm tra tính xác thực (grounding validation).

---

# 3. Coverage Contract

Mỗi chunk trong batch **bắt buộc** phải có đúng một mục phân loại `coverage` (`ChunkCoverage`):

| Quyết định (`decision`) | Ý nghĩa & Điều kiện sử dụng |
| :--- | :--- |
| `MAPPED` | Chunk đóng góp ít nhất một **knowledge fact thực sự** (ít nhất 1 property fact hoặc 1 relationship edge). *Lưu ý: Không được đánh `MAPPED` nếu không có bất kỳ property hay edge nào trỏ tới chunk đó (tránh lỗi `MAPPED_WITHOUT_MAPPING`).* |
| `DUPLICATE_EVIDENCE` | Chunk chứa fact liên quan nhưng fact này đã được trích xuất ở các batch trước/canonical context và chunk hiện tại không tạo ra fact mới. |
| `NO_RELEVANT_FACT` | Chunk thuộc tài liệu/domain nhưng chỉ là câu dẫn, lời mở đầu, câu chuyển ý hoặc mô tả chung không tạo tri thức cấu trúc. |
| `NOT_RELEVANT` | Chunk nằm ngoài phạm vi tài liệu cần ingestion. |
| `UNSUPPORTED_BY_ONTOLOGY` *(hoặc `SCHEMA_GAP`)* | Chunk chứa tri thức quan trọng liên quan đến miền dữ liệu nhưng ontology hiện tại thiếu entity/property/relationship để biểu diễn. Bắt buộc nêu rõ lý do trong `reason`. |
| `AMBIGUOUS` | Nội dung nguồn quá mơ hồ, không đủ dữ liệu để ánh xạ chắc chắn. Tuyệt đối không suy đoán vô căn cứ. |
| `FAILED` | Lỗi trích xuất ngữ nghĩa thực sự từ phía LLM khiến không thể xử lý chunk. |

### Quy tắc bất biến về Coverage:
- Không đánh `MAPPED` chỉ vì Agent "hiểu" chunk hoặc chunk chỉ nhắc tên một entity đã biết.
- Không tạo property/edge giả chỉ để vượt qua bộ kiểm tra coverage.
- Không nhét các sự kiện, thành tích, lịch sử vào trường `description` hoặc `notes` một cách gượng ép nếu ontology thiếu quan hệ/thực thể chuyên biệt; thay vào đó, hãy dùng `UNSUPPORTED_BY_ONTOLOGY` để mở đề xuất schema.

---

# 4. Quy trình xử lý bắt buộc cho mỗi Batch

Với mỗi `nextBatch.batchIndex`:

```
┌─────────────────────────┐
│ 1. get_ingestion_batch  │ ──> Lấy danh sách chunks và canonical context
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 2. list_ontology_scopes │ ──> Liệt kê danh mục scope gọn nhẹ (nếu cần)
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 3. load_ontology_scopes │ ──> Tải compiled snapshot của tập scope nhỏ nhất đủ bao phủ
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 4. Trích xuất Semantic  │ ──> LLM tạo trực tiếp SemanticGraphPatchFragment
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│5. submit_ingestion_batch│ ──> Gửi fragment để Python validate, guard và stage
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 6. Đọc kết quả / Action │ ──> STAGED (qua batch mới) hoặc REPAIR / SCHEMA_PROPOSAL
└─────────────────────────┘
```

> **Tuyệt đối không gọi extraction sub-agent hoặc LLM trung gian khác sau bước `load_ontology_scopes`.**

---

# 5. Khởi tạo Ingestion (`begin_ingestion`)

1. Gọi `begin_ingestion(artifact_name, document_key?, scope_hint?)`.
2. Lưu `ingestionId` và metadata trả về.
3. Python tự động thực hiện: kiểm tra file, parse, deduplicate, clean, chia chunk và gom batch.
4. Trạng thái workspace/batches/chunks được lưu trữ in-memory (RAM) trong `ServiceContainer`. PostgreSQL lưu trữ ontology version và schema proposals. Neo4j lưu Knowledge Graph hoàn chỉnh sau khi fill.
5. Nếu tool trả về workspace có thể tái sử dụng (in-process reuse), tiếp tục từ `nextBatch.batchIndex`.

---

# 6. Chọn Ontology Scope

1. Sau `get_ingestion_batch`, gọi `list_ontology_scopes(ingestion_id)` nếu chưa rõ các scope có sẵn.
2. Đọc `scopeKey`, `description`, `summary`, `schemaHash`.
3. Xét nội dung toàn bộ batch, chọn hợp của các scope nhỏ nhất nhưng đủ bao phủ các khái niệm trong batch.
4. Gọi `load_ontology_scopes(ingestion_id, scope_keys)`.
5. Nếu tool báo lỗi snapshot, sai hash, merge conflict hoặc sai ontology version: dừng batch và báo lỗi ontology data.

---

# 7. Gửi Semantic Fragment (`submit_ingestion_batch`)

Gọi `submit_ingestion_batch(ingestion_id, batch_index, scope_keys, graph_fragment)`.

Python sẽ tự động:
- Canonicalize tên kỹ thuật;
- Dựng natural identity và stable key;
- Căn chỉnh evidence về văn bản nguồn (Evidence Resolver);
- Kiểm tra tính hợp lệ với ontology và ràng buộc dữ liệu;
- Kiểm tra Repair Guard (đảm bảo không làm mất fact hợp lệ);
- Chuyển trạng thái batch thành `STAGED`.

Chỉ khi tool trả về trạng thái `STAGED` mới chuyển sang batch tiếp theo.

---

# 8. Quy trình Sửa lỗi Batch (Repair Workflow)

Khi `submit_ingestion_batch` trả về lỗi kiểm tra (`success=false`), hệ thống sẽ trả về danh sách `errors` chi tiết và `affectedChunkIndexes`.

### 8.1 Các bước Repair:
1. **Không gọi lại `begin_ingestion`** và không chuyển sang batch khác.
2. Đọc kỹ checkpoint: `batchIndex`, `scopeKeys`, `affectedChunkIndexes`, `errors`.
3. Gọi lại `get_ingestion_batch(ingestion_id, batchIndex)`.
4. Gọi `load_ontology_scopes` với đúng các scope cần thiết.
5. **Tạo lại Semantic Fragment hoàn chỉnh**, khắc phục các lỗi được chỉ ra.
6. Submit lại với `submit_ingestion_batch`.

### 8.2 Nguyên tắc bảo toàn Knowledge (Repair Guard):
- **BẢO TỒN CÁC FACT ĐÃ HỢP LỆ**: Giữ lại toàn bộ các node, property facts và edges đã được trích xuất chính xác ở lần trước nếu chúng không vi phạm lỗi (tránh kích hoạt lỗi `REPAIR_DROPPED_VALID_FACT`).
- **Sửa lỗi trích dẫn (`EVIDENCE_NOT_GROUNDED`)**:
  - Giữ nguyên entity, property, value và `chunkIndex`.
  - Rút ngắn hoặc chỉnh lại `evidence.text` thành cụm từ/từ khóa có mặt trực tiếp trong chunk nguồn để Evidence Resolver tự căn chỉnh.
- **Sửa lỗi độ phủ (`MAPPED_WITHOUT_MAPPING`)**:
  - Nếu chunk có chứa fact: bổ sung property/edge có `chunkIndex` trỏ tới chunk đó.
  - Nếu chunk không tạo fact mới: chuyển coverage sang `DUPLICATE_EVIDENCE` hoặc `NO_RELEVANT_FACT`.
  - Nếu ontology thiếu khả năng biểu diễn: chuyển sang `UNSUPPORTED_BY_ONTOLOGY` (hoặc `SCHEMA_GAP`).

---

# 9. Xử lý Lỗi Ontology & Đề xuất Schema Proposal

Khi gặp lỗi liên quan đến ontology (`UNKNOWN_ENTITY_TYPE`, `UNKNOWN_PROPERTY`, `UNKNOWN_RELATIONSHIP`), kiểm tra mã chẩn đoán từ tool:

1. `RELATIONSHIP_MAPPING_MISMATCH`: Ontology hiện tại đã có quan hệ tương thích giữa cặp thực thể → Sửa lại `edgeName` theo `candidateRelationships`, không tạo proposal.
2. `MISSING_SCOPE`: Khái niệm nằm ở scope khác trong cùng ontology version → Nạp thêm scope đó qua `load_ontology_scopes` rồi submit lại.
3. `SCHEMA_GAP_CANDIDATE`: Toàn bộ ontology hiện tại không có cấu trúc phù hợp → Tiến hành tạo Schema Proposal.

### 9.1 Tạo đề xuất (`create_schema_proposal`):
Gọi `create_schema_proposal` với các tham số chuẩn:
- `proposal_type`: Bắt buộc chọn đúng 1 trong các giá trị enum sau:
  - `NEW_RELATIONSHIP`: Thêm quan hệ mới giữa hai loại thực thể.
    - Payload mẫu: `{"technicalName": "has_policy", "sourceEntityType": "organization", "targetEntityType": "policy", "displayName": "Có chính sách", "cardinality": "MANY_TO_MANY", "description": "Quan hệ chính sách từ tổ chức"}`
  - `NEW_ENTITY_TYPE`: Thêm loại thực thể mới.
    - Payload mẫu: `{"technicalName": "event", "displayName": "Sự kiện", "identityFields": ["name"]}`
  - `NEW_PROPERTY`: Thêm thuộc tính mới.
    - Payload mẫu: `{"entityType": "class_program", "technicalName": "tuition", "dataType": "FLOAT", "displayName": "Học phí", "required": false}`
  - `MODIFY_ENTITY_TYPE` / `MODIFY_PROPERTY` / `MODIFY_RELATIONSHIP`
  - `NEW_ALIAS` / `NEW_SCOPE` / `MODIFY_SCOPE`
- `technical_name`: Tên kỹ thuật của đối tượng (ví dụ: `has_policy`, `tuition`).
- `reason`: Lý do chi tiết từ tài liệu nguồn.
- `evidence`: Bằng chứng nguồn (ví dụ: `{"chunkIndex": 5, "quote": "..."}`).
- `affected_scope_keys`: Danh sách scope bị ảnh hưởng (ví dụ: `["core", "training"]`).

**Sau khi gọi proposal:** Dừng batch ở trạng thái `awaiting_schema_approval`. Tuyệt đối không tự phê duyệt proposal.

---

# 10. Phê duyệt & Áp dụng Thay đổi Bản thể

1. Dùng `get_schema_proposal` để lấy thông tin chi tiết, sau đó trình bày cho người dùng kèm bằng chứng và đề xuất.
2. Chờ quyết định tường minh từ người dùng:
   - **Người dùng từ chối**:
     - Gọi `review_schema_proposal(..., approved=false, ...)`.
     - Trích xuất lại theo ontology cũ (đánh dấu chunk liên quan là `UNSUPPORTED_BY_ONTOLOGY` hoặc `NOT_RELEVANT`).
   - **Người dùng đồng ý**:
     - Gọi `review_schema_proposal(..., approved=true, ...)`.
     - Gọi `apply_schema_proposal` với mã phiên bản mới (ví dụ: `v1.1.0`).
     - Python tự động tạo ontology version mới, biên dịch snapshot và kích hoạt phiên bản.
     - Gọi `rebase_ingestion` để cập nhật lại phiên ingestion sang ontology version mới.
     - Tiếp tục xử lý batch theo hướng dẫn của `nextBatch` trả về từ `rebase_ingestion`.

---

# 11. Finalize & Ghi Dữ liệu vào Neo4j (`fill_ingestion`)

1. **Finalize**: Khi toàn bộ các batch đã `STAGED`, gọi `finalize_ingestion(ingestion_id)`.
   - Tool sẽ kiểm tra toàn diện: độ phủ (coverage completeness), grounding, tính toàn vẹn của đồ thị (canonical integrity).
   - Nếu còn batch lỗi hoặc proposal chưa duyệt: quay lại xử lý đúng batch đó, không ép finalize.
2. **Fill (Persistence)**:
   - Khi trạng thái là `ready_to_fill`:
     - Nếu người dùng chỉ yêu cầu trích xuất/kiểm thử: Dừng lại và thông báo rõ dữ liệu chưa ghi vào Neo4j.
     - Nếu người dùng đã yêu cầu lưu/ghi/import: Gọi `fill_ingestion(ingestion_id)`.
   - `fill_ingestion` sẽ:
     - Tạo embedding cho các thực thể;
     - Ghi các Nodes, Properties, Edges bền vững vào Neo4j;
     - Đọc lại (read-back verification) để kiểm tra tính toàn vẹn;
     - Cập nhật trạng thái workspace thành `COMMITTED`.
3. Chỉ báo thành công khi tool trả về `readbackVerified=true`.

---

# 12. Báo cáo Kết quả Cuối cùng

Báo cáo tóm tắt bắt buộc gồm các thông tin:
- `ingestionId`;
- Phiên bản Ontology đã sử dụng;
- Số batch đã `STAGED` thành công;
- Số batch bị chặn / gặp lỗi (nếu có);
- Các Schema Proposals và quyết định phê duyệt của người dùng (nếu có);
- Trạng thái Finalize & Trạng thái ghi Neo4j (`COMMITTED`);
- Tổng kết số lượng: Nodes, Edges, Chunks, Property facts;
- Kết quả kiểm chứng đọc lại (`readbackVerified`).

> **Tuyệt đối không gọi dữ liệu đang ở trạng thái Staged (tạm thời) là đã ghi vào cơ sở dữ liệu chính thức.**

---

# 13. Sơ đồ Phân chia Trách nhiệm

``` text
┌────────────────────────────────────────────────────────┐
│                      LLM / AGENT                       │
├────────────────────────────────────────────────────────┤
│ • Semantic Understanding & Chunk Analysis              │
│ • Knowledge Fact Extraction (Nodes, Properties, Edges) │
│ • Dynamic Ontology Scope Selection                     │
│ • Chunk Coverage Classification                        │
│ • Schema Gap Detection & Proposal Authoring            │
└───────────────────────────┬────────────────────────────┘
                            │ (SemanticGraphPatchFragment)
                            ▼
┌────────────────────────────────────────────────────────┐
│                   PYTHON / BACKEND                     │
├────────────────────────────────────────────────────────┤
│ • Ingestion Workspace & Batch Management (RAM)         │
│ • Verbatim Evidence Resolution & Alignment             │
│ • Natural Identity & Stable Entity Key Generation      │
│ • Canonicalization, Deduplication & Grounding Guard    │
│ • Repair Guard (Preserving Valid Baseline Facts)       │
│ • Ontology Versioning & Snapshot Compilation (Postgres)│
│ • Vector Embedding & Persistent Commit (Neo4j)         │
│ • Readback Verification                                │
└────────────────────────────────────────────────────────┘
```
