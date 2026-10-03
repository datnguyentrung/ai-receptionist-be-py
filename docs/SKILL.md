---
description: |
  Nạp tài liệu vào Knowledge Graph theo từng mẻ, chọn phạm vi bản thể
  động, trích xuất tri thức theo semantic contract tối giản, phát hiện
  schema gap, điều phối phê duyệt thay đổi bản thể và chỉ ghi Neo4j khi
  được yêu cầu.
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
name: ingestion
---

# Ingestion với bản thể động

Skill này điều phối ingestion tài liệu vào Knowledge Graph.

## Kiến trúc cốt lõi

**Agent/LLM chỉ chịu trách nhiệm semantic intelligence:** - hiểu nội
dung chunk; - xác định fact; - chọn entity type, property hoặc
relationship phù hợp; - trích xuất semantic value; - chỉ ra chunk
nguồn; - phân loại coverage; - phát hiện tri thức không thể biểu diễn
bằng ontology hiện tại.

**Python/backend chịu trách nhiệm deterministic bookkeeping:** - chuẩn
hóa evidence thành verbatim source; - dựng natural identity; -
canonicalize entity; - sinh/quản lý local handle và stable entity key; -
gắn ontology version; - deduplicate; - validate grounding; - bảo vệ
validated baseline; - quản lý retry, repair và staging.

Không đẩy các nhiệm vụ bookkeeping tất định sang LLM.

# 1. Quy tắc bất biến

-   `scope_hint` chỉ là gợi ý; không coi là lựa chọn cuối cùng.
-   Không mặc định scope và không hiểu `core` là toàn bộ ontology.
-   Mỗi batch có thể chọn một hoặc nhiều scope.
-   Chỉ sử dụng ontology version đã ghim cho ingestion hiện tại.
-   Không tạo hoặc sử dụng schema mới trước khi người dùng phê duyệt.
-   Không gọi `fill_ingestion` nếu người dùng chưa yêu cầu
    ghi/lưu/import rõ ràng.
-   Không gọi `review_schema_proposal`, `apply_schema_proposal`, xóa
    hoặc rollback nếu người dùng chưa quyết định tường minh.
-   Chỉ chuyển batch sau khi batch hiện tại đã `STAGED` hoặc workflow
    chủ động chuyển sang trạng thái chờ phê duyệt.
-   Kết quả tool là nguồn sự thật của workflow.
-   Nếu `terminal=true`, hoặc `nextAction` là
    `explicit_extraction_failure` / `report_tool_failure`, dừng
    ingestion ngay.
-   Conversation history không phải bộ nhớ ingestion. Workspace Python
    là nguồn state.
-   Agent không tự viết SQL/Cypher.
-   Agent không tự thay đổi workflow state.
-   Agent không tự tạo identity hoặc stable key.
-   Agent không phải duy trì cùng một `tempId` qua repair.
-   Agent không phải copy evidence nguyên văn tuyệt đối.
-   Agent không phải tự điền `ontologyVersion` vào semantic draft.

# 2. Semantic Extraction Contract

Sau khi nhận chunks và ontology projection, Agent chỉ trả lời:

1.  Chunk đang nói những fact nào?
2.  Fact thuộc entity nào?
3.  Ontology đã load có property/relationship phù hợp không?
4.  Nếu có, semantic value là gì và đến từ chunk nào?
5.  Nếu không biểu diễn được, coverage phù hợp là gì?

## 2.1 Node

Khi fact mô tả thuộc tính của entity: - chọn `className` đúng
ontology; - xác định entity bằng giá trị semantic trong nguồn; - điền
property phù hợp; - mỗi property chỉ cần semantic value và chunk
nguồn; - không tự dựng `identity`; - không cần giữ `tempId` ổn định.

Python dùng `identityStrategy.required` để dựng natural identity và
canonical entity key.

## 2.2 Property

Mỗi property phải: - tồn tại trong ontology projection; - thuộc đúng
entity type; - có giá trị được source hỗ trợ; - có `chunkIndex` trỏ tới
chunk chứa fact.

Nếu tool contract còn có `evidence.text`, Agent có thể bỏ trống nếu
schema cho phép hoặc cung cấp cụm từ/câu ngắn làm evidence hint.

Agent không phải bảo đảm evidence hint khớp verbatim từng ký tự. Python
Evidence Resolver căn chỉnh về source verbatim trước validation/staging.

## 2.3 Relationship

Chỉ tạo relationship khi: - relationship tồn tại trong ontology đã
load; - source/target phù hợp domain/range; - quan hệ thực sự được
source hỗ trợ; - có `chunkIndex` chứng minh relationship.

Nếu endpoint đã tồn tại trong canonical graph context, dùng reference do
tool cung cấp. Không phát lại entity cũ chỉ để tạo edge. Không tạo
entity không có căn cứ từ source chỉ để đủ endpoint.

## 2.4 Evidence

Agent cung cấp **semantic provenance**, không làm string bookkeeping.

Thông tin tối thiểu: - `chunkIndex`; - semantic value/fact; - optional
evidence hint nếu schema/tool yêu cầu.

Python chịu trách nhiệm: 1. tìm source trong `chunk.text`; 2. xét cả
`chunk.section` khi cần; 3. xử lý khác biệt whitespace/newline/Markdown;
4. align hint hoặc semantic value về source; 5. lấy lại đoạn verbatim từ
source canonical; 6. chỉ báo `EVIDENCE_NOT_GROUNDED` nếu fact thực sự
không được chunk hỗ trợ.

Không sửa semantic value chỉ để evidence pass.

# 3. Coverage Contract

Mỗi chunk có đúng một coverage decision.

## `MAPPED`

Chunk đóng góp ít nhất một **knowledge fact thực sự** dưới dạng: -
property fact; hoặc - relationship edge.

`node.evidence` hoặc việc chunk chỉ nhắc tên entity không đủ chứng minh
`MAPPED`.

## `DUPLICATE_EVIDENCE`

Chunk chứa fact liên quan nhưng fact đã tồn tại trong canonical/staged
knowledge và chunk hiện tại không tạo fact mới.

Không dùng để né schema gap. Backend phải kiểm tra fact trùng có thực sự
tồn tại.

## `NO_RELEVANT_FACT`

Chunk thuộc tài liệu/domain nhưng không chứa knowledge fact cần đưa vào
graph, ví dụ câu dẫn, câu chuyển ý hoặc mô tả không tạo tri thức cấu
trúc.

## `NOT_RELEVANT`

Chunk không thuộc phạm vi tri thức cần ingestion.

Không dùng `NOT_RELEVANT` để che việc ontology thiếu khả năng biểu diễn.

## `UNSUPPORTED_BY_ONTOLOGY`

Chunk chứa knowledge fact liên quan nhưng ontology version đã load không
có entity/property/relationship phù hợp.

Đây là tín hiệu semantic cho workflow schema-gap.

## `AMBIGUOUS`

Source không đủ rõ để xác định semantic mapping đáng tin cậy. Không tự
suy đoán để biến thành `MAPPED`.

## `FAILED`

Chỉ dùng khi Agent không thể xử lý chunk vì lỗi extraction thực sự.
Không dùng thay `UNSUPPORTED_BY_ONTOLOGY`.

## 3.1 Quy tắc coverage

-   Không đánh `MAPPED` chỉ vì Agent hiểu chunk.
-   Không đánh `MAPPED` chỉ vì chunk nhắc entity đã biết.
-   `MAPPED` phải có property hoặc relationship fact từ chính chunk.
-   Nếu chunk vừa có fact map được vừa có fact quan trọng không thể biểu
    diễn, không âm thầm coi toàn chunk đã hoàn tất.
-   Không tạo property/edge giả để vượt coverage validator.
-   Không nhét historical fact/event/achievement vào `description` hoặc
    `notes` chỉ để tránh schema gap nếu làm sai semantic meaning.
-   Nếu ontology không có cấu trúc phù hợp, dùng
    `UNSUPPORTED_BY_ONTOLOGY`.

# 4. Workflow bắt buộc cho mỗi batch

Với đúng `nextBatch.batchIndex`:

1.  `get_ingestion_batch`
2.  `list_ontology_scopes` nếu cần xác định scope.
3.  Chọn tập scope nhỏ nhất nhưng đủ bao phủ batch.
4.  `load_ontology_scopes`
5.  Agent tạo semantic fragment trực tiếp từ chunks + ontology
    projection.
6.  `submit_ingestion_batch`
7.  Đọc kết quả tool và làm đúng `nextAction`.

Sau `load_ontology_scopes`, thao tác semantic tiếp theo là
`submit_ingestion_batch`.

Không gọi extraction sub-agent hoặc LLM trung gian khác.

# 5. Bắt đầu ingestion

1.  Gọi `begin_ingestion(artifact_name, document_key?, scope_hint?)`.
2.  Lưu `ingestionId` và ontology/workspace metadata tool trả về.
3.  Python tự preparation: kiểm tra file, parse, dedup, clean, validate
    chất lượng, chunking, batching.
4.  Không tải toàn bộ ontology ở bước begin.
5.  Workspace ingestion do Python quản lý.
6.  Nếu tool trả workspace có thể reuse, tiếp tục từ `nextBatch`.

# 6. Chọn ontology scope

Sau `get_ingestion_batch`: 1. Gọi `list_ontology_scopes(ingestion_id)`
nếu cần. 2. Đọc `scopeKey`, `description`, `summary`, `schemaHash`. 3.
Xét toàn bộ nội dung batch. 4. Chọn hợp scope nhỏ nhất nhưng đủ. 5. Gọi
`load_ontology_scopes(ingestion_id, scope_keys)`.

Không suy đoán scope ngoài catalog.

Nếu tool báo snapshot không tồn tại, hash lỗi, merge conflict hoặc
ontology version mismatch thì dừng batch và báo lỗi ontology data.

# 7. Submit semantic fragment

Agent tập trung vào:

``` text
source fact
    ↓
entity type
    ↓
property / relationship
    ↓
semantic value
    ↓
chunkIndex
    ↓
coverage
```

Agent không tập trung vào:

``` text
ontologyVersion
tempId stability
stable_entity_key
exact evidence substring
canonical whitespace
dedup keys
repair baseline
staging mechanics
```

Gọi
`submit_ingestion_batch(ingestion_id, batch_index, scope_keys, graph_fragment)`.

Python chịu trách nhiệm canonicalization, evidence resolution, identity
resolution, validation, repair guard và staging.

Chỉ khi tool xác nhận `STAGED` mới chuyển batch.

# 8. Repair batch

Repair không có nghĩa là tái tạo chính xác JSON lần submit trước.

Mục tiêu: **sửa semantic mapping bị lỗi nhưng bảo toàn knowledge đã được
backend xác nhận hợp lệ.**

Không coi failed fragment gần nhất là baseline tuyệt đối.

## 8.1 Quy trình repair

1.  Không gọi lại `begin_ingestion`.
2.  Không chuyển batch.
3.  Đọc checkpoint: `batchIndex`, `scopeKeys`, `affectedChunkIndexes`,
    `errors`.
4.  Gọi lại `get_ingestion_batch`.
5.  Load lại đúng scope cần thiết.
6.  Nếu backend trả `repairContext.repairTemplate`, copy nguyên template và
    chỉ patch các vị trí trong `validationIssues`; không dựng lại từ trí nhớ.
7.  Submit lại cùng batch.

Agent không cần nhớ raw fragment cũ từ conversation history.

## 8.2 Repair evidence

Nếu lỗi là `EVIDENCE_NOT_GROUNDED`: - không đổi semantic fact nếu fact
vẫn đúng; - giữ entity/property/relationship/value; - giữ đúng
`chunkIndex`; - evidence hint có thể rút ngắn; - để Python Evidence
Resolver căn chỉnh verbatim.

Không viết lại toàn graph chỉ vì formatting evidence.

## 8.3 Repair coverage

Nếu lỗi `MAPPED_WITHOUT_MAPPING`:

-   Có fact biểu diễn được → tạo property/relationship đúng ontology.
-   Fact đã tồn tại → `DUPLICATE_EVIDENCE` với `factRef` và grounded quote.
-   Chỉ structural boilerplate được chứng minh deterministic → `NO_RELEVANT_FACT`.
-   Ontology không biểu diễn được → `UNSUPPORTED_BY_ONTOLOGY`.
-   Source mơ hồ → `AMBIGUOUS`.

Không tạo description/node/edge giả chỉ để giữ `MAPPED`.

## 8.4 Bảo toàn knowledge

Bảo toàn theo: - semantic entity identity; - property name + value; -
relationship semantics;

không theo chuỗi `tempId`.

Nếu tool contract vẫn expose local handle, LLM có thể đặt khác giữa các
lần repair. Backend canonicalize về cùng natural identity/stable key.

# 9. Xử lý lỗi ontology

`UNKNOWN_ENTITY_TYPE`, `UNKNOWN_PROPERTY`, `UNKNOWN_RELATIONSHIP` hoặc
domain/range mismatch không đồng nghĩa ngay với schema gap.

Ưu tiên diagnosis tool:

-   `RELATIONSHIP_MAPPING_MISMATCH`: ontology đã có relationship tương
    thích → sửa mapping, không proposal.
-   `MISSING_SCOPE`: concept tồn tại ở scope khác cùng ontology version
    → load scope phù hợp rồi remap.
-   `SCHEMA_GAP_CANDIDATE`: toàn ontology version không có cấu trúc
    tương thích → đánh giá proposal.

Không hard-code theo tên relationship/domain cụ thể.

# 10. Unsupported ontology → Schema proposal

Khi semantic extraction trả `UNSUPPORTED_BY_ONTOLOGY`, backend/tool phải
xác minh schema gap thật sự:

1.  kiểm tra scope hiện tại;
2.  kiểm tra scope khác trong ontology version đã ghim;
3.  nếu có cấu trúc tương thích → load đúng scope và remap;
4.  chỉ khi toàn ontology không có cấu trúc phù hợp mới proposal.

Ưu tiên mở rộng scope hiện có. Chỉ tạo `NEW_SCOPE` khi concept thuộc
miền riêng biệt.

Proposal type hợp lệ: - `NEW_RELATIONSHIP` - `MODIFY_RELATIONSHIP` -
`NEW_ENTITY_TYPE` - `NEW_PROPERTY` - `MODIFY_ENTITY_TYPE` -
`MODIFY_PROPERTY` - `NEW_ALIAS` - `NEW_SCOPE` - `MODIFY_SCOPE`

Khi gọi `create_schema_proposal`, cung cấp: - `proposal_type`; -
`technical_name`; - payload cấu trúc; - reason; - evidence/source
chunk; - affected scope keys.

Không tự tạo proposal type ngoài enum.

Sau khi tạo proposal: - dừng batch ở `awaiting_schema_approval`; - không
chuyển batch; - không tự approve.

# 11. Người dùng phê duyệt ontology change

1.  Gọi `get_schema_proposal`.
2.  Trình bày proposal và evidence.
3.  Chờ quyết định tường minh.

Nếu từ chối: - `review_schema_proposal(..., approved=false, ...)`; -
không tự thay đổi ontology; - làm theo workflow tool trả về.

Nếu đồng ý: 1. `review_schema_proposal(..., approved=true, ...)`; 2. chỉ
khi `APPROVED` mới `apply_schema_proposal`; 3. Python tạo ontology
version mới và compile snapshot; 4. gọi `rebase_ingestion`.

Không âm thầm đổi ontology version giữa ingestion.

Sau rebase, đọc `nextBatch`, xử lý lại batch bị invalidated và tái sử
dụng canonical knowledge còn hợp lệ theo tool result.

# 12. Finalize

Khi mọi batch đã `STAGED`, gọi `finalize_ingestion`.

Finalize kiểm tra tối thiểu: - coverage completeness và semantic proof; -
grounding; - unresolved schema gaps; - failed/ambiguous chunks theo policy;
- repair baseline preservation; - scalar conflicts theo `multiValue`; - batch
consistency; - canonical graph integrity.

Nếu phát hiện batch không hợp lệ: - không ép READY; - mở lại đúng batch
cần repair/schema-gap; - làm theo `nextAction`.

# 13. Fill Neo4j

Khi `ready_to_fill`:

Nếu người dùng chỉ yêu cầu extract/validate: - dừng; - nói rõ Neo4j chưa
thay đổi.

Nếu người dùng đã yêu cầu lưu/import: - gọi `fill_ingestion`.

`fill_ingestion` mới thực hiện embedding, persistence Neo4j, readback
verification và commit workspace state.

Chỉ báo thành công khi `readbackVerified=true`.

# 14. Terminal behavior

Nếu tool trả `terminal=true`, `nextAction=explicit_extraction_failure`
hoặc `nextAction=report_tool_failure`: - dừng ngay; - không retry
thêm; - không chuyển batch; - không finalize; - không fill; - báo
ingestion ID, batch, errors và persistence state.

# 15. Báo cáo cuối

Báo cáo tối thiểu: - `ingestionId`; - ontology version; - số batch
`STAGED`; - số batch bị chặn; - schema proposal nếu có; - quyết định
người dùng nếu có; - trạng thái finalize; - trạng thái Neo4j commit; -
số node; - số edge; - số chunk; - số fact; - `readbackVerified`.

Không gọi dữ liệu staged/tạm là đã ghi chính thức.

# 16. Nguyên tắc thiết kế cuối cùng

``` text
LLM / Agent
───────────
Semantic understanding
Fact extraction
Ontology mapping
Coverage classification
Schema-gap recognition

Python / Backend
────────────────
Evidence resolution
Natural identity
Stable entity keys
Local handles / temp refs
Ontology version
Canonicalization
Deduplication
Grounding validation
Repair baseline
Retry state
Staging
Persistence
```

Nếu một thao tác có thể thực hiện chính xác, lặp lại và tất định bằng
Python, không yêu cầu LLM thực hiện thao tác đó.
