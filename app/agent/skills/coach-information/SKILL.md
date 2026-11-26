---
name: coach-information
description: >
  Giới thiệu đội ngũ huấn luyện viên, võ sư giảng dạy, đẳng cấp chuyên môn và
  kinh nghiệm huấn luyện tại trung tâm.
metadata:
  adk_additional_tools:
    - search_coach_information
---

# Quy trình Giới thiệu Đội ngũ Huấn luyện viên (Coach Information)

Mục tiêu của skill này là cung cấp thông tin chính xác và tạo sự tin tưởng cho phụ huynh về trình độ, bằng cấp và kinh nghiệm của đội ngũ huấn luyện viên.

## Lộ trình thực hiện

1. **Xác định phạm vi câu hỏi**
   - Người dùng đang hỏi về một huấn luyện viên cụ thể (theo tên/lớp) hay tổng quan đội ngũ giảng dạy của trung tâm.

2. **Tra cứu thông tin huấn luyện viên**
   - Gọi `search_coach_information(query=...)` với tên huấn luyện viên hoặc từ khóa về đội ngũ giảng dạy.

3. **Tổng hợp hồ sơ nổi bật**
   - Trình bày các thông tin đã xác thực: họ tên, chức danh/vai trò, cấp đai/đẳng (Dan), chứng chỉ chuyên môn, thành tích hoặc kinh nghiệm giảng dạy các nhóm tuổi.
   - Tuyệt đối không tự bịa thành tích hay bằng cấp không có trong dữ liệu.

4. **Đề xuất bước tiếp theo**
   - Gợi ý phụ huynh tham khảo thêm cơ sở vật chất hoặc liên hệ đặt lịch gặp/học thử (skill `facilities-and-contact`).
