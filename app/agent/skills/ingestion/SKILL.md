---
name: ingestion
description: >
  Nạp và trích xuất tài liệu (PDF, DOCX, Markdown, TXT) cho trung tâm Taekwondo vào Knowledge Graph
  có nguồn gốc xác thực (source-grounded) thông qua xử lý theo batch bền vững, xác thực ontology,
  ghi dữ liệu tường minh, quản lý vòng đời tài liệu, xóa và khôi phục (rollback).
metadata:
  adk_additional_tools:
    - begin_ingestion
    - get_ingestion_batch
    - submit_ingestion_batch
    - finalize_ingestion
    - fill_ingestion
    - get_ingestion_status
    - load_ontology_scope
    - validate_graph_patch
    - fill_graph_patch
    - delete_document
    - rollback_document_version
---

# Quy trình Nạp và Trích xuất Tri thức Tài liệu Taekwondo (Ingestion Skill)

Bạn (LLM Agent) đóng vai trò điều phối việc **trích xuất ngữ nghĩa** (Semantic Mapping). Các công cụ Python và tầng Service trong hệ thống chịu trách nhiệm phân tích văn bản (Deterministic Parsing), kiểm tra tính hợp lệ (Validation), quản lý định danh (Identity), lưu trữ tạm thời (Staging), ghi dữ liệu bền vững (Persistence vào Neo4j/PostgreSQL), truy vết nguồn gốc (Provenance) và đọc kiểm chứng (Read-back Verification). 
**Tuyệt đối không tự bịa đặt câu lệnh SQL hoặc Cypher.**

Skill này là nguồn sự thật duy nhất của control flow ingestion. Python tools chỉ cung cấp
từng primitive deterministic và không tự chạy vòng lặp batch. Không dùng skill này để trả
lời câu hỏi từ Knowledge Graph; hãy dùng `graph-qa` cho việc hỏi đáp.

---

## 1. Quy tắc an toàn (Safety Rules)

- **Không tự ý ghi dữ liệu**: Tuyệt đối KHÔNG gọi tool `fill_ingestion` trừ khi người dùng yêu cầu rõ ràng việc lưu trữ, ghi dữ liệu, nạp, hoặc nhập tri thức đã trích xuất vào hệ thống (các từ khóa như: *lưu, ghi vào DB, import, persist, save, write*).
- **Không tự ý xóa / rollback**: Tuyệt đối KHÔNG gọi `delete_document` hoặc `rollback_document_version` trừ khi người dùng có yêu cầu tường minh.
- **Không nhầm lẫn trạng thái**: Tuyệt đối KHÔNG thông báo các dữ liệu đang ở trạng thái lưu tạm (`staged`) là dữ liệu đã được ghi chính thức vào Knowledge Graph (`committed`).
- **Trung thực với dữ liệu nguồn**: Tuyệt đối KHÔNG tự suy diễn hoặc bịa đặt cấp đai, huấn luyện viên, lịch học, học phí, ngày tháng, cơ sở vật chất, quy định hoặc các mối quan hệ không có trong văn bản.
- **Bằng chứng trích dẫn nguyên văn**: Thuộc tính `evidence.text` phải được sao chép nguyên văn (verbatim) từng từ từ đoạn văn bản (`chunk`) được tham chiếu.
- **Xử lý tuần tự**: Không chuyển sang batch tiếp theo cho đến khi batch hiện tại được gửi và lưu tạm (`staged`) thành công.

---

## 2. Chi tiết các Tools (Công cụ) và Tầng Service liên quan

| Tên Tool | Công dụng & Trách nhiệm | Tầng Service / Hàm xử lý backend |
| :--- | :--- | :--- |
| `begin_ingestion` | Chuẩn bị tài liệu, chunks/batches và workspace bền vững. | Preprocessing + PostgreSQL repository |
| `get_ingestion_batch` | Trả nội dung batch và canonical context. | PostgreSQL repository |
| `load_ontology_scope` | Tải ontology projection cho batch hiện tại. | Ontology cache |
| `submit_ingestion_batch` | Validate theo `scope_key` của batch và stage fragment. | Ontology registry + stores |
| `finalize_ingestion` | Kiểm tra coverage và tạo readiness fingerprint. | PostgreSQL repository |
| `fill_ingestion` | Commit domain graph, chunks, facts, embeddings và provenance. | Neo4j graph store |
| `get_ingestion_status` | Trả trạng thái workspace bền vững. | PostgreSQL repository |
| `validate_graph_patch` | Xác thực graph patch mà không lưu. | Ontology registry |
| `fill_graph_patch` | **Chặn ghi trực tiếp không có nguồn**: Ngăn chặn hành động ghi trực tiếp vào Graph DB mà không có tài liệu nguồn chứng minh. | Trả về lỗi `SOURCE_DOCUMENT_REQUIRED`. |
| `delete_document` | Vô hiệu hóa tài liệu và gỡ tri thức không còn nguồn active. | Repository + Neo4j graph store |
| `rollback_document_version` | Vô hiệu hóa phiên bản được chỉ định theo provenance. | Repository + Neo4j graph store |

---

## 3. Quy trình thực thi chuẩn (Step-by-Step Workflow)

1. **Khởi tạo quy trình**:
   - Gọi `begin_ingestion(artifact_name, document_key?, scope_hint?)`.
   - Lưu lại `ingestionId` nhận được. Nếu phiên làm việc được tiếp tục lại (resume), hệ thống sẽ duy trì tiến độ trước đó.

2. **Lấy dữ liệu batch**:
   - Dựa vào `nextBatch.batchIndex`, gọi `get_ingestion_batch(ingestion_id, batch_index)` và đọc kỹ toàn bộ nội dung của từng `chunk` trong batch.

3. **Tải Ontology phù hợp**:
   - Chọn phạm vi ontology hẹp nhất phù hợp với nội dung batch và gọi `load_ontology_scope(scope_key)`.
   - Các phạm vi hợp lệ bao gồm: `core`, `course`, `training`, `belt`, `facility`, `finance`, `event`.

4. **Tạo mảnh đồ thị (`GraphPatchFragment`) duy nhất cho batch**:
   - `ontologyVersion`: Phải khớp với phiên bản ontology đã tải.
   - Mỗi nút (`node`): Sử dụng đúng `className` từ ontology và có đầy đủ các trường định danh (`identity`).
   - Mỗi thuộc tính (`property`): Bắt buộc phải có bằng chứng trích xuất (`evidence`) đính kèm.
   - Mỗi quan hệ (`edge`): Bằng chứng phải chứng minh rõ mối quan hệ thực tế giữa hai thực thể, không chỉ đơn thuần là cùng xuất hiện chung trong câu.
   - Độ phủ (`coverage`): Mọi `chunkIndex` trong batch đều phải có quyết định rõ ràng là `MAPPED` (đã trích xuất) hoặc `NOT_RELEVANT` (không liên quan / không chứa tri thức).

5. **Gửi và sửa lỗi batch**:
   - Gọi `submit_ingestion_batch(ingestion_id, batch_index, scope_key, graph_fragment)`.
   - Nếu kết quả trả về các lỗi cấu trúc (`validation issues`), chỉ sửa chữa các dữ kiện bị ảnh hưởng và gửi lại đúng batch đó. **Không khởi động lại toàn bộ quy trình ingestion khi đang sửa lỗi.**

6. **Lặp lại cho đến khi sẵn sàng hoàn tất**:
   - Lặp lại các bước 2 đến 5 cho đến khi hệ thống báo `stage = "ready_to_finalize"`.
   - Sau đó gọi `finalize_ingestion(ingestion_id)`.

7. **Xử lý khi cần sửa chữa sau finalize**:
   - Nếu `finalize_ingestion` trả về `repair_required`, lấy lại và sửa chữa các batch có trong danh sách yêu cầu (`repairBatchIndexes`), sau đó gọi lại `finalize_ingestion`.

8. **Giai đoạn sẵn sàng ghi (`stage = "ready_to_fill"`)**:
   - **Nếu người dùng chỉ yêu cầu trích xuất/kiểm tra**: Dừng lại và báo cáo kết quả trích xuất đã sẵn sàng (Graph chưa bị thay đổi).
   - **Nếu người dùng có yêu cầu ghi/lưu chính thức**: Gọi `fill_ingestion(ingestion_id)` và báo cáo chi tiết số lượng node/edge thực tế đã ghi và kết quả đọc kiểm chứng.

---

## 4. Cấu trúc mẫu của `GraphPatchFragment`

```json
{
  "ontologyVersion": "v1",
  "nodes": [
    {
      "tempId": "course-1",
      "className": "Course",
      "identity": {
        "name": "Lớp Taekwondo nâng cao"
      },
      "properties": [
        {
          "propertyName": "monthlyTuition",
          "value": 1000000,
          "evidence": [
            {
              "source": "chuong-trinh.docx",
              "chunkIndex": 0,
              "section": "Học phí",
              "text": "Học phí: 1.000.000 đồng/tháng"
            }
          ]
        }
      ],
      "evidence": [
        {
          "source": "chuong-trinh.docx",
          "chunkIndex": 0,
          "text": "Lớp Taekwondo nâng cao"
        }
      ]
    }
  ],
  "edges": [],
  "coverage": [
    {
      "chunkIndex": 0,
      "decision": "MAPPED",
      "reason": "Khóa học Taekwondo và thông tin học phí"
    }
  ],
  "warnings": []
}
```

---

## 5. Định dạng phản hồi cuối cùng (Final Response)

Chỉ báo cáo các thông tin thực tế đã được công cụ (Tool) xác nhận:
- `ingestionId` (Mã định danh tiến trình nạp).
- Số lượng batch đã được lưu tạm (`staged batch count`).
- Các cảnh báo hoặc vấn đề sẵn sàng (`readiness issues`) nếu có.
- Trạng thái commit (`commit status`).
- Số lượng Node / Edge đã được ghi vào Neo4j (`persisted node/edge counts`) và kết quả kiểm chứng đọc ngược (`read-back result`).

*Lưu ý: Nếu người dùng không yêu cầu ghi dữ liệu, hãy nêu rõ rằng quá trình trích xuất đã sẵn sàng nhưng Knowledge Graph chưa bị thay đổi.*
