---
name: ingestion
description: >
  Nạp tài liệu vào Knowledge Graph theo từng mẻ, chọn phạm vi bản thể động,
  trích xuất tri thức theo semantic contract tối giản, phát hiện schema gap,
  điều phối phê duyệt thay đổi bản thể và chỉ ghi Neo4j khi được yêu cầu.
metadata:
  adk_additional_tools:
    - begin_ingestion
    - create_schema_proposal
    - get_schema_proposal
    - review_schema_proposal
    - apply_schema_proposal
    - rebase_ingestion
    - finalize_ingestion
    - fill_ingestion
    - get_ingestion_status
    - ingestion_batch_agent
---

# Quy trình nạp dữ liệu (Ingestion workflow)

Tải skill này trước khi bắt đầu nạp dữ liệu và duy trì các hướng dẫn này cho đến khi
quy trình kết thúc. Root agent điều phối toàn bộ quy trình xử lý tài liệu; nó không
trực tiếp truy xuất chunk, chọn scope, trích xuất graph, gửi batch hay tự sửa batch.

1. Gọi `begin_ingestion` cho artifact được cung cấp. Đọc `ingestionId` và
   `nextBatch` từ kết quả trả về.
2. Với mỗi batch đang chờ xử lý, gọi `ingestion_batch_agent` một lần với định dạng
   tham số chính xác như sau: `{ "request": "{\\"ingestionId\\":\\"...\\",\\"batchIndex\\":0}" }`.
   Batch agent chịu trách nhiệm xử lý duy nhất batch đó:
   nó truy xuất batch một lần, tải các ontology scope, tạo
   `SemanticGraphPatchFragment`, và gửi hoặc sửa lại fragment cho đến khi được staged,
   terminal, hoặc bị chặn do cần duyệt schema (schema review).
3. Nếu kết quả batch báo cáo có schema blocker, hãy điều phối các công cụ
   proposal/review/apply/rebase hiện có tại root agent. Sau khi rebase, gọi lại chính
   batch agent đó cho batch hiện tại. Các ontology scope phải được tải lại;
   các document chunk đã lưu cache vẫn giữ nguyên hiệu lực.
4. Khi một batch đã được staged, sử dụng giá trị `nextBatch` được trả về và gọi batch
   agent cho batch đang chờ tiếp theo. Không truy xuất lại các batch đã hoàn thành.
5. Khi tất cả các batch đã được staged, gọi `finalize_ingestion`. Chỉ gọi
   `fill_ingestion` sau khi quá trình finalization báo cáo trạng thái `ready_to_fill`
   và người dùng đã yêu cầu lưu trữ (persistence).

Batch agent chỉ nhận các primitive của batch. Các service tool thực thi tính toàn vẹn
về định danh (identity), kiểm thực (validation), chuẩn hóa (canonicalization) và
lưu trữ (persistence); không diễn giải lại hoặc ghi đè chúng trong prompt. Payload
trả về từ các tool giữ nguyên các tên trường JSON đã quy định. Ngữ cảnh xuyên suốt
các batch (cross-batch context) của root agent chỉ chứa các canonical entity, tuyệt
đối không chứa chunk cũ hay output thô của model.
