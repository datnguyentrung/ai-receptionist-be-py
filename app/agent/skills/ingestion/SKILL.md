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
   - quyết định coverage chỉ là `MAPPED` hoặc `NOT_RELEVANT`;
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

9. Nếu `terminal=true`, dừng ngay; không retry và không chuyển batch.

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

1. Không gọi lại `begin_ingestion`.
2. Không tạo phiên ingestion mới.
3. Không chuyển sang mẻ kế tiếp.
4. Không tạo đề xuất thay đổi bản thể.
5. Giữ nguyên mẻ hiện tại.
6. Giữ nguyên tập phạm vi nếu chúng vẫn hợp lệ.
7. Giữ nguyên lược đồ đã tải nếu chúng vẫn hợp lệ.
8. Đọc semantic fragment trước cùng validation errors, chỉ sửa đúng phần lỗi
   và giữ mọi fact/node/edge hợp lệ không liên quan.
9. Gửi lại đúng mẻ hiện tại bằng `submit_ingestion_batch`; không có bước
   extract độc lập trước submit.

Nếu ngữ cảnh hiện tại không còn đủ dữ liệu cần thiết thì chỉ lấy lại
những dữ liệu thiếu tối thiểu; không tự khởi động lại toàn bộ quy trình.

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

#### 3. Thiếu lược đồ thật sự

Thực hiện theo thứ tự:

1. Ưu tiên dùng phạm vi hiện có.
2. Nếu có thể, ưu tiên mở rộng phạm vi hiện có bằng:
   - loại thực thể;
   - thuộc tính;
   - quan hệ;
   - bí danh.
3. Chỉ đề xuất `NEW_SCOPE` khi nội dung thuộc một miền khái niệm riêng
   và không phù hợp để mở rộng phạm vi hiện có.
4. Gọi `create_schema_proposal` với:
   - thay đổi tối thiểu cần thiết;
   - phạm vi bị ảnh hưởng;
   - bằng chứng nguồn.
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

5. Lấy lại chính mẻ bị chặn và tiếp tục vòng lặp B.

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
