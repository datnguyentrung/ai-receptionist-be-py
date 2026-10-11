Sửa chính xác một batch dựa trên protectedBaseline và validationIssues.

NGUYÊN TẮC:
1. TUYỆT ĐỐI KHÔNG sinh lại các node/edge đã có trong protectedBaseline nếu chúng không có trong danh sách lỗi.
2. CHỈ trả về các node/edge trong danh sách `repaired_nodes` và `repaired_edges` liên quan trực tiếp đến validationIssues.
3. Khi sửa bằng chứng (evidence), CHỈ ĐƯỢC PHÉP dùng chunkIndex nằm trong phạm vi batch hiện tại. Tuyệt đối không trỏ về chunk của batch khác.
4. Nếu một thông tin bị ảo giác (hallucination) và không có căn cứ sửa chữa, đưa tempId vào `dropped_temp_ids`.

NẾU GẶP EVIDENCE_NOT_GROUNDED:
- Ưu tiên tìm và sao chép lại đoạn trích nguyên văn từ chunk.
- Giữ nguyên dấu câu, khoảng trắng và ký tự xuống dòng.
- Không tự viết lại nội dung evidence theo cách diễn đạt của mình.
- Nếu bằng chứng quá dài, chọn đoạn ngắn hơn nhưng vẫn đủ chứng minh.
- Không thay đổi dữ kiện đúng chỉ vì evidence bị sai định dạng.

Không gọi công cụ nào.
