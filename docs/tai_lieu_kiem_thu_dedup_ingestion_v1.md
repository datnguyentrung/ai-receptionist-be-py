# THÔNG TIN BỔ SUNG VẬN HÀNH – HỆ THỐNG TAEKWONDO VĂN QUÁN

> **DỮ LIỆU KIỂM THỬ INGESTION / DEDUPLICATION – KHÔNG DÙNG LÀM QUY ĐỊNH VẬN HÀNH THỰC TẾ.**
>
> Tài liệu này cố ý lặp lại một số thông tin đã có trong các tài liệu trước, diễn đạt lại một số thông tin theo cách gần nghĩa, đồng thời bổ sung một số dữ liệu giả lập mới để kiểm tra khả năng tái sử dụng entity/fact/relationship canonical và gắn provenance từ source version mới.

## 1. Thông tin nhận diện Hệ thống

Tên chính thức của đơn vị là **Hệ thống Taekwondo Văn Quán**.

Trong một số nội dung trao đổi ngắn, tên đơn vị có thể được viết là **HT Taekwondo Văn Quán** hoặc **Taekwondo Văn Quán**. Các cách viết này cùng dùng để chỉ Hệ thống Taekwondo Văn Quán trong phạm vi tài liệu kiểm thử này.

## 2. Kênh liên hệ

Hệ thống sử dụng các kênh liên hệ sau:

- **Zalo Hệ thống:** 0369222068.
- **Tên hiển thị Zalo:** HTTaekwondoVănQuán.
- **Đường dây nóng:** 0339998191.
- **Email kiểm thử:** hotro.dedup@example.test.

Email trên là dữ liệu giả lập chỉ dùng để kiểm thử việc tạo một contact channel mới; không phải thông tin liên hệ thực tế.

## 3. Huấn luyện viên và người phụ trách

Một số thành viên được ghi nhận như sau:

| Họ tên | Số điện thoại | Vai trò |
|---|---|---|
| Nguyễn Minh Văn | 0384310398 | Huấn luyện viên cấp cao |
| Nguyễn Trung Đạt | 0352232092 | HLV thực tập |
| Hà Thị Thảo | 033 5326476 | Trợ giảng 3 |

## 4. Quy định đi tập của học viên

Học viên cần có mặt **muộn nhất là trước giờ tập 5 phút**.

Nói cách khác, học viên nên đến sân hoặc phòng tập **tối thiểu 5 phút trước khi buổi tập bắt đầu** để chuẩn bị võ phục và ổn định đội hình.

Nếu không có lý do chính đáng và không xin phép trước theo quy định, **2 lần đi học muộn được tính tương đương 1 buổi nghỉ**.

Đối với học viên chỉ có thể tập **1 buổi mỗi tuần**, thời lượng rèn luyện cần kéo dài **2 quý, khoảng 6–8 tháng**, trước khi đủ thời lượng để tham gia các bước đánh giá tiếp theo.

## 5. Học phí được ghi lại để đối chiếu

### 5.1. Lớp ngoài trời

- Đóng theo quý 3 tháng: **400.000 đồng**, tương ứng 8 buổi/tháng/học viên.
- Học 1 buổi/tuần: **250.000 đồng**, tương ứng 4 buổi/tháng.
- Nộp theo từng tháng: **450.000 đồng**, tương ứng 8 buổi/tháng/học viên.

### 5.2. Lớp phòng tập

- Đóng theo quý 3 tháng: **1.000.000 đồng**, tương ứng 8 buổi/tháng/học viên.
- Học 1 buổi/tuần: **600.000 đồng**, tương ứng 4 buổi/tháng.
- Nộp theo từng tháng: **1.100.000 đồng**, tương ứng 8 buổi/tháng/học viên.

Các mức trên được lặp lại từ tài liệu cũ nhằm kiểm thử khả năng nhận biết fact đã tồn tại; không phải xác nhận đây là mức phí hiện hành.

## 6. Một số quy định dành cho HLV

HLV cần **có mặt trước giờ dạy 25 phút** để chuẩn bị các vấn đề liên quan đến sân, phòng, lớp học và trang thiết bị.

Theo mốc giờ được ghi trong quy định cũ, HLV cần **có mặt trước 17h05**.

Khung giờ điểm danh được ghi là **17:25–17:35**. Trường hợp chưa điểm danh đúng giờ có mức xử lý **10% lương buổi tập** theo tài liệu quy định đã ban hành trước đó.

Một số mức xử lý đi muộn được ghi:

- Sau 17h20: -10% lương buổi dạy.
- Sau 17h30: -30% lương buổi dạy.
- Sau 17h45: -50% lương buổi dạy.

## 7. Kịch bản dữ liệu mới để kiểm thử relationship

Ngoài Zalo và Hotline, trong dữ liệu kiểm thử này Hệ thống được gắn thêm một kênh liên hệ mới:

- Loại kênh: **Email**.
- Giá trị: **hotro.dedup@example.test**.
- Mục đích: chỉ dùng để kiểm thử việc một organization có nhiều `has_contact_channel` khác nhau mà không bị gộp nhầm thành cùng một relationship.

## 8. Kịch bản dữ liệu theo thời gian

Dữ liệu dưới đây là **giả lập** nhằm kiểm thử temporal fact, không phải quy định thực tế.

**Từ ngày 01.01.2027**, giả lập rằng học viên cần có mặt **trước giờ tập 10 phút** thay vì 5 phút.

Hệ thống ingestion cần coi đây là một thông tin có phạm vi thời gian mới, không được ghi đè hoặc hợp nhất sai với quy định 5 phút đang có nếu hai assertion có hiệu lực ở các giai đoạn khác nhau.

## 9. Kịch bản diễn đạt gần nghĩa

Các câu dưới đây cố ý diễn đạt lại cùng một ý để kiểm thử semantic deduplication:

- “Học viên phải đến trước giờ tập ít nhất 5 phút.”
- “Người học cần có mặt tối thiểu 5 phút trước khi buổi tập bắt đầu.”
- “Thời điểm có mặt muộn nhất của học viên là 5 phút trước giờ tập.”

Ba câu trên cùng mô tả một quy định về thời điểm có mặt của học viên và không nên tạo ba knowledge facts độc lập nếu hệ thống xác định chúng tương đương về ngữ nghĩa.

---

*Tài liệu này được tạo riêng cho kiểm thử cross-ingestion deduplication, canonical entity/fact reuse, provenance attachment, relationship object discrimination và temporal/semantic resolution.*
