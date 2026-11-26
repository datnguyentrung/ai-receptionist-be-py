---
name: payment-consultation
description: >
  Tư vấn các phương thức đóng học phí (chuyển khoản, tiền mặt, ví điện tử),
  thời hạn thanh toán và quy định xác nhận biên lai.
metadata:
  adk_additional_tools:
    - search_payment_policy
---

# Quy trình Tư vấn Phương thức Thanh toán (Payment Consultation)

Mục tiêu của skill này là hướng dẫn phụ huynh/học viên hình thức thanh toán học phí thuận tiện, đúng quy định và đúng thời hạn.

## Lộ trình thực hiện

1. **Xác định nội dung thanh toán cần hỏi**
   - Làm rõ người dùng muốn biết hình thức thanh toán (tiền mặt/chuyển khoản/quét QR), thời điểm đóng phí (đầu tháng/đầu khóa), hoặc quy định xuất hóa đơn/biên lai.

2. **Tra cứu quy định thanh toán**
   - Gọi `search_payment_policy(query=...)` để lấy thông tin chính thức về các kênh thanh toán được hỗ trợ, nội dung chuyển khoản mẫu và hạn đóng học phí.

3. **Hướng dẫn chi tiết và an toàn**
   - Trình bày rõ từng phương thức thanh toán, lưu ý cú pháp chuyển khoản (họ tên học viên, lớp học, số điện thoại) nếu có trong dữ liệu.
   - Không tự tạo số tài khoản ngân hàng nếu tool không trả về.

4. **Hỗ trợ tiếp theo**
   - Hỏi xem phụ huynh có cần tính lại học phí theo gói (skill `discount-consultation`) hoặc cần liên hệ trực tiếp bộ phận lễ tân/kế toán để xác nhận giao dịch không.
