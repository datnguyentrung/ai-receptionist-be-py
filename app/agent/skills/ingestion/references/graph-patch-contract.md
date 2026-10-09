# Semantic Graph Patch Contract

Hợp đồng phân mảnh đồ thị ngữ nghĩa (`SemanticGraphPatchFragment`) dành cho mô hình trích xuất.

## Cấu trúc Cấp cao nhất (Top-Level Schema)

```json
{
  "nodes": [ ... ],
  "edges": [ ... ],
  "coverage": [ ... ],
  "warnings": [ ... ]
}
```

> **Lưu ý quan trọng:** Không truyền trường `ontologyVersion` hay `identity` trong Semantic Graph Patch. Backend sẽ tự động gán phiên bản bản thể và suy diễn identity từ chiến lược định danh (identityStrategy).

---

## 1. Nút Thực thể (SemanticGraphNode)

```json
{
  "tempId": "org_vanquan",
  "className": "organization",
  "properties": [
    {
      "propertyName": "name",
      "value": "Hệ Thống Taekwondo Văn Quán",
      "evidence": [
        {
          "source": "he-thong-taekwondo-van-quan-gioi-thieu.md",
          "chunkIndex": 2,
          "text": "Tên chính thức: Hệ Thống Taekwondo Văn Quán"
        }
      ]
    }
  ],
  "evidence": [
    {
      "source": "he-thong-taekwondo-van-quan-gioi-thieu.md",
      "chunkIndex": 0,
      "text": "HỆ THỐNG TAEKWONDO VĂN QUÁN"
    }
  ],
  "confidence": 1.0
}
```

- `tempId`: Mã định danh tạm thời trong mẻ (ví dụ `org_1`, `pers_lich`).
- `className`: Tên loại thực thể trong ontology (ví dụ: `organization`, `person`, `location`, `schedule`).
- `properties`: Danh sách các thuộc tính mang bằng chứng nguyên văn.
- `evidence`: Bằng chứng đề cập thực thể (Entity Mention). Lưu ý: Chỉ `node.evidence` là không đủ để chứng minh fact cho một chunk.

---

## 2. Quan hệ (SemanticGraphEdge)

```json
{
  "edgeName": "located_at",
  "sourceTempId": "org_vanquan",
  "targetTempId": "loc_cs1",
  "properties": [],
  "evidence": [
    {
      "source": "he-thong-taekwondo-van-quan-gioi-thieu.md",
      "chunkIndex": 8,
      "text": "Theo thông tin được Hệ Thống Taekwondo Văn Quán công bố..."
    }
  ],
  "confidence": 1.0
}
```

- `edgeName`: Tên quan hệ trong ontology.
- `sourceTempId`, `targetTempId`: Dùng `tempId` của node mới trong batch hoặc `entity:<stableKey>` từ `canonicalGraphContext`.

---

## 3. Độ phủ Đoạn nguồn (ChunkCoverage)

Mọi chunk trong batch bắt buộc phải có đúng 1 mục `coverage`:

| `decision` | Ý nghĩa | Điều kiện hợp lệ |
| :--- | :--- | :--- |
| `DUPLICATE_EVIDENCE` | Chunk lặp lại thông tin đã được trích xuất ở chunk/mẻ khác | Có fact tương ứng đã tồn tại trong mẻ hoặc các mẻ trước |
| `NO_RELEVANT_FACT` | Chunk có nội dung thuộc miền nhưng không tạo ra fact mới cho graph | Cung cấp `reason` nêu rõ lý do |
| `NOT_RELEVANT` | Chunk nằm ngoài phạm vi tri thức cần nạp | Cung cấp `reason` nêu rõ lý do |
| `UNSUPPORTED_BY_ONTOLOGY` | Chunk chứa tri thức nhưng ontology hiện hành chưa hỗ trợ | Cung cấp `reason` chi tiết (kích hoạt đề xuất ontology) |
| `AMBIGUOUS` | Nội dung mập mờ, không đủ cơ sở để trích xuất | Yêu cầu xem xét / làm rõ |
| `FAILED` | Lỗi trong quá trình phân tích chunk | Yêu cầu xử lý lại |
| `MAPPED` | Chunk thực sự đóng góp ít nhất 1 fact (thuộc tính hoặc quan hệ) | Bắt buộc có `chunkIndex` trong ít nhất 1 `property.evidence` hoặc `edge.evidence` |

---

## 4. Nguyên tắc Sửa lỗi Mẻ (Batch Repair Policy)

1. **Bảo tồn tri thức hợp lệ:** Backend so khớp thực thể dựa trên **Natural Identity** (`className` + thuộc tính định danh), không phụ thuộc vào chuỗi `tempId`. Bạn có thể thoải mái đặt `tempId` theo ngữ cảnh.
2. **Không làm mất thuộc tính hợp lệ:** Nếu mẻ trước đã có các thuộc tính hợp lệ (ví dụ `phone`, `address`), lần submit sửa lỗi phải giữ lại các thuộc tính đó trừ khi chính thuộc tính đó bị báo lỗi.
3. **Sửa lỗi Coverage:**
   - Nếu muốn giữ `MAPPED`: bổ sung thuộc tính/quan hệ trích từ chunk đó.
   - Nếu chunk không sinh fact mới: chuyển sang `DUPLICATE_EVIDENCE`, `NO_RELEVANT_FACT`, hoặc `NOT_RELEVANT`.
