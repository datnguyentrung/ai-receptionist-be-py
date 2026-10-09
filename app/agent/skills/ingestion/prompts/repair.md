Sửa chính xác một batch dựa trên protectedBaseline và validationIssues.

NGUYÊN TẮC:
1. Chỉ trả về SemanticGraphRepairDelta với baselineFingerprint đã cung cấp.
2. Giữ nguyên toàn bộ dữ kiện baseline hợp lệ.
3. Chỉ sửa những dữ kiện liên quan trực tiếp đến validationIssues.
4. Không thêm, xóa hoặc thay đổi thông tin không liên quan đến lỗi.
5. Mọi dữ kiện sau sửa phải được hỗ trợ bởi chunk nguồn.

NẾU GẶP EVIDENCE_NOT_GROUNDED:
- Ưu tiên tìm và sao chép lại đoạn trích nguyên văn từ chunk.
- Giữ nguyên dấu câu, khoảng trắng và ký tự xuống dòng.
- Không tự viết lại nội dung evidence theo cách diễn đạt của mình.
- Nếu bằng chứng quá dài, chọn đoạn ngắn hơn nhưng vẫn đủ chứng minh.
- Không thay đổi dữ kiện đúng chỉ vì evidence bị sai định dạng.

Không gọi công cụ nào.
