---
name: graph-qa
description: >
  Trả lời các câu hỏi về Taekwondo bằng evidence đã được deterministic hybrid
  retrieval truy xuất từ Neo4j.
---

# Taekwondo Graph QA

Graph QA là con đường duy nhất để trả lời từ kho tri thức Taekwondo đã ingest.
Nó không cung cấp function tool cho LLM. Runtime tự thực hiện đúng một hybrid
retrieval operation, bên trong kết hợp vector, full-text, entity traversal và
fact support trên dữ liệu đã `COMMITTED`.

Kết quả công khai cho formatter chỉ gồm evidence đã grounded:

- `READY`: có passages hoặc facts với citation.
- `INSUFFICIENT`: không có đủ bằng chứng; phải abstain.
- `UNAVAILABLE`: retrieval infrastructure không còn path khả dụng; phải abstain.

Không tự trả lời bằng kiến thức nền, không sinh Cypher, không chọn strategy,
không retry function calling để tìm thêm. Vector score, embedding, raw Neo4j
values và retrieval trace là chi tiết nội bộ.

Số liệu, danh sách và địa điểm chỉ được nêu khi passages/facts đã publish hỗ
trợ trực tiếp; không suy luận một con số chính xác từ tập ứng viên vector.
