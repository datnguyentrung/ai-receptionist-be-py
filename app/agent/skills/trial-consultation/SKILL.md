---
name: trial-consultation
description: >
  Tư vấn chính sách học thử miễn phí/ưu đãi, hướng dẫn chuẩn bị trước buổi học
  thử và quy trình đăng ký nhập học chính thức.
metadata:
  adk_additional_tools:
    - search_trial_policy
    - search_registration_process
---

# Quy trình Tư vấn Học thử và Đăng ký (Trial Consultation)

Mục tiêu của skill này là giúp phụ huynh/học viên mới nắm rõ chính sách học thử và các bước đăng ký học chính thức tại trung tâm.

## Lộ trình thực hiện

1. **Xác định nhu cầu học thử hay đăng ký chính thức**
   - Phân biệt người dùng đang hỏi về buổi học thử, thủ tục đăng ký nhập học, hoặc cả hai.

2. **Tra cứu chính sách và quy trình**
   - Nếu hỏi về học thử: gọi `search_trial_policy(query=...)` để kiểm tra số buổi học thử, chi phí (nếu có), trang phục cần mặc và cách đặt lịch.
   - Nếu hỏi về đăng ký chính thức: gọi `search_registration_process(query=...)` để lấy các bước ghi danh, thông tin cần cung cấp và xác nhận lịch học.
   - Có thể gọi cả hai tool nếu người dùng quan tâm trọn bộ lộ trình từ học thử đến nhập học.

3. **Hướng dẫn theo từng bước**
   - Trình bày rõ ràng quy trình từng bước (Bước 1, Bước 2, ...), lưu ý đồ dùng cần mang theo (quần áo thoải mái, nước uống, đến sớm trước giờ tập nếu cần).
   - Không tự bịa đặt ưu đãi học thử hay quy trình nếu tool không trả về.

4. **Chốt hỗ trợ**
   - Mời phụ huynh cung cấp khung giờ mong muốn hoặc thông tin liên hệ để được hỗ trợ xếp lịch nhanh nhất.
