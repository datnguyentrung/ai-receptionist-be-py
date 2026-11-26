---
name: facilities-and-contact
description: >
  Tư vấn cơ sở vật chất phòng tập (thảm, dụng cụ bảo hộ, tiện ích) và cung cấp
  thông tin liên hệ chính thức của trung tâm.
metadata:
  adk_additional_tools:
    - search_center_facilities
    - search_contact_information
---

# Quy trình Tư vấn Cơ sở vật chất và Liên hệ (Facilities and Contact)

Mục tiêu của skill này là giúp phụ huynh nắm rõ điều kiện tập luyện an toàn tại phòng tập và các kênh liên hệ chính thức với trung tâm.

## Lộ trình thực hiện

1. **Phân biệt nhu cầu cơ sở vật chất hay thông tin liên hệ**
   - Nếu hỏi về phòng tập, thảm tập, điều hòa, chỗ để xe, phòng thay đồ, dụng cụ bảo hộ → ưu tiên `search_center_facilities`.
   - Nếu hỏi về hotline, Zalo, Facebook, email, kênh liên hệ trực tiếp → ưu tiên `search_contact_information`.
   - Có thể gọi cả hai tool nếu người dùng hỏi kết hợp.

2. **Tra cứu dữ liệu chính thức**
   - Gọi `search_center_facilities(query=...)` và/hoặc `search_contact_information(query=...)`.

3. **Trình bày đầy đủ, dễ theo dõi**
   - Liệt kê các điểm nổi bật về cơ sở vật chất đảm bảo an toàn cho học viên.
   - Hiển thị rõ ràng từng kênh liên hệ (số điện thoại, mạng xã hội, giờ tiếp nhận) theo đúng dữ liệu trả về.

4. **Chốt hỗ trợ**
   - Khuyến khích phụ huynh liên hệ trực tiếp qua kênh phù hợp nhất hoặc để lại câu hỏi nếu cần tư vấn thêm về lớp học và học phí.
