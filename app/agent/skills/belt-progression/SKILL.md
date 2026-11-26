---
name: belt-progression
description: >
  Tư vấn hệ thống cấp đai Taekwondo, ý nghĩa màu đai, lộ trình thăng cấp và quy
  định thi lên đai tại trung tâm.
metadata:
  adk_additional_tools:
    - search_belt_system
    - search_exam_policy
---

# Quy trình Tư vấn Lộ trình Cấp đai (Belt Progression)

Mục tiêu của skill này là giúp học viên và phụ huynh hiểu rõ hệ thống cấp đai, thời gian rèn luyện giữa các cấp và điều kiện dự thi lên đai.

## Lộ trình thực hiện

1. **Xác định trọng tâm câu hỏi**
   - Người dùng đang hỏi về **thứ tự và ý nghĩa hệ thống đai** hay **quy định, điều kiện, lịch thi và nội dung thi lên đai**.

2. **Tra cứu hệ thống đai và quy chế thi**
   - Gọi `search_belt_system(query=...)` để lấy danh sách cấp đai (đai trắng, vàng, xanh, đỏ, đen/đẳng) và lộ trình phát triển.
   - Gọi `search_exam_policy(query=...)` để lấy thông tin thời gian tối thiểu giữa các kỳ thi, điều kiện chuyên cần, nội dung kiểm tra (kỹ thuật, bài quyền, đối kháng, thể lực).

3. **Trình bày lộ trình rõ ràng**
   - Sắp xếp thông tin theo trình tự từ cơ bản đến nâng cao để phụ huynh dễ hình dung quá trình tiến bộ của học viên.
   - Nêu rõ điều kiện cần đạt trước mỗi kỳ thi lên đai theo dữ liệu trả về.

4. **Kết nối thông tin liên quan**
   - Gợi ý tìm hiểu nội quy tập luyện và quy định chuyên cần (skill `training-policy`) nếu cần chuẩn bị cho kỳ thi sắp tới.
