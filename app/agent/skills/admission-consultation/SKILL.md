---
name: admission-consultation
description: >
  Tư vấn điều kiện nhập học, độ tuổi tiếp nhận, hồ sơ cần chuẩn bị và các quy
  định tuyển sinh cho phụ huynh và học viên mới.
metadata:
  adk_additional_tools:
    - search_admission_knowledge
---

# Quy trình Tư vấn Điều kiện Nhập học (Admission Consultation)

Mục tiêu của skill này là giải đáp chính xác điều kiện nhập học, đối tượng tiếp nhận và hồ sơ cần thiết cho học viên mới dựa trên tri thức từ Graph RAG.

## Lộ trình thực hiện

1. **Xác định nhu cầu tuyển sinh của phụ huynh/học viên**
   - Làm rõ đối tượng dự kiến học (độ tuổi, người mới bắt đầu hay đã có nền tảng võ thuật, tình trạng sức khỏe nếu người dùng đề cập).
   - Nếu thông tin đã đủ rõ để tra cứu, chuyển ngay sang bước tra cứu.

2. **Tra cứu thông tin tuyển sinh**
   - Gọi `search_admission_knowledge(query=...)` với truy vấn tập trung vào điều kiện nhập học, độ tuổi tối thiểu/tối đa, yêu cầu sức khỏe hoặc hồ sơ đăng ký.
   - Không tự suy diễn điều kiện tuyển sinh khi chưa có kết quả từ tool.

3. **Tổng hợp và tư vấn rõ ràng**
   - Trình bày ngắn gọn, dễ hiểu theo từng ý: độ tuổi phù hợp, yêu cầu đầu vào, giấy tờ/hồ sơ cần chuẩn bị (nếu có) và các lưu ý quan trọng cho người mới.
   - Nếu thiếu thông tin trong dữ liệu trả về, thông báo trung thực phần chưa có dữ liệu và gợi ý liên hệ tư vấn viên.

4. **Đề xuất bước tiếp theo**
   - Chủ động gợi ý phụ huynh chọn lớp phù hợp (chuyển sang skill `class-recommendation`) hoặc tìm hiểu chính sách học thử (chuyển sang skill `trial-consultation`).
