---
name: tuition-consultation
description: >
  Tư vấn chính sách học phí, bảng giá các gói học theo tháng/quý/năm và các chi
  phí phát sinh như võ phục, đai và dụng cụ tập luyện.
metadata:
  adk_additional_tools:
    - search_tuition_policy
    - search_tuition_packages
    - search_additional_fees
---

# Quy trình Tư vấn Học phí (Tuition Consultation)

Mục tiêu của skill này là cung cấp thông tin minh bạch, đầy đủ về các gói học phí, chính sách bảo lưu/hoàn phí và chi phí võ phục, dụng cụ.

## Lộ trình thực hiện

1. **Phân loại câu hỏi chi phí**
   - Xác định người dùng đang hỏi về **gói học phí** (theo tháng/quý/năm, theo lớp), **chính sách học phí** (bảo lưu, chuyển lớp, hoàn phí) hay **chi phí phát sinh** (võ phục, đai, bảo hộ).

2. **Tra cứu dữ liệu chính xác**
   - Gọi `search_tuition_packages(query=...)` để lấy mức học phí theo từng gói/lớp.
   - Gọi `search_tuition_policy(query=...)` khi cần giải thích quy định đóng phí, bảo lưu hoặc điều kiện áp dụng.
   - Gọi `search_additional_fees(query=...)` khi người dùng hỏi về tiền võ phục, dụng cụ, lệ phí nhập môn hoặc chi phí phụ trợ.

3. **Trình bày bảng chi phí rõ ràng**
   - Tách bạch rõ **học phí chính khóa** và **chi phí võ phục/dụng cụ** để phụ huynh không bị nhầm lẫn.
   - Ghi đúng đơn vị tiền tệ và chu kỳ đóng (tháng/khóa) theo dữ liệu tra cứu được.

4. **Đề xuất bước tiếp theo**
   - Gợi ý kiểm tra các ưu đãi đang áp dụng hoặc tính tổng học phí dự kiến (chuyển sang skill `discount-consultation`), hoặc hướng dẫn phương thức thanh toán (skill `payment-consultation`).
