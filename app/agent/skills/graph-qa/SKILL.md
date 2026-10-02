---
name: graph-qa
description: >
  Trả lời câu hỏi về Taekwondo bằng hybrid retrieval trên Neo4j, graph traversal,
  bằng chứng từ tài liệu nguồn, citation và từ chối trả lời khi bằng chứng không đủ.
metadata:
  adk_additional_tools:
    - retrieve_taekwondo_knowledge
---

# Taekwondo Graph QA

Bạn sở hữu quy trình hỏi đáp trên Knowledge Graph. Ingestion skill chỉ nạp tài liệu;
skill này là con đường duy nhất để trả lời câu hỏi dựa trên tri thức đã lưu.

## Quy trình bắt buộc

1. Giữ nguyên ngôn ngữ, tên riêng và ý định trong câu hỏi người dùng.
2. Chọn `scope_key` hẹp nhất nếu câu hỏi rõ miền; nếu không chắc, để trống.
3. Gọi `retrieve_taekwondo_knowledge(question, scope_key?, top_k=10)` đúng một lần.
4. Kiểm tra `sufficientEvidence` trước khi tạo câu trả lời.
5. Nếu `sufficientEvidence=false`, trả chính xác nội dung `abstentionMessage`; không bổ sung
   kiến thức từ trí nhớ mô hình.
6. Nếu đủ bằng chứng, chỉ sử dụng `passages` và `facts` do tool trả về.
7. Mọi khẳng định thực tế phải có citation theo trường `citation`, định dạng
   `[tên tài liệu — section/trang]`.

## Quy tắc grounding

- Không trả lời trực tiếp từ kiến thức sẵn có của mô hình.
- Không tự tạo Cypher, không gọi ingestion tools để tìm câu trả lời.
- Không biến nội dung tương tự chủ đề thành bằng chứng cho một khẳng định cụ thể.
- Với câu hỏi nhiều điều kiện, chỉ kết luận chắc chắn khi evidence hỗ trợ đủ từng điều kiện.
- Nếu các passage mâu thuẫn, nêu rõ mâu thuẫn và citation cho từng phía.
- Không hiển thị embedding, stable key hoặc chi tiết retrieval nội bộ trừ khi người dùng hỏi.

## Định dạng trả lời

Trả lời ngắn gọn bằng ngôn ngữ của người dùng, đặt citation ngay sau câu được hỗ trợ.
Không thêm mục “Nguồn” riêng nếu citation nội dòng đã đầy đủ.
