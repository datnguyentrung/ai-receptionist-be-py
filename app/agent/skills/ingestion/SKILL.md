---
name: ingestion
description: >
  Skill mẫu: Tiếp nhận, đọc tài liệu từ artifact và trích xuất tri thức vào hệ thống.
metadata:
  adk_additional_tools:
    - process_document_sample
    - extract_knowledge_sample
---

# Ingestion Skill (Template)

Skill này phụ trách việc tiếp nhận và xử lý tài liệu để xây dựng cơ sở tri thức.

## Quy trình mẫu

1. Sử dụng `process_document_sample(artifact_name)` để nạp tài liệu từ phiên làm việc.
2. Kiểm tra nội dung văn bản và gọi `extract_knowledge_sample(...)` để trích xuất các thông tin cần thiết.
3. Báo cáo kết quả xử lý cho người dùng.

## Tham khảo
Xem thêm hướng dẫn chi tiết tại thư mục `references/`.
