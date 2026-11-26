---
name: center-information
description: >
  Giới thiệu thông tin chung về trung tâm/võ đường Taekwondo, lịch sử hình
  thành, địa chỉ các cơ sở và thời gian hoạt động.
metadata:
  adk_additional_tools:
    - search_center_knowledge
    - search_center_locations
    - search_opening_hours
---

# Quy trình Tư vấn Thông tin Trung tâm (Center Information)

Mục tiêu của skill này là giới thiệu tổng quan về trung tâm, địa điểm các cơ sở và khung giờ hoạt động để phụ huynh/học viên dễ dàng tìm đến.

## Lộ trình thực hiện

1. **Xác định khía cạnh thông tin người dùng cần**
   - Giới thiệu chung / lịch sử / triết lý đào tạo của trung tâm.
   - Địa chỉ cơ sở, phòng tập, chỉ đường.
   - Thời gian mở cửa / giờ làm việc hành chính.

2. **Tra cứu thông tin tương ứng**
   - Gọi `search_center_knowledge(query=...)` khi cần giới thiệu tổng quan, lịch sử, thành tích hoặc tầm nhìn trung tâm.
   - Gọi `search_center_locations(query=...)` khi người dùng hỏi địa chỉ cơ sở hoặc khu vực gần nhất.
   - Gọi `search_opening_hours(query=...)` khi người dùng hỏi giờ mở cửa, ngày làm việc hoặc lịch nghỉ lễ.

3. **Trình bày rõ ràng, thân thiện**
   - Cung cấp chính xác tên trung tâm, địa chỉ đầy đủ từng cơ sở và khung giờ hoạt động theo dữ liệu tra cứu được.
   - Không tự suy đoán địa chỉ hay số nhà.

4. **Gợi ý bước tiếp theo**
   - Mời người dùng tìm hiểu thêm về đội ngũ huấn luyện viên (`coach-information`) hoặc cơ sở vật chất và kênh liên hệ (`facilities-and-contact`).
