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

Mỗi chunk trong batch bắt buộc có đúng 1 mục coverage theo thứ tự ưu tiên kiểm tra:

| Thứ tự / Quyết định | Ý nghĩa & Ràng buộc hợp lệ |
| :--- | :--- |
| **1. UNSUPPORTED_BY_ONTOLOGY** | **Ưu tiên cho tri thức chưa hỗ trợ:** Chunk chứa tri thức nhưng ontology hiện hành chưa hỗ trợ (lịch sử, thành lập, quy mô...). Bắt buộc ghi
eason chi tiết (kích hoạt đề xuất ontology). |
| **2. DUPLICATE_EVIDENCE** | Chunk lặp lại fact đã có ➔ duplicateClaims trỏ tới actRef trong canonicalFactContext, kèm evidence quote grounded và khớp fact. |
| **3. AMBIGUOUS** | Nội dung mập mờ, không đủ cơ sở để trích xuất ➔ Yêu cầu xem xét / làm rõ. |
| **4. NO_RELEVANT_FACT / NOT_RELEVANT** | Chunk cấu trúc chắc chắn không chứa fact (tiêu đề đơn thuần, ký tự phân cách) hoặc ngoài phạm vi. Prose yêu cầu human review. |
| **5. MAPPED** | Chunk thực sự đóng góp ít nhất 1 fact (thuộc tính hoặc quan hệ) ➔ Bắt buộc có chunkIndex trong ít nhất 1 property.evidence hoặc edge.evidence. |
| **6. FAILED** | Lỗi trong quá trình phân tích chunk ➔ Yêu cầu xử lý lại. |

DUPLICATE_EVIDENCE có dạng:

```json
{
  "chunkIndex": 7,
  "decision": "DUPLICATE_EVIDENCE",
  "reason": "Lặp lại số điện thoại đã biết",
  "duplicateClaims": [
    {
      "factRef": "property:<stable-key>:phone:<value-digest>",
      "evidence": {"chunkIndex": 7, "text": "0369 222 068"}
    }
  ]
}
```

Fact đã staged/baseline phải dùng `factRef` do `canonicalFactContext` cung cấp.
Nếu fact được khai báo ngay trong payload hiện tại, dùng một trong hai ref deterministic:

- `local-property:<tempId>:<propertyName>` — property name phải duy nhất trên node;
- `local-edge:<edgeIndex>` — index của edge trong payload hiện tại.

Không tự đoán hoặc tự tạo canonical digest.

---

## 4. Nguyên tắc Sửa lỗi Mẻ (Batch Repair Policy)

1. **Bảo tồn tri thức hợp lệ:** Backend so khớp thực thể dựa trên **Natural Identity** (`className` + thuộc tính định danh). Ở lần trích xuất đầu có thể chọn `tempId`; khi repair phải giữ nguyên `tempId` trong `repairTemplate` để payload ổn định và dễ kiểm chứng.
2. **Không làm mất thuộc tính hợp lệ:** Nếu mẻ trước đã có các thuộc tính hợp lệ (ví dụ `phone`, `address`), lần submit sửa lỗi phải giữ lại các thuộc tính đó trừ khi chính thuộc tính đó bị báo lỗi.
3. **Sửa lỗi Coverage:**
   - Nếu muốn giữ `MAPPED`: bổ sung thuộc tính/quan hệ trích từ chunk đó.
   - Nếu fact đã có: dùng `DUPLICATE_EVIDENCE` với `duplicateClaims` có thể kiểm chứng.
   - Nếu ontology thiếu khả năng biểu diễn: dùng `UNSUPPORTED_BY_ONTOLOGY`; không đổi sang negative label để bypass.

Khi batch ở trạng thái repair, `get_ingestion_batch` trả `repairContext`:

- `protectedBaseline`: canonical snapshot backend đang bảo vệ;
- `repairTemplate`: semantic payload an toàn để làm điểm bắt đầu;
- `validationIssues`: duy nhất các vị trí được phép sửa.

Phải sao chép nguyên `repairTemplate`, kể cả `tempId` và evidence của edge. Chỉ
thêm/sửa phần được `validationIssues` chỉ ra.
