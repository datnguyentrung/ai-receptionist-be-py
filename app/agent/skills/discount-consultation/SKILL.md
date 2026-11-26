---
name: discount-consultation
description: >
  Tư vấn các chương trình ưu đãi, khuyến mãi, giảm giá gia đình/nhóm và tính
  toán tổng học phí thực tế theo gói học và ưu đãi áp dụng.
metadata:
  adk_additional_tools:
    - search_discount_policy
    - calculate_tuition
---

# Quy trình Tư vấn Ưu đãi và Tính học phí (Discount Consultation)

Mục tiêu của skill này là giúp phụ huynh nắm bắt các chương trình ưu đãi phù hợp và tính toán chi phí thực đóng một cách chính xác.

## Lộ trình thực hiện

1. **Tra cứu chính sách ưu đãi hiện hành**
   - Gọi `search_discount_policy(query=...)` để kiểm tra các chương trình giảm giá (đăng ký dài hạn, anh/chị/em trong gia đình, đăng ký nhóm, học viên mới).

2. **Thu thập tham số tính học phí (nếu người dùng muốn tính cụ thể)**
   - Xác định: tên gói học phí (`package_name`), số tháng đăng ký (`months`), số học viên (`student_count`) và mã/loại ưu đãi (`discount_code`) nếu có.
   - Nếu thiếu thông tin gói học hoặc thời lượng, hỏi lại ngắn gọn hoặc nêu giả định rõ ràng.

3. **Tính toán học phí qua công cụ**
   - Gọi `calculate_tuition(package_name=..., months=..., discount_code=..., student_count=...)` để tính mức học phí trước giảm, mức giảm và tổng tiền sau ưu đãi.
   - Tuyệt đối không tự tính nhẩm khi đã có công cụ `calculate_tuition`.

4. **Trình bày kết quả minh bạch**
   - Hiển thị chi tiết: học phí gốc, ưu đãi được áp dụng, điều kiện kèm theo và số tiền dự kiến cần đóng.
   - Gợi ý chuyển sang skill `payment-consultation` nếu phụ huynh muốn biết cách thức thanh toán.
