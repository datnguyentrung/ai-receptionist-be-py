---
name: graph-qa
description: >
  Trả lời câu hỏi về Taekwondo bằng multi-strategy retrieval trên Neo4j knowledge graph:
  Cypher chính xác, vector semantic, hoặc hybrid — với citation và abstention nghiêm ngặt.
metadata:
  adk_additional_tools:
    - execute_read_cypher
    - vector_search
    - hybrid_search
    - retrieve_taekwondo_knowledge
    - retrieve_taekwondo_document_history
---

# Taekwondo Graph QA

Skill này là **con đường duy nhất** để trả lời câu hỏi dựa trên tri thức đã lưu trong Knowledge Graph.
Ingestion skill chỉ nạp dữ liệu. Tuyệt đối **không được trả lời từ kiến thức có sẵn** của mô hình.

Tổng số lần gọi công cụ truy xuất tối đa là **2** (1 lần chính + tối đa 1 lần fallback hợp lệ).

---

## Quy trình 6 bước chuẩn

```
[B1: Khám phá Scope] → [B2: Tải Schema Context] → [B3: Lập Kế hoạch Mapping] 
  → [B4: Chọn 1 Chiến lược] → [B5: Truy xuất (max 2)] → [B6: Đánh giá & Trả lời]
```

### Bước 1 — Khám phá Scope Domain
Nếu câu hỏi thuộc về một lĩnh vực cụ thể, gọi công cụ từ skill `schema-context`:
`list_active_schema_scopes()` để lấy danh mục các scope hiện có.
- Chọn `scope_key` **hẹp nhất** phù hợp với câu hỏi (ví dụ: `poomsae`, `competition`, `training`).
- Nếu câu hỏi mơ hồ hoặc bao trùm nhiều miền: đặt `scope_key = None` và không lọc theo scope.

### Bước 2 — Tải Schema Kỹ thuật (Technical Schema)
Với `scope_key` đã chọn ở Bước 1, gọi công cụ từ skill `schema-context`:
`load_active_schema_context(scope_keys=[scope_key])`.
Công cụ trả về:
- `entityTypes`: các kiểu thực thể kèm `neo4jLabel`.
- `properties`: các thuộc tính kèm `neo4jPropertyKey` vật lý.
- `relationships`: các quan hệ kèm `relationshipType` dạng `UPPER_SNAKE_CASE`.

### Bước 3 — Lập kế hoạch Entity / Property / Relation
Dựa trên schema đã nạp ở Bước 2:
- Xác định chính xác các node label, property key vật lý và quan hệ cần truy vấn.
- **Tuyệt đối không** dùng tên ontology namespace (ví dụ: `pskg:...`, `p.pskg:name`) mà phải dùng `neo4jPropertyKey` thuần túy.

### Bước 4 — Chọn 1 trong 3 Chiến lược Truy xuất

| Chiến lược | Công cụ | Dùng khi nào |
|---|---|---|
| **Cypher chính xác** | `execute_read_cypher` | Biết rõ thực thể, quan hệ, cần lọc/đếm/tổng hợp/join chính xác nhiều bảng |
| **Vector ngữ nghĩa** | `vector_search` | Câu hỏi khái niệm, tìm theo ý nghĩa, chưa rõ tên thực thể cụ thể |
| **Hybrid** | `retrieve_taekwondo_knowledge` (hoặc `hybrid_search`) | Câu hỏi phức tạp, cần cả tìm kiếm ngữ nghĩa lẫn mở rộng đồ thị thực thể & facts |

### Bước 5 — Thực thi Truy xuất (Ngân sách nghiêm ngặt)
- **Lần 1 (Truy xuất chính)**: Gọi công cụ đã chọn với tham số tối ưu.
- **Lần 2 (Chỉ khi fallback hợp lệ)**: CHỈ ĐƯỢC PHÉP khi:
  - Lần 1 gặp lỗi cú pháp/schema Cypher có thể sửa được, HOẶC
  - Lần 1 không tìm thấy kết quả nhưng có chiến lược thay thế rõ ràng (ví dụ: Cypher trả về 0 dòng → fallback sang `vector_search`).
  - **Cấm fallback** chỉ vì muốn "kiểm tra thêm" hoặc điểm tương đồng chưa vừa ý.
- **Tuyệt đối không** gọi quá 2 lần truy xuất trong một câu hỏi.

### Bước 6 — Đánh giá Evidence & Sinh Câu trả lời
1. Kiểm tra bằng chứng:
   - Nếu `sufficientEvidence = false` hoặc `rowCount = 0`: Trả lời chính xác nguyên văn `abstentionMessage` ("Không có đủ bằng chứng trong kho tri thức để trả lời câu hỏi này."). Tuyệt đối không tự suy diễn.
2. Nếu đủ bằng chứng (`sufficientEvidence = true` hoặc `rowCount > 0`):
   - Chỉ sử dụng các đoạn trích (`passages`), sự thật (`facts`), hoặc các dòng dữ liệu (`rows`) do công cụ trả về để tổng hợp câu trả lời.
   - Chỉ chèn citation khi người dùng hỏi rõ hoặc khi có xung đột thông tin cần làm rõ nguồn gốc.
   - **Tuyệt đối không chèn mã citation hoặc tên file kỹ thuật vào từng câu một cách không cần thiết**.

---

## Quy tắc Cypher An toàn

Khi sử dụng `execute_read_cypher`:
- Luôn dùng tham số hóa `$param_name` trong `parameters`, không ghép chuỗi thủ công.
- Luôn kèm mệnh đề `LIMIT` (khuyến nghị $\le 50$).
- Chỉ dùng các nhãn node (`neo4jLabel`), thuộc tính (`neo4jPropertyKey`), và quan hệ (`relationshipType`) có trong schema đã tải.
- **Cấm tuyệt đối**: `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`, và dấu chấm phẩy `;`.
- **Cấm tuyệt đối**: Truy cập namespace property như `n.pskg:itemName`.

---

## Quy tắc Grounding & Bằng chứng

- **Không dùng kiến thức nền của LLM**: Mọi thông tin phải xuất phát từ kết quả truy xuất.
- **Xung đột thông tin**: Nếu các đoạn trích mâu thuẫn nhau, trình bày rõ cả hai quan điểm kèm trích dẫn nguồn riêng biệt. Không tự ý chọn một bên là chân lý.
- **Thông tin chưa phân xử**: Với trạng thái `PENDING` hoặc `OTHER`, nêu rõ cả 2 nguồn và ghi rõ chưa xác minh.
- **Hiệu lực dữ liệu**: Fact không có `validFrom` cần ghi chú rõ không thể xác nhận thông tin còn hiệu lực hiện hành hay không.
- **Lịch sử tài liệu**: Khi người dùng hỏi về bản cũ hoặc lịch sử tài liệu, gọi `retrieve_taekwondo_document_history(document_key)` và phân biệt rõ `COMMITTED` với `SUPERSEDED`.
- **Không hiển thị chi tiết nội bộ**: Không hiển thị embedding, stable key, điểm vector thô hoặc vết truy xuất kỹ thuật trừ khi người dùng yêu cầu cụ thể.

---

## Định dạng trả lời

Trả lời ngắn gọn, lịch sự, thân thiện bằng ngôn ngữ của người dùng (tiếng Việt).
Trình bày rõ ràng (dùng đầu mục rõ ràng khi liệt kê nhiều ý).
Không chèn tên file hoặc trích dẫn kỹ thuật vào nội dung trả lời.
