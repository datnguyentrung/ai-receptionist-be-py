---
name: ingestion
description: >
  Nạp từng tài liệu Markdown/TXT vào knowledge graph theo nguồn và thời gian,
  với báo cáo trước commit và xác nhận thật từ người dùng.
metadata:
  adk_additional_tools:
    - begin_ingestion
    - create_schema_proposal
    - get_schema_proposal
    - review_schema_proposal
    - apply_schema_proposal
    - rebase_ingestion
    - finalize_ingestion
    - get_ingestion_preview
    - list_ingestion_conflicts
    - review_ingestion_conflict
    - commit_ingestion
    - abort_ingestion
    - get_ingestion_status
    - ingestion_batch_agent
---

# Ingestion một tài liệu mỗi lượt

Mỗi lần nạp có `document_key` là đường dẫn tương đối ổn định của tài liệu. Nạp
tài liệu khác tạo nguồn bổ sung; nạp lại cùng key và nội dung trả job cũ; nội
dung mới của cùng key tạo phiên bản mới. Chỉ chấp nhận Markdown và TXT UTF-8.

1. Gọi `begin_ingestion`. Lấy `ingestionId`, `nextBatch`. Nếu job đã có preview,
   đọc `get_ingestion_preview`; nếu đã `COMMITTED`, báo idempotent và dừng.
2. Chạy `ingestion_batch_agent` cho đúng `nextBatch.batchIndex`. Sau mỗi batch,
   lấy `nextBatch` mới từ response hoặc `get_ingestion_status`; tiếp tục cho
   đến khi tất cả batch `STAGED`.
3. Nếu batch báo schema gap, kiểm tra scope hiện có trước. Nếu thật sự thiếu,
   tạo proposal có bằng chứng. Dừng cho người dùng duyệt qua tool có ADK
   confirmation. `apply_schema_proposal` chỉ tạo/cập nhật ontology DRAFT của
   job. Gọi `rebase_ingestion` với draft ID và chạy đúng `nextBatch` trả về,
   kể cả khi quay về batch 0. Không tự nhận là người duyệt.
4. Gọi `finalize_ingestion`; nếu batch cần sửa thì sửa rồi finalize lại. Khi
   thành công, đọc `get_ingestion_preview` và trình cho người dùng báo cáo
   thay đổi, nguồn/trích dẫn, thời gian, schema và mâu thuẫn. Không gọi commit
   trước khi người dùng thấy báo cáo.
5. Cho **mỗi** mâu thuẫn, dùng `review_ingestion_conflict` với đúng một lựa chọn
   `CONFIRM`, `REJECT`, `OTHER`. `OTHER` cần ghi chú và giữ chưa phân xử. Sau
   mỗi quyết định, đọc lại preview vì `reportDigest` thay đổi.
6. Sau khi người dùng xác nhận commit cuối, gọi `commit_ingestion` với
   `ingestionId` và `reportDigest` mới nhất. Tool này yêu cầu ADK confirmation;
   model không được tự điền quyết định duyệt hoặc gọi đường ghi khác. Nếu báo
   `PREVIEW_STALE`, tạo và trình báo cáo mới để xin xác nhận lại. Nếu commit bị
   gián đoạn, dùng cùng job để resume; chỉ báo thành công khi trạng thái
   `COMMITTED` và `readbackVerified=true`.
7. Nếu người dùng hủy trước commit, gọi `abort_ingestion`; draft của job không
   được kích hoạt và job chuyển `ABORTED`.

Trước commit, Neo4j không công bố fact mới và PostgreSQL chỉ giữ schema DRAFT.
Sau commit, truy vấn thường dùng phiên bản hiện hành; truy vấn lịch sử vẫn giữ
nguồn cũ. Với mâu thuẫn `OTHER`, câu trả lời phải trích dẫn cả hai nguồn và nêu
rõ chưa xác định nguồn hiện hành.
