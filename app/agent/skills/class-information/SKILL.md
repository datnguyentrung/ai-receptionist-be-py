---
name: class-information
description: >
  Tư vấn lịch học, ca tập, thời lượng mỗi buổi và thông tin chi tiết của các lớp
  Taekwondo đang mở tại trung tâm.
metadata:
  adk_additional_tools:
    - search_class_schedule
    - search_class_details
---

# Quy trình Tư vấn Thông tin Lớp học (Class Information)

Mục tiêu của skill này là cung cấp chính xác lịch tập luyện, thời lượng buổi học và thông tin chi tiết của từng lớp học.

## Lộ trình thực hiện

1. **Xác định lớp học hoặc khung giờ người dùng quan tâm**
   - Nhận diện tên lớp, nhóm tuổi (thiếu nhi, thiếu niên, người lớn, nâng cao) hoặc ngày/khung giờ (cuối tuần, buổi tối) mà người dùng đang hỏi.

2. **Tra cứu lịch học và chi tiết lớp**
   - Gọi `search_class_schedule(query=...)` để lấy các ngày học trong tuần, giờ bắt đầu – kết thúc và thời lượng mỗi buổi.
   - Gọi `search_class_details(query=...)` khi cần bổ sung thông tin về trình độ, sĩ số, yêu cầu đầu vào hoặc nội dung trọng tâm của lớp.

3. **Trình bày lịch học khoa học**
   - Liệt kê rõ theo từng lớp: Tên lớp, ngày tập, khung giờ, thời lượng và ghi chú trình độ (nếu có).
   - Không tự bịa lịch tập nếu dữ liệu trả về chưa có.

4. **Gợi ý bước tiếp theo**
   - Đề xuất tìm hiểu thêm về chương trình đào tạo của lớp (skill `training-knowledge`) hoặc quy định nghỉ học, học bù (skill `training-policy`).
