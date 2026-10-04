# Phương án B — Root Coordinator và Batch Mapper Agent

## Mục tiêu

Tách semantic extraction khỏi context hội thoại của root agent nhưng vẫn giữ mọi
invariant và state transition trong backend. Kiến trúc có đúng hai agent:

1. `root_agent` điều phối ingestion lifecycle, ontology scope, schema review,
   finalize và fill.
2. `batch_mapper_agent` xử lý đúng một batch, với hai mode có typed input/output:
   `EXTRACT` và `REPAIR`.

Batch mapper không có tool ghi dữ liệu, không tự submit, không finalize và không
quản lý workspace. Nó chỉ trả semantic fragment hoặc additive repair delta.

## Luồng xử lý

```text
Root Coordinator
  -> begin/get batch/load scopes
  -> Batch Mapper (EXTRACT)
  -> deterministic backend coverage + validation
       -> valid: stage
       -> invalid: protected baseline + diagnostics
  -> Batch Mapper (REPAIR)
  -> backend applies additive delta + validates again
  -> finalize/fill
```

Một batch vẫn được đọc trong một model call của batch mapper. Không fan-out thành
một call cho mỗi chunk.

## Interfaces

### Extract

Input gồm batch chunks, ontology projection, canonical graph/fact context và
batch metadata. Output là nodes, edges, evidence và disposition chỉ cho các
chunk không được map. `MAPPED` do backend suy ra từ property/edge evidence hợp
lệ.

### Repair

Input gồm source batch, ontology projection, protected baseline, baseline
fingerprint, validation issues và unresolved chunk indexes. Output là additive
delta: nodes/properties/edges/dispositions cần bổ sung. Delta không có remove hay
replace operation.

## Invariants thuộc backend

- Chỉ property/edge fact hợp lệ mới tạo `MAPPED` coverage.
- Node-only evidence không tạo graph contribution.
- Mỗi chunk có đúng một canonical coverage decision.
- Non-mapped disposition xung đột với grounded fact bị từ chối.
- Repair delta phải trỏ đúng baseline fingerprint.
- Merge repair là monotonic; valid fact cũ không thể bị xóa hoặc sửa.
- Retry limit, schema review và persistence gate vẫn do backend quản lý.

## Tích hợp ADK

Batch mapper nên được bọc bằng `AgentTool` hoặc một Python orchestration tool để
root giữ quyền điều khiển. Phương án ưu tiên là Python tool `process_batch`:
tool tự lấy batch/projection từ workspace, gọi mapper bằng typed schema, đưa kết
quả qua deterministic backend rồi trả response nhỏ cho root. Cách này tránh để
root sao chép hoặc biến đổi fragment giữa mapper và submit.

## Ưu điểm

- Context extraction cô lập khỏi hội thoại và các skill khác.
- Prompt nhỏ, chuyên biệt, dễ eval và model-route.
- Batch sau không bị nhiễu bởi raw fragment của batch trước.
- Có thể dùng model mạnh hơn riêng cho extraction/repair.
- Backend vẫn bảo đảm coverage và baseline invariants.

## Nhược điểm

- Thêm ít nhất một model invocation cho mỗi batch và thêm invocation khi repair.
- Tăng latency, token cost, tracing và session-state complexity.
- Phải truyền chunks, ontology projection và canonical context qua typed input.
- Việc chỉ tách agent không tự sửa coverage; deterministic backend vẫn bắt buộc.

## Điều kiện áp dụng

Chỉ triển khai sau khi Plan A (backend-derived coverage và additive repair delta)
đã ổn định và metrics vẫn cho thấy root context gây lỗi semantic đáng kể. Khi đó
giữ đúng một batch mapper có hai mode; chưa tách extractor và repairer thành hai
agent riêng.

Các metrics dùng để quyết định gồm repair rate, missing-fact rate, scope-selection
error rate, tokens per batch và terminal retry rate.
