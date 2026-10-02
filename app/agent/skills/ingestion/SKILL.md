---
name: ingestion
description: >
  Nạp tài liệu vào Knowledge Graph theo từng mẻ, chọn phạm vi bản thể động, giữ bằng chứng nguồn,
  và điều phối phê duyệt thay đổi bản thể trước khi ghi Neo4j.
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

Skill này là nguồn điều khiển thứ tự nghiệp vụ của toàn bộ quá trình ingestion.

Các công cụ Python chỉ thực hiện từng thao tác xác định, có đầu vào và đầu ra rõ ràng.
Không tự viết SQL/Cypher, không tự bỏ qua giai đoạn, không tự thay đổi thứ tự xử lý đã quy định ở đây.

## Quy tắc bất biến

- `scope_hint` chỉ là gợi ý; không coi nó là lựa chọn cuối cùng.
- Không mặc định bất kỳ phạm vi nào và không hiểu `core` là toàn bộ bản thể.
- Mỗi mẻ có thể chọn một hoặc nhiều scope (phạm vi).
- Chỉ dùng bản chụp lược đồ thuộc `ontologyVersionId` đã ghim cho phiên ingestion hiện tại.
- Không tạo hoặc sử dụng phạm vi/lược đồ mới trước khi người dùng phê duyệt.
- Không gọi `fill_ingestion` nếu người dùng chưa yêu cầu ghi/lưu/import rõ ràng.
- Không gọi `review_schema_proposal`, `apply_schema_proposal`, xóa hay rollback nếu người dùng
  chưa đưa ra quyết định tường minh.
- Bằng chứng phải trích nguyên văn từ đúng đoạn nguồn.
- Không làm mất đoạn nguồn hoặc bằng chứng khi một mẻ bị chặn.
- Chỉ xử lý mẻ kế tiếp sau khi mẻ hiện tại đã `STAGED` hoặc đã chuyển sang trạng thái
  chờ phê duyệt có chủ đích.
- Kết quả tool là nguồn sự thật của workflow. Nếu `terminal=true`, hoặc `nextAction`
  là `explicit_extraction_failure` / `report_tool_failure`, dừng ingestion ngay và
  báo lại lỗi; không gọi tiếp batch/scope/finalize/fill.
- Trong ingestion, tác nhân gốc đồng thời là semantic mapper.
- Tác nhân chỉ quyết định `className`, properties, edges, evidence và coverage.
- Tác nhân KHÔNG tạo `identity`. Python dựng identity từ
  `ontology.entityTypes[*].identityStrategy.required`.
- Mỗi semantic mapping phải kết thúc bằng đúng một lần
  `submit_ingestion_batch`; không tồn tại bước extract độc lập.
- Khi `get_ingestion_batch` trả `canonicalGraphContext`, dùng `ref` dạng
  `entity:<stable-key>` làm `sourceTempId` hoặc `targetTempId` để nối quan hệ tới
  entity đã `STAGED` ở mẻ trước. Không phát lại node cũ chỉ để tạo edge.
- Nếu một quan hệ cần entity chưa có trong mẻ hiện tại hoặc `canonicalGraphContext`,
  trì hoãn quan hệ đó; không tạo node không có bằng chứng trong chunk hiện tại.
- Evidence phải trích nguyên văn từ `chunks[*].text` đã được Python chuẩn hóa
  thành canonical plain text. Không trích từ Markdown raw nếu ký tự `**`, bảng `|`
  hoặc xuống dòng đã được chuẩn hóa khác đi.

## Thứ tự xử lý bắt buộc cho mỗi mẻ

1. `get_ingestion_batch`
2. Nếu mẻ chưa có phạm vi phù hợp:
   - `list_ontology_scopes`
   - chọn tập phạm vi phù hợp
3. `load_ontology_scopes`
4. Tác nhân gốc tạo trực tiếp `SemanticGraphPatchFragment` từ chunks và ontology.
5. `submit_ingestion_batch`

Sau `load_ontology_scopes`, thao tác ingestion kế tiếp phải là
`submit_ingestion_batch`. Không gọi extraction sub-agent hay extractor tool.

## A. Bắt đầu và chuẩn bị tài liệu

1. Gọi `begin_ingestion(artifact_name, document_key?, scope_hint?)`.
2. Lưu `ingestionId` và `ontologyVersionId` trả về.
3. Phía Python tự thực hiện các thao tác chuẩn bị tài liệu:
   - kiểm tra tệp;
   - phân tích nội dung;
   - khử trùng lặp;
   - làm sạch;
   - kiểm tra chất lượng;
   - chia đoạn;
   - gom các đoạn thành từng mẻ.
4. Không tải lược đồ chi tiết của một phạm vi cụ thể trong bước này.
5. Trạng thái làm việc của ingestion (workspace, batches, chunks) được lưu tạm trong bộ nhớ RAM của tiến trình hiện tại.
   PostgreSQL chỉ chịu trách nhiệm quản lý ontology/schema (phiên bản, scopes, compiled snapshots) và các đề xuất schema proposal, không lưu trạng thái ingestion job.
6. Nếu `begin_ingestion` trả về một workspace ingestion đang tồn tại trong tiến trình hiện tại (tái sử dụng phiên ingestion còn tồn tại trong tiến trình hiện tại / in-process reuse),
   tiếp tục từ `nextBatch`; không tự tạo phiên mới khi không cần thiết.

## B. Vòng lặp cho từng mẻ

Với đúng `nextBatch.batchIndex`:

1. Gọi `get_ingestion_batch` và đọc toàn bộ các đoạn thuộc mẻ hiện tại.

2. Gọi `list_ontology_scopes(ingestion_id)` để lấy danh mục phạm vi động gồm:
   - `scopeKey`
   - `description`
   - `summary`
   - `schemaHash`

   Không suy đoán phạm vi ngoài danh mục được trả về.

3. Dựa trên:
   - nội dung của mẻ;
   - danh mục phạm vi;
   - `scope_hint` nếu có, nhưng chỉ coi là gợi ý;

   xét từng chunk, xác định khái niệm của chunk rồi chọn hợp của các scope cần thiết:
   tập phạm vi nhỏ nhất nhưng đủ bao phủ toàn bộ mẻ.

   Một mẻ có thể sử dụng một hoặc nhiều phạm vi.

4. Gọi:

   `load_ontology_scopes(ingestion_id, scope_keys)`

   để tải các bản chụp lược đồ đã biên dịch và lược đồ hợp nhất tương ứng.

   Nếu phát hiện:
   - bản chụp không tồn tại;
   - mã băm không hợp lệ;
   - xung đột khi hợp nhất;
   - phiên bản không khớp với `ontologyVersionId` đã ghim;

   thì dừng mẻ và báo lỗi dữ liệu bản thể.

5. Dựa trực tiếp trên chunks từ `get_ingestion_batch` và ontology vừa tải,
   tác nhân gốc tạo `SemanticGraphPatchFragment`.

   Fragment semantic tuyệt đối không có trường `identity`. Không sao chép chunks
   qua một tác nhân trung gian và không gọi model lần nữa trước khi submit.

6. Bảo đảm:

   Các quy tắc nghiệp vụ vẫn phải được bảo đảm:
   - mọi đoạn nguồn có đúng một mục `coverage`;
   - coverage chỉ dùng `MAPPED`, `NOT_RELEVANT` hoặc `SCHEMA_GAP`;
   - `MAPPED` chỉ được dùng khi chính chunk đó có evidence (`chunkIndex: <index>`) trong ít nhất một
     node/property/edge của fragment; tuyệt đối không đánh `MAPPED` nếu không có bằng chứng nào trích từ chunk đó (tránh lỗi `MAPPED_WITHOUT_MAPPING`);
   - nếu một chunk chỉ chứa thông tin mở đầu, bối cảnh phụ trợ hoặc lịch sử tóm tắt mà không trích xuất thành node/property/edge riêng, hãy đánh dấu `NOT_RELEVANT` (kèm `reason` nêu rõ lý do). Nếu muốn đánh dấu `MAPPED`, BẮT BUỘC phải trích dẫn ít nhất một câu từ chunk đó vào thuộc tính (ví dụ: `description`, `notes`) hoặc bằng chứng thực thể;
   - `NOT_RELEVANT` chỉ dùng khi chunk không chứa tri thức cần đưa vào graph;
     tuyệt đối không dùng để che việc ontology thiếu khả năng biểu diễn;
   - nếu chunk có tri thức liên quan nhưng schema đã tải không có entity/property/
     relationship phù hợp, dùng `SCHEMA_GAP` và nêu rõ phần tri thức bị thiếu trong `reason`;
   - nếu chunk vừa có phần map được vừa có phần không biểu diễn được, ưu tiên
     `SCHEMA_GAP`; không được coi cả chunk là hoàn tất chỉ vì một phần đã map;
   - thuộc tính và quan hệ phải có bằng chứng nguyên văn;
   - chỉ sử dụng loại thực thể, thuộc tính và quan hệ có trong lược đồ đã tải.
   - edge endpoint được dùng tempId node mới trong fragment hiện tại hoặc `ref`
     từ `canonicalGraphContext`; không dùng tempId của batch trước.

7. Gọi ngay:

   `submit_ingestion_batch(
       ingestion_id,
       batch_index,
       scope_keys,
       graph_fragment
   )`

   Python sẽ canonicalize tên, dựng identity, validate, guard repair và stage.

8. Chỉ khi `submit_ingestion_batch` trả mẻ ở trạng thái `STAGED`
   mới chuyển sang mẻ kế tiếp.

9. Nếu `terminal=true`, dừng ngay; không retry, không gọi lại `list_ontology_scopes`, `load_ontology_scopes`, `submit_ingestion_batch`, `finalize_ingestion` hay `fill_ingestion` cho ingestion đó.

### Khi gửi mẻ thất bại

Phải phân biệt lỗi cấu trúc/lỗi kiểm tra với thiếu lược đồ thật sự.

#### 1. Lỗi cấu trúc hoặc lỗi kiểm tra dữ liệu

Ví dụ:
- sai kiểu dữ liệu;
- thiếu trường bắt buộc;
- thừa trường;
- coverage không hợp lệ;
- bằng chứng không hợp lệ;
- đầu mút quan hệ không tồn tại;
- dùng sai tên đã có trong lược đồ.

Khi đó:

1. Không gọi lại `begin_ingestion`, không tạo ingestion mới và không chuyển batch.
2. Dùng `batchIndex`, `scopeKeys`, `affectedChunkIndexes` và `errors` từ checkpoint.
3. Gọi lại `get_ingestion_batch(ingestion_id, batchIndex)` để lấy đúng batch cần sửa.
4. Tải lại đúng `scopeKeys` cần thiết; không giữ hoặc khôi phục toàn bộ history batch cũ.
5. Dựa trên source + schema + validation errors, tạo lại fragment hoàn chỉnh cho cùng batch,
   sửa lỗi được báo và giữ các fact/node/edge hợp lệ.
6. **QUY TẮC BẮT BUỘC KHI SỬA LỖI (REPAIR BATCH):**
   - **BẮT BUỘC GIỮ NGUYÊN 100% `tempId`** của tất cả các node đã khai báo ở lần submit trước (ví dụ: `node.tempId` ban đầu là `org_vanquan` thì BẮT BUỘC giữ nguyên `org_vanquan`, tuyệt đối KHÔNG tự ý đổi thành `org_van_quan`, `org_vq` hay bất kỳ tên nào khác). Đổi `tempId` sẽ bị hệ thống xem là đã xóa node hợp lệ và báo lỗi `REPAIR_DROPPED_VALID_NODE`.
   - **KHÔNG XÓA BỎ CÁC NODE, THUỘC TÍNH, QUAN HỆ HỢP LỆ**: Không xóa bỏ các thành phần hợp lệ đã submit trước đó nếu chúng không thuộc diện báo lỗi (tránh `REPAIR_DROPPED_VALID_NODE`, `REPAIR_DROPPED_VALID_FACT`, `REPAIR_DROPPED_VALID_EDGE`).
   - **KHI SỬA LỖI TRÍCH DẪN (`EVIDENCE_NOT_GROUNDED`):**
     + Giữ nguyên danh sách node, thuộc tính, giá trị thuộc tính, edges và coverage.
     + Chỉ sửa duy nhất trường `evidence.text`: tìm lại đúng câu văn trong chunk nguồn, copy chính xác từng ký tự (verbatim) vào `text`.
   - **KHI SỬA LỖI COVERAGE (`MAPPED_WITHOUT_MAPPING`):**
     + Nếu muốn giữ chunk đó là `MAPPED`: bổ sung node mới hoặc bổ sung thuộc tính (ví dụ: `description`, `notes`) vào node hiện có mang bằng chứng trích từ chunk đó.
     + Nếu chunk đó không có tri thức mới cần lưu: đổi quyết định của chunk đó thành `NOT_RELEVANT` với `reason` phù hợp.
7. Gửi lại đúng batch bằng `submit_ingestion_batch`.
8. Không tạo proposal ontology trừ khi tool phân loại rõ là schema gap.

Conversation history không phải bộ nhớ ingestion. Workspace Python là nguồn state;
sau mỗi submit, fragment/chunks/schema cũ có thể đã bị compact khỏi model context.

#### 2. `UNKNOWN_ENTITY_TYPE`, `UNKNOWN_PROPERTY`, `UNKNOWN_RELATIONSHIP`

Trước tiên phải xác định đây là:

- mô hình dùng sai lược đồ hiện có; hoặc
- tài liệu thật sự chứa khái niệm chưa biểu diễn được trong bản thể.

Nếu mô hình dùng sai lược đồ:

1. Không tạo đề xuất.
2. Giữ nguyên phạm vi/lược đồ hiện tại nếu vẫn phù hợp.
3. Sửa đúng semantic fragment theo schema đã tải và giữ các fact hợp lệ.
4. Gửi lại đúng mẻ hiện tại.

Chỉ khi nội dung thật sự không thể biểu diễn bằng bản thể hiện có mới đi vào
luồng đề xuất thay đổi bản thể.

#### 3. Phân loại động lỗi ontology

Ưu tiên làm theo chẩn đoán do tool trả về, không suy đoán theo tên quan hệ cụ thể:

- `RELATIONSHIP_MAPPING_MISMATCH`: ontology hiện tại đã có quan hệ tương thích với
  cặp source/target; sửa edge theo `candidateRelationships`, giữ nguyên các node/fact hợp lệ.
- `MISSING_SCOPE`: khái niệm/quan hệ tồn tại trong scope khác của đúng ontology version
  đã ghim; dùng `candidateScopes` nếu có, tải lại hợp scope cần thiết rồi submit lại batch.
- `SCHEMA_GAP_CANDIDATE`: toàn bộ ontology version đã ghim không có cấu trúc tương thích;
  khi đó mới đánh giá proposal thay đổi ontology.
- Không hard-code cách xử lý theo tên như `has_policy`, `located_at` hay tên domain cụ thể.

#### 4. Thiếu lược đồ thật sự

Thực hiện theo thứ tự:

1. Ưu tiên dùng phạm vi hiện có.
2. Nếu có thể, ưu tiên mở rộng phạm vi hiện có bằng:
   - loại thực thể (`NEW_ENTITY_TYPE`);
   - thuộc tính (`NEW_PROPERTY`);
   - quan hệ (`NEW_RELATIONSHIP`);
   - bí danh (`NEW_ALIAS`).
3. Chỉ đề xuất `NEW_SCOPE` khi nội dung thuộc một miền khái niệm riêng
   và không phù hợp để mở rộng phạm vi hiện có.
4. Gọi `create_schema_proposal` với:
   - `proposal_type`: BẮT BUỘC chọn đúng 1 trong các giá trị enum sau (tuyệt đối KHÔNG tự nghĩ tên lạ như `EXTEND_RELATIONSHIP_SOURCE` hay `ADD_RELATIONSHIP`):
     + `NEW_RELATIONSHIP`: Thêm quan hệ mới giữa hai loại thực thể (ví dụ: `organization -> policy`).
     + `MODIFY_RELATIONSHIP`: Sửa đổi cấu trúc quan hệ hiện có.
     + `NEW_ENTITY_TYPE`: Thêm loại thực thể mới.
     + `NEW_PROPERTY`: Thêm thuộc tính mới cho một loại thực thể.
     + `MODIFY_ENTITY_TYPE`: Sửa đổi loại thực thể hiện có.
     + `MODIFY_PROPERTY`: Sửa đổi thuộc tính hiện có.
     + `NEW_ALIAS`: Thêm bí danh.
     + `NEW_SCOPE`, `MODIFY_SCOPE`: Tạo mới hoặc sửa scope.
   - `technical_name`: Tên kỹ thuật của đối tượng cần tạo/sửa (ví dụ: `has_policy`).
   - `payload`: Bắt buộc cung cấp thông tin cấu trúc chi tiết tương ứng với `proposal_type`:
     + Với `NEW_RELATIONSHIP`: `{"technicalName": "has_policy", "sourceEntityType": "organization", "targetEntityType": "policy", "displayName": "Có chính sách", "cardinality": "MANY_TO_MANY", "description": "Quan hệ chính sách từ tổ chức"}`
     + Với `NEW_PROPERTY`: `{"entityType": "class_program", "technicalName": "tuition", "dataType": "FLOAT", "displayName": "Học phí", "required": false}`
     + Với `NEW_ENTITY_TYPE`: `{"technicalName": "event", "displayName": "Sự kiện", "identityFields": ["name"]}`
   - `reason`: Lý do chi tiết tại sao cần mở rộng lược đồ dựa trên tài liệu.
   - `evidence`: Bằng chứng trích xuất từ tài liệu (ví dụ: `{"chunkIndex": 20, "quote": "..."}`).
   - `affected_scope_keys`: Danh sách scope bị ảnh hưởng (ví dụ: `["core", "fundamentals", "training"]`).
5. Dừng mẻ ở `awaiting_schema_approval`.
6. Không chuyển sang mẻ kế tiếp.
7. Không tự phê duyệt (không tự approve) đề xuất.

## C. Người dùng phê duyệt thay đổi bản thể

1. Dùng `get_schema_proposal` để trình bày đề xuất và bằng chứng cho người dùng.

2. Chờ quyết định tường minh.

   Nếu người dùng từ chối:
   - gọi `review_schema_proposal(..., approved=false, ...)`;
   - sau đó trích xuất lại theo lược đồ cũ, đánh `NOT_RELEVANT`,
     hoặc dừng theo chỉ dẫn của người dùng.

   Nếu người dùng duyệt:
   - gọi `review_schema_proposal(..., approved=true, ...)`.

3. Chỉ sau trạng thái `APPROVED` (chỉ sau khi đề xuất ở trạng thái `APPROVED` được người dùng phê duyệt tường minh) mới gọi
   `apply_schema_proposal` với mã phiên bản mới.

   Phía Python chịu trách nhiệm:
   - tạo phiên bản bản thể mới từ phiên bản hiện hành;
   - áp dụng thay đổi đã duyệt;
   - biên dịch lại các bản chụp lược đồ cần thiết;
   - kích hoạt phiên bản mới theo quy tắc hệ thống;
   - làm mới bộ nhớ đệm phạm vi/bản chụp.

4. Sau khi bản thể thay đổi, gọi `rebase_ingestion` tường minh.

   Không âm thầm đổi `ontologyVersionId` giữa chừng.

   `rebase_ingestion` phải xác định rõ:
   - mẻ nào vẫn còn hợp lệ;
   - mẻ nào phải xử lý lại;
   - mẻ nào bị ảnh hưởng bởi thay đổi lược đồ.

5. **TIẾP TỤC SAU KHI REBASE:**
   - Đọc kỹ `nextBatch` trong phản hồi của `rebase_ingestion` để biết batch tiếp theo cần xử lý (ví dụ: nếu các batch trước bị vô hiệu hóa do thay đổi scope, `nextBatch` sẽ chỉ rõ `batchIndex`).
   - Gọi `get_ingestion_batch(ingestion_id, nextBatch.batchIndex)` và tiếp tục vòng lặp B.
   - Nếu phải xử lý lại batch đã từng trích xuất, hãy ưu tiên trích xuất đúng và đầy đủ các thực thể/quan hệ như trước, chú ý trích dẫn nguyên văn bằng chứng.

## D. Hoàn tất và ghi chính thức

1. Khi mọi mẻ đều `STAGED`, gọi `finalize_ingestion`.

2. Nếu còn mẻ lỗi hoặc đề xuất đang chờ:
   - xử lý đúng mẻ;
   - không ép hoàn tất.

3. Khi trạng thái là `ready_to_fill`:
   - nếu người dùng chỉ yêu cầu trích xuất/kiểm tra:
     dừng và nói rõ Neo4j chưa thay đổi;
   - nếu người dùng đã yêu cầu ghi:
     gọi `fill_ingestion` đúng một lần.

4. `fill_ingestion` mới thực hiện:
   - tạo embedding;
   - ghi dữ liệu chính thức vào Neo4j (nơi lưu trữ dữ liệu Knowledge Graph cuối cùng);
   - đọc lại để kiểm chứng;
   - cập nhật trạng thái hoàn tất (COMMITTED) cho workspace trong bộ nhớ RAM của tiến trình hiện tại.

5. Chỉ báo thành công khi công cụ trả về:

   `readbackVerified=true`

PostgreSQL chỉ quản lý ontology/schema và các đề xuất thay đổi lược đồ. Toàn bộ trạng thái ingestion (workspace/job) được giữ trong RAM của tiến trình hiện tại và dữ liệu tri thức sau khi nạp được ghi bền vững vào Neo4j.

## Báo cáo cuối

Báo cáo tối thiểu:

- `ingestionId`;
- phiên bản bản thể đã dùng;
- số mẻ `STAGED`;
- số mẻ bị chặn nếu có;
- đề xuất thay đổi bản thể và quyết định của người dùng nếu có;
- trạng thái hoàn tất;
- trạng thái ghi Neo4j;
- số node;
- số edge;
- số chunk;
- số fact;
- kết quả đọc lại kiểm chứng.

Không gọi dữ liệu đang ở trạng thái tạm là đã được ghi chính thức.
