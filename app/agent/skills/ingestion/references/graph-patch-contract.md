# Claim Ledger Batch Extraction Contract

Hợp đồng phân mảnh trích xuất Claim Ledger (`SemanticBatchExtraction`) dành cho mô hình trích xuất.

## Cấu trúc Cấp cao nhất (Top-Level Schema)

```json
{
  "entities": [
    {
      "tempId": "org_1",
      "className": "organization"
    }
  ],
  "chunks": [
    {
      "chunkIndex": 0,
      "claims": [ ... ],
      "noRelevantFactReason": null
    }
  ],
  "warnings": []
}
```

Mỗi batch bắt buộc có đúng một entry trong `chunks[]` cho mỗi chunk đầu vào.

### Evidence contract: source-owned `evidenceUnits`

`get_ingestion_batch` trả về mỗi chunk cùng danh sách `evidenceUnits[]`. Mỗi unit có `evidenceRef`, `chunkIndex`, `kind`, `text` nguyên văn và line range.

Trong payload mới, LLM chỉ chọn ref có sẵn:

```json
"evidence": {
  "chunkIndex": 0,
  "evidenceRef": "chunk:0:line:1"
}
```

Không tự sinh `evidence.text`, không bỏ Markdown, không nối/rút gọn câu và không paraphrase evidence. Backend là bên duy nhất resolve `evidenceRef` thành nguyên văn source. Trường `text` chỉ còn để tương thích payload legacy.

---

## 1. Khai báo Thực thể (SemanticEntity)

```json
{
  "tempId": "org_1",
  "className": "organization",
  "entityRef": null
}
```

- **Thực thể mới trong batch:** Khai báo `tempId` và `className`.
- **Thực thể đã staged từ batch trước:** Khai báo `entityRef` (ví dụ `entity:<stableKey>` từ `canonicalGraphContext`) và `className`.
- Không cần sinh identity hay canonical key — Backend compiler tự tổng hợp identity từ các mapped property claims tương ứng với `identityStrategy.required`.

---

## 2. Claim Nguyên Tử (SemanticClaim)

Mỗi sự kiện / thông tin có nghĩa trong chunk là một claim độc lập:

```json
{
  "claimId": "c_1",
  "statement": "Câu lạc bộ thành lập ngày 15/05/2012",
  "evidence": {
    "chunkIndex": 0,
    "evidenceRef": "chunk:0:line:1"
  },
  "outcome": "MAPPED",
  "mapping": {
    "kind": "PROPERTY",
    "entityRef": "org_1",
    "propertyName": "founded_date",
    "value": "2012-05-15"
  },
  "factRef": null,
  "schemaGap": null,
  "reason": null
}
```

- `claimId`: Chuỗi định danh duy nhất trong batch (ví dụ `c_1`, `c_2`).
- `statement`: Câu phát biểu ngắn gọn về fact được trích xuất.
- `evidence`: Bắt buộc trỏ bằng `evidenceRef` tới một source-owned `evidenceUnit` thuộc chính chunk chứa claim. Backend tự lấy nguyên văn; LLM không tự tạo quote.
- `outcome`: Đúng một trong 4 giá trị: `MAPPED`, `DUPLICATE`, `SCHEMA_GAP`, `AMBIGUOUS`.

---

## 3. Các Loại Mapping và Outcomes

### 3.1 `MAPPED` — Thuộc tính (Property Claim)

```json
{
  "claimId": "c_prop",
  "statement": "Tên câu lạc bộ là Taekwondo Văn Quán",
  "evidence": {"chunkIndex": 0, "evidenceRef": "chunk:0:line:1"},
  "outcome": "MAPPED",
  "mapping": {
    "kind": "PROPERTY",
    "entityRef": "org_1",
    "propertyName": "name",
    "value": "Taekwondo Văn Quán"
  }
}
```

**Deterministic value support:** Với `PROPERTY`, `mapping.value` phải được khôi phục từ evidence đã chọn. Backend cho phép các chuẩn hóa hình thức xác định (ví dụ ngày `15/05/2012` → `2012-05-15`, Markdown/whitespace), nhưng không chấp nhận một value không xuất hiện/có thể khôi phục từ evidence.

### 3.2 `MAPPED` — Quan hệ (Edge Claim)

```json
{
  "claimId": "c_edge",
  "statement": "Phùng Thế Lịch là người sáng lập câu lạc bộ",
  "evidence": {"chunkIndex": 0, "evidenceRef": "chunk:0:line:1"},
  "outcome": "MAPPED",
  "mapping": {
    "kind": "EDGE",
    "edgeName": "founded_by",
    "sourceRef": "org_1",
    "targetRef": "coach_1",
    "properties": {}
  }
}
```

### 3.3 `DUPLICATE` — Fact lặp lại

```json
{
  "claimId": "c_dup",
  "statement": "Trụ sở tại Hà Đông",
  "evidence": {"chunkIndex": 1, "evidenceRef": "chunk:1:line:1"},
  "outcome": "DUPLICATE",
  "factRef": "property:org_vanquan:address:10-tran-phu"
}
```

### 3.4 `SCHEMA_GAP` — Đề xuất mở rộng Schema có cấu trúc

**Property Schema Gap:**
```json
{
  "claimId": "c_gap_prop",
  "statement": "CLB có 6 cơ sở hoạt động",
  "evidence": {"chunkIndex": 0, "evidenceRef": "chunk:0:line:1"},
  "outcome": "SCHEMA_GAP",
  "schemaGap": {
    "kind": "PROPERTY",
    "entityRef": "org_1",
    "technicalName": "facility_count",
    "displayName": "Số lượng cơ sở",
    "dataType": "INTEGER",
    "value": 6,
    "reason": "Ontology chưa có thuộc tính lưu số cơ sở"
  }
}
```

**Relationship Schema Gap:**
```json
{
  "claimId": "c_gap_edge",
  "statement": "CLB hợp tác với trường liên cấp Marie Curie",
  "evidence": {"chunkIndex": 2, "evidenceRef": "chunk:2:line:1"},
  "outcome": "SCHEMA_GAP",
  "schemaGap": {
    "kind": "RELATIONSHIP",
    "technicalName": "partners_with",
    "displayName": "Hợp tác đào tạo với",
    "sourceRef": "org_1",
    "targetRef": "org_marie_curie",
    "cardinality": "MANY_TO_MANY",
    "reason": "Ontology chưa có quan hệ liên kết đào tạo giữa 2 tổ chức"
  }
}
```

### 3.5 `AMBIGUOUS` — Thông tin mơ hồ

```json
{
  "claimId": "c_amb",
  "statement": "Học phí lớp nâng cao có thể thay đổi tùy khóa",
  "evidence": {"chunkIndex": 3, "evidenceRef": "chunk:3:line:1"},
  "outcome": "AMBIGUOUS",
  "reason": "Không đủ thông tin xác định con số học phí cụ thể"
}
```

### 3.6 Chunk không có Fact liên quan

```json
{
  "chunkIndex": 4,
  "claims": [],
  "noRelevantFactReason": "Đoạn văn bản chỉ chứa tiêu đề phân đoạn và lời chào mở đầu"
}
```
