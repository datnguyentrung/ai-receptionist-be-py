---
name: class-recommendation
description: >
  Tư vấn lựa chọn lớp học Taekwondo phù hợp theo độ tuổi, thể lực, mục tiêu tập
  luyện và khung giờ rảnh của học viên.
metadata:
  adk_additional_tools:
    - search_class_knowledge
    - recommend_class
---

# Quy trình Tư vấn Lựa chọn Lớp học (Class Recommendation)

Mục tiêu của skill này là thu thập đủ thông tin đầu vào của học viên để tra cứu danh sách lớp và đưa ra gợi ý lớp học phù hợp nhất.

## Lộ trình thực hiện

1. **Kiểm tra và thu thập thông tin đầu vào**
   - Cần nắm ít nhất **độ tuổi** của học viên.
   - Khai thác thêm **mục tiêu tập luyện** (rèn thể lực, tăng tự tin, tự vệ, thi đấu, phong trào) và **khung giờ/ngày mong muốn** nếu phụ huynh chưa cung cấp.
   - Nếu thiếu độ tuổi, hỏi lại ngắn gọn, lịch sự trước khi gọi `recommend_class`.

2. **Tra cứu thông tin lớp học và gợi ý lớp**
   - Gọi `search_class_knowledge(query=...)` khi cần đối chiếu danh sách lớp, trình độ hoặc đặc điểm từng nhóm lớp.
   - Gọi `recommend_class(age=..., goal=..., schedule_preference=...)` để nhận danh sách lớp được đề xuất theo độ tuổi và nhu cầu.

3. **Trình bày phương án gợi ý**
   - Đưa ra 1–2 lựa chọn lớp phù hợp nhất kèm lý do phù hợp (nhóm tuổi, mục tiêu, cường độ tập, khung giờ).
   - Trình bày mạch lạc để phụ huynh dễ so sánh và ra quyết định.

4. **Đề xuất bước tiếp theo**
   - Gợi ý phụ huynh đăng ký học thử để bé/học viên trải nghiệm thực tế (chuyển sang skill `trial-consultation`).
