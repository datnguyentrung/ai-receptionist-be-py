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

1. Gọi `begin_ingestion`. Lấy `ingestionId`, `nextBatch`. Nếu response có
   `pendingSchemaProposal` hoặc `pendingSchemaProposals`, trình bày proposal và
   yêu cầu người dùng duyệt ngay; không gọi `get_ingestion_status` hoặc
   `get_schema_proposal` chỉ để đọc lại proposal đó. Nếu job đã có preview,
   đọc `get_ingestion_preview`; nếu đã `COMMITTED`, báo idempotent và dừng.
2. Chạy `ingestion_batch_agent` với `ingestion_id` và đúng `batch_index` từ
   `nextBatch`. Sau mỗi batch,
   lấy `nextBatch` mới từ response hoặc `get_ingestion_status`; tiếp tục cho
   đến khi tất cả batch `STAGED`.
3. Xử lý trường hợp thiếu schema (chỉ chạy khi batch báo schema gap).

   Trong phase xử lý batch, hệ thống đã tải và đối chiếu ontology catalog/schema
   cần thiết từ ontology version đang được job sử dụng. Không tải lại schema context
   hoặc chạy lại batch để kiểm tra cùng schema gap.

   Nếu ontology hiện tại thực sự không có entity type, property hoặc relationship cần thiết:

   1. Tạo schema proposal kèm bằng chứng trích từ tài liệu. Gửi một object
      `proposal` khớp `proposalType`; không gửi `payload` tự do. Với
      `NEW_PROPERTY`, dùng `payload.entityType` và một `payload.dataType` viết
      hoa (`STRING`, `INTEGER`, `FLOAT`, `BOOLEAN`, `DATE`, `DATETIME`, `UUID`,
      hoặc `JSON`). Với `NEW_ENTITY_TYPE`, dùng
      `payload.identityStrategy.sourceScoped: true` hoặc
      `payload.identityStrategy.required: ["property_name"]`. Mỗi property có
      trong `identityStrategy.required` phải được khai báo đầy đủ trong
      `payload.properties` với `technicalName` và `dataType`.
   2. Sau khi tạo proposal, trả lời bằng văn bản để trình proposal và dừng lượt.
      Không gọi `review_schema_proposal` trong cùng lượt tạo proposal. Chỉ gọi
      tool review ở lượt sau khi có tin nhắn mới của người dùng đồng ý hoặc từ
      chối; ADK confirmation vẫn là lớp xác nhận cuối.
   3. Sau khi người dùng duyệt, gọi `apply_schema_proposal` để tạo hoặc cập nhật
      ontology DRAFT riêng cho ingestion job. Không sửa ontology ACTIVE.
   4. Gọi `rebase_ingestion` với UUID trong
      `apply_schema_proposal.targetOntologyVersionId`. Không dùng tên version
      dạng `draft-...` làm ID.
   5. Chạy lại đúng `nextBatch` mà rebase trả về; `nextBatch` có thể quay lại batch 0.

   Model không được tự phê duyệt schema proposal.

   Nếu status có `SCHEMA_PROPOSAL_PENDING` hoặc `wait_for_schema_review` và vẫn
   có proposal thực sự đang chờ (`pendingProposalCount > 0` hoặc
   `pendingSchemaProposal(s)`), dừng lượt và chờ người dùng duyệt. Không apply,
   rebase, chạy lại batch hoặc tạo proposal khác trong lúc proposal đang chờ.
   Nếu lỗi này là dữ liệu cũ nhưng `pendingProposalCount` bằng 0, làm theo
   `nextAction` mới; thường là `rebase_ingestion`, sau đó chạy lại `nextBatch`.
4. Khi tất cả batch đã được xử lý, gọi `finalize_ingestion`.

   Nếu finalize hoặc validation của một batch trả về repair_required:
   1. Gọi công cụ repair cho đúng batch với repairContext.
   2. Chờ batch được validate và stage lại.
   3. Gọi `finalize_ingestion` lại.

   Chỉ khi finalize thành công mới gọi `get_ingestion_preview`.
   Trình preview cho người dùng, gồm thay đổi, nguồn/trích dẫn, thời gian,
   schema và mâu thuẫn. Không gọi `commit_ingestion` trước khi người dùng xem
   và xác nhận báo cáo.
5. Cho **mỗi** mâu thuẫn, dùng `review_ingestion_conflict` với đúng một lựa chọn
   `CONFIRM`, `REJECT`, `OTHER`. `OTHER` cần ghi chú và giữ chưa phân xử. Sau
   mỗi quyết định, đọc lại preview vì `reportDigest` thay đổi.
6. Sau khi người dùng xác nhận commit cuối, gọi `commit_ingestion` với
   `ingestionId` và `reportDigest` mới nhất. Tool này yêu cầu ADK confirmation;
   model không được tự điền quyết định duyệt hoặc gọi đường ghi khác. Nếu báo
   `PREVIEW_STALE`, tạo và trình báo cáo mới để xin xác nhận lại. Nếu commit bị
   gián đoạn, dùng cùng job để resume; chỉ báo thành công khi trạng thái
   `COMMITTED` và `readbackVerified=true`. Khi thành công, trả lời bằng báo cáo
   commit cuối gồm ingestion/version ID, counts và readback; không gọi lại preview
   hoặc commit cho cùng job.
7. Nếu người dùng hủy trước commit, gọi `abort_ingestion`; draft của job không
   được kích hoạt và job chuyển `ABORTED`.

Trước commit, Neo4j không công bố fact mới và PostgreSQL chỉ giữ schema DRAFT.
Sau commit, truy vấn thường dùng phiên bản hiện hành; truy vấn lịch sử vẫn giữ
nguồn cũ. Với mâu thuẫn `OTHER`, câu trả lời phải trích dẫn cả hai nguồn và nêu
rõ chưa xác định nguồn hiện hành.
