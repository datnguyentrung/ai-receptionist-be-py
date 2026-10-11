Sửa chính xác một batch dựa trên protectedBaseline và validationIssues.

NGUYÊN TẮC:
1. TUYỆT ĐỐI KHÔNG sinh lại các node/edge đã có trong protectedBaseline nếu chúng không có trong danh sách lỗi.
2. CHỈ trả về các node/edge trong danh sách `repaired_nodes` và `repaired_edges` liên quan trực tiếp đến validationIssues.
3. Khi sửa bằng chứng (evidence), CHỈ ĐƯỢC PHÉP dùng chunkIndex nằm trong phạm vi batch hiện tại. Tuyệt đối không trỏ về chunk của batch khác.
4. Chỉ đưa tempId vào `dropped_temp_ids` khi validationIssues chỉ rõ chính node đó sai hoặc không có căn cứ. Không được xóa node baseline hợp lệ để giải quyết lỗi coverage hay schema gap của chunk khác.
5. Với SCHEMA_GAP/UNSUPPORTED_BY_ONTOLOGY, kiểm tra lại toàn bộ schema đã nạp trước. Dùng property tổng quát như `description`, `notes`, `age_range` khi chúng biểu diễn đúng ý nghĩa; chỉ giữ schema gap nếu thật sự không có phần tử tương thích.
6. `entityExtractionContract` được tạo động từ ontology đã pin. Với node cần sửa, bổ sung mọi property nằm trong `identityFields` còn thiếu, cùng evidence nguyên văn. Không tự tạo `identity`; engine sẽ suy identity từ properties.
7. `requiredCoverageUpdates` liệt kê các chunk phải được sửa trong output `coverage_updates`. Với mỗi chunk, hoặc thêm fact/edge có evidence từ chính chunk đó, hoặc đổi `MAPPED` thành một decision đúng nội dung. Không để nguyên `MAPPED`.

NẾU GẶP EVIDENCE_NOT_GROUNDED:
- Ưu tiên tìm và sao chép lại đoạn trích nguyên văn từ chunk.
- Giữ nguyên dấu câu, khoảng trắng và ký tự xuống dòng.
- Không tự viết lại nội dung evidence theo cách diễn đạt của mình.
- Nếu bằng chứng quá dài, chọn đoạn ngắn hơn nhưng vẫn đủ chứng minh.
- Không thay đổi dữ kiện đúng chỉ vì evidence bị sai định dạng.

NẾU GẶP MAPPED_WITHOUT_MAPPING:
- Với từng coverage item bị báo lỗi, hoặc thêm một fact/edge có evidence nguyên văn từ đúng chunk đó, hoặc đổi decision thành NO_RELEVANT_FACT, DUPLICATE_EVIDENCE, AMBIGUOUS, hay NOT_RELEVANT theo nội dung thật.
- Không để MAPPED khi không có fact/edge có evidence từ chunk đó.
- Điều kiện, quy định và ngoại lệ có thể được giữ nguyên văn trong `policy.description` hoặc `requirement.description` khi schema có các entity/property này. Không kết luận SCHEMA_GAP chỉ vì logic có nhiều điều kiện.
- Tóm tắt, mục lục hoặc đoạn diễn giải lại nội dung chi tiết phía sau phải đổi thành NO_RELEVANT_FACT hoặc DUPLICATE_EVIDENCE; không cần thêm schema để mô hình hóa chúng.

Không gọi công cụ nào.
