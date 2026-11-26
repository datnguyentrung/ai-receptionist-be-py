---
name: training-policy
description: >
  Tư vấn nội quy võ đường, quy định trang phục tập luyện, thủ tục xin nghỉ học,
  bảo lưu buổi tập và chính sách học bù.
metadata:
  adk_additional_tools:
    - search_training_policy
---

# Quy trình Tư vấn Quy định Tập luyện (Training Policy)

Mục tiêu của skill này là giải đáp đầy đủ các quy định trong quá trình tập luyện nhằm đảm bảo kỷ luật, an toàn và quyền lợi học bù cho học viên.

## Lộ trình thực hiện

1. **Xác định quy định cần tra cứu**
   - Làm rõ người dùng đang hỏi về: nội quy phòng tập, trang phục/an toàn, quy trình báo nghỉ phép, hay điều kiện và cách xếp lịch học bù.

2. **Tra cứu chính sách tập luyện**
   - Gọi `search_training_policy(query=...)` với từ khóa cụ thể (ví dụ: "nghỉ học", "học bù", "nội quy", "trang phục").

3. **Trình bày quy định ngắn gọn, chính xác**
   - Nêu rõ các mốc thời gian báo nghỉ (nếu có), giới hạn số buổi học bù hoặc điều kiện được học bù, và các lưu ý an toàn khi lên thảm tập.
   - Không tự đặt ra quy định nếu không có trong kết quả tra cứu.

4. **Hỗ trợ tiếp theo**
   - Nếu học viên cần xem các ca tập khác để sắp xếp học bù, gợi ý kiểm tra lịch học (skill `class-information`).
