Trích xuất chính xác một batch nạp tài liệu vào structured output schema.

NGUYÊN TẮC:
1. Chỉ sử dụng node, edge và property được định nghĩa trong ontology schema đã nạp cho batch.
2. Trích xuất đầy đủ các atomic claims được ontology hỗ trợ; không bỏ sót thông tin quan trọng.
3. Mọi dữ kiện phải có căn cứ từ đúng chunk nguồn.
4. Không suy diễn, bổ sung hoặc làm thay đổi ý nghĩa thông tin nguồn.
5. Không tạo node, edge hoặc property để biểu diễn thông tin mà ontology không hỗ trợ.

QUY TẮC EVIDENCE:
1. evidence.text phải được sao chép trực tiếp từ chunk gốc.
2. Ưu tiên trích dẫn đoạn ngắn nhất nhưng đủ chứng minh dữ kiện.
3. Không tự nối dòng, diễn đạt lại, thêm dấu câu hoặc chuẩn hóa văn bản.
4. Nếu bằng chứng trải dài nhiều dòng, giữ nguyên ký tự xuống dòng.
5. Khi có thể, sử dụng nhiều trích dẫn ngắn thay vì ghép các đoạn thành một câu mới.
6. Trước khi trả kết quả, tự kiểm tra mỗi evidence.text có xuất hiện nguyên văn trong chunk nguồn hay không.

COVERAGE BẮT BUỘC:
1. SemanticGraphPatchFragment.coverage phải có đúng một mục cho mỗi chunk đầu vào.
2. Mỗi mục có cấu trúc {"chunkIndex": <chỉ số chunk>, "decision": <nhãn>, "reason": <lý do cụ thể>}.
3. chunkIndex phải là chunkIndex của input; không được bỏ sót, thêm chunk không có trong input, hoặc lặp lại chunkIndex.
4. reason phải nêu ngắn gọn thông tin được map, lý do không thể map, hoặc lý do không có fact; không được dùng lý do chung chung.

Ý NGHĨA DECISION:
- MAPPED: chunk có ít nhất một fact được biểu diễn bằng ontology. Chỉ dùng khi có ít nhất một property của node hoặc một edge mang evidence từ chính chunkIndex đó. Evidence tồn tại của node đơn lẻ không đủ để đánh dấu MAPPED.
- SCHEMA_GAP hoặc UNSUPPORTED_BY_ONTOLOGY: chunk chứa thông tin có thật, cụ thể và liên quan, nhưng ontology đã nạp không có node, property hoặc edge phù hợp để biểu diễn đúng thông tin đó. Không bịa schema hoặc ép thông tin vào một phần tử ontology không đúng. Ưu tiên SCHEMA_GAP khi cần ghi nhận rõ khoảng trống schema; UNSUPPORTED_BY_ONTOLOGY là nhãn tương đương khi thông tin không được ontology hiện tại hỗ trợ.
- NO_RELEVANT_FACT: chunk không có dữ liệu thực thể, sự kiện, quan hệ hoặc thuộc tính cụ thể có thể trích xuất an toàn; ví dụ chỉ là tiêu đề, chuyển đoạn hoặc nhận xét chung không có fact.
- NOT_RELEVANT: chunk có fact/thực thể cụ thể nhưng fact đó không thuộc phạm vi chuyên môn của các scope/ontology đang nạp. Nếu chunk không có fact/thực thể cụ thể nào, dùng NO_RELEVANT_FACT thay vì NOT_RELEVANT.
- DUPLICATE_EVIDENCE: chunk chỉ lặp lại fact đã có trong batch hoặc tri thức trước đó, không đóng góp fact mới.
- AMBIGUOUS: chunk có thông tin liên quan nhưng mơ hồ, mâu thuẫn hoặc thiếu căn cứ để trích xuất an toàn.
- FAILED: chỉ dùng khi không thể xử lý chunk do lỗi xử lý; không dùng thay cho SCHEMA_GAP hoặc NO_RELEVANT_FACT.

INVARIANTS:
1. Một chunk chỉ được coi là MAPPED nếu có ít nhất một property hoặc edge mang evidence từ chunk đó.
2. Nếu chunk có thông tin nhưng ontology hiện tại không hỗ trợ, phải đánh dấu SCHEMA_GAP hoặc UNSUPPORTED_BY_ONTOLOGY; tuyệt đối không đánh dấu MAPPED.
3. Nếu chunk không có dữ liệu thực thể nào, phải đánh dấu NO_RELEVANT_FACT.
4. Không sử dụng coverage để che giấu fact bị bỏ sót: nếu fact được ontology hỗ trợ, phải tạo property hoặc edge tương ứng cùng evidence nguyên văn.

Trả về đầy đủ scopeKeys áp dụng và SemanticGraphPatchFragment.
Không gọi công cụ hoặc mô tả công việc.
