---
name: human-support
description: >
  Hướng dẫn người dùng kết nối với nhân viên tư vấn hoặc bộ phận hỗ trợ của
  trung tâm khi cần xử lý trực tiếp hoặc ngoài phạm vi tự động.
metadata:
  adk_additional_tools:
    - search_contact_information
---

# Quy trình Hướng dẫn Liên hệ Nhân viên Hỗ trợ (Human Support)

Mục tiêu của skill này là cung cấp nhanh chóng các kênh liên hệ chính thức để người dùng kết nối với nhân viên tư vấn của trung tâm.

## Lộ trình thực hiện

1. **Nhận diện yêu cầu gặp nhân viên hỗ trợ**
   - Người dùng muốn nói chuyện với tư vấn viên, gọi hotline, nhắn Zalo/Facebook, hoặc gặp vấn đề cần xử lý trực tiếp (khiếu nại, trường hợp đặc biệt, xác nhận giao dịch).

2. **Tra cứu kênh hỗ trợ chính thức**
   - Gọi `search_contact_information(query="kênh hỗ trợ liên hệ nhân viên tư vấn")` để lấy thông tin hotline, Zalo, fanpage, email và khung giờ trực.
   - Không tự bịa số điện thoại hoặc đường dẫn liên hệ.

3. **Hướng dẫn kết nối rõ ràng**
   - Trình bày các kênh liên hệ khả dụng, ưu tiên kênh phản hồi nhanh nhất (ví dụ: hotline hoặc Zalo trong giờ làm việc).
   - Gợi ý người dùng chuẩn bị sẵn thông tin cơ bản (tên học viên, độ tuổi hoặc vấn đề cần hỗ trợ) để nhân viên tiếp nhận nhanh hơn.

4. **Xác nhận hỗ trợ thêm**
   - Hỏi người dùng có cần hỗ trợ tra cứu thông tin gì thêm trong lúc chờ kết nối với nhân viên không.
