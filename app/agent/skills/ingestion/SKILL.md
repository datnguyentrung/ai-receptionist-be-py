---
name: ingestion

description: >
  Trích xuất, xác thực, lưu tạm tăng dần (incremental staging) và tùy chọn lưu trữ lâu dài (persistence) tri thức Bán hàng Sản phẩm từ các tài liệu kinh doanh được tải lên vào Đồ thị Tri thức Bán hàng Sản phẩm (Product Sales Knowledge Graph). Skill này nắm quyền điều phối toàn bộ quy trình ingestion, quản lý xử lý theo batch, tải schema động, trích xuất ngữ nghĩa, sửa lỗi (repair), hoàn tất (finalization) và lưu trữ dữ liệu.

metadata:
  adk_additional_tools:
    - begin_ingestion
    - get_ingestion_batch
    - submit_ingestion_batch
    - finalize_ingestion
    - fill_ingestion
    - get_ingestion_status

    - load_product_catalog_schema
    - load_business_rules_schema
    - load_campaign_targeting_schema
    - load_customer_recommendation_schema
    - load_sales_enablement_schema
    - load_governance_versioning_schema

    - delete_document
    - validate_graph_patch
    - fill_graph_patch
---

# Ingestion Đồ thị Tri thức Bán hàng Sản phẩm (Product Sales Knowledge Graph)

Bạn nắm quyền điều phối toàn bộ quy trình ingestion (ingestion workflow).

Bạn chịu trách nhiệm quyết định:

- Bước ingestion nào sẽ chạy tiếp theo;
- Những Schema Skill nào cần thiết cho batch hiện tại;
- Cách trích xuất ngữ nghĩa cho batch hiện tại;
- Khi nào batch hiện tại phải được sửa chữa (repair) và thử lại (retry);
- Khi nào tất cả các batch đã sẵn sàng để hoàn tất (finalize);
- Khi nào quá trình lưu trữ lâu dài (persistence) có thể bắt đầu.

Các công cụ Python cung cấp các khả năng tiền định (deterministic) như chuẩn bị tài liệu, quản lý workspace, xác thực (validation), xử lý định danh (identity handling), lưu tạm (staging), quản lý vòng đời (lifecycle) và lưu trữ vào Neo4j (persistence).

Không tái hiện lại các logic tiền định về persistence, staging, identity hoặc validation bên trong luồng suy luận (reasoning).

Bạn cũng đóng vai trò là bộ ánh xạ ngữ nghĩa (semantic mapper).

Không gọi một agent trích xuất khác hoặc một model định tuyến schema (schema-routing model) riêng biệt.

Việc chọn schema, tải schema và trích xuất đồ thị cho một batch là một quy trình ngữ nghĩa liền mạch duy nhất do skill này sở hữu.

Ontology ở chế độ chỉ đọc (read-only) và là nguồn chân lý duy nhất (source of truth) cho:

- Tên định danh kỹ thuật (technical names);
- Các lớp (classes) và thuộc tính (properties);
- Miền giá trị và phạm vi quan hệ (relationship domains & ranges);
- Kiểu dữ liệu (datatypes);
- Quy tắc định danh (identity rules);
- Các ràng buộc lưu trữ (persistence constraints).

Không bao giờ sửa đổi ontology.

Không bao giờ bịa đặt các dữ kiện kinh doanh còn thiếu để thỏa mãn các yêu cầu của ontology.

Một đồ thị chỉ bao phủ một phần tiện lợi của nguồn tài liệu không được coi là một lượt ingestion hoàn chỉnh.


## Kết quả yêu cầu (Requested outcomes)

Đối với yêu cầu nạp mới/nhập/tải/ghi dữ liệu (ingest/import/load/write):

- Thực thi quy trình batch ingestion phân tầng theo từng giai đoạn (staged batch ingestion) được định nghĩa bên dưới;
- Chỉ thực hiện lưu trữ (persist) khi người dùng yêu cầu rõ ràng.

Đối với yêu cầu chỉ trích xuất (extract-only):

- Thực thi cùng quy trình xử lý batch cho đến bước hoàn tất (finalization);
- Không gọi `fill_ingestion`.

Đối với yêu cầu chỉ xác thực (validate-only) trên một graph patch nhỏ do phía gọi cung cấp:

- Sử dụng `validate_graph_patch`;
- Báo cáo các vấn đề xác thực có cấu trúc (structured validation issues);
- Không chạy quy trình ingestion tài liệu trừ khi cần xử lý thêm tài liệu nguồn.

Đối với yêu cầu xóa (delete):

- Sử dụng `delete_document`;
- Không bao giờ mô phỏng thao tác xóa bằng cách ingest một đồ thị rỗng.

Đối với các graph patch nhỏ do phía gọi cung cấp:

- Có thể sử dụng trực tiếp `validate_graph_patch` và `fill_graph_patch`.

Không gọi hoặc mô phỏng các one-shot ingestion runner đã deprecated.
Quy trình trong skill này là nguồn chân lý duy nhất cho luồng điều khiển ingestion tài liệu.


## Quy trình Ingestion bắt buộc (Required ingestion workflow)

Đối với một lượt ingestion tài liệu mới, hãy tuân thủ chính xác quy trình sau.


### 1. Bắt đầu Ingestion (Begin ingestion)

Gọi:

`begin_ingestion(artifact_name)`

Hàm này chuẩn bị tài liệu nguồn, tạo các chunk và batch, khởi tạo ingestion workspace và trả về bản tóm tắt thu gọn của `nextBatch` đầu tiên.

Nếu một uncommitted ingestion workspace đang hoạt động đã tồn tại cho cùng tài liệu đó, `begin_ingestion` sẽ trả về trạng thái workspace hiện tại với `"resumed": true` thay vì phân vùng lại hoặc tạo mới workspace.

Lưu giữ `ingestionId` được trả về cho toàn bộ phiên chạy ingestion.

Nếu khởi tạo thất bại, dừng lại và báo cáo lỗi được trả về.


### 2. Lấy và xử lý batch đang hoạt động (Retrieve and process the active batch)

`nextBatch` được trả về bởi `begin_ingestion`, `submit_ingestion_batch`, hoặc `get_ingestion_status` chỉ là một tóm tắt ngắn gọn. Trước khi trích xuất một batch, hãy gọi:

`get_ingestion_batch(ingestion_id, nextBatch.batchIndex)`

Đọc tất cả các chunk chứa trong `batch` được trả về.

Xử lý toàn bộ batch như một đơn vị trích xuất ngữ nghĩa duy nhất.

Không được chỉ đọc chunk đầu tiên rồi giả định các chunk còn lại thuộc cùng miền schema.

Nếu `batch` trả về có chứa `canonicalGraphContext`, hãy sử dụng nó làm ngữ cảnh về các thực thể liên quan đã được stage từ các batch trước đó.

Canonical graph context là ngữ cảnh ngữ nghĩa mang tính tham khảo (advisory semantic context). Nó không thay thế cơ chế xử lý định danh tiền định (deterministic identity handling) do tầng staging/persistence đảm nhiệm.


### 3. Chọn và tải các Schema Skill linh hoạt (Select and load Schema Skills dynamically)

Việc chọn schema có phạm vi nghiêm ngặt theo từng batch (batch-scoped).

Với mỗi batch mới:

- Chỉ kiểm tra các chunk trong batch đang hoạt động;
- Chọn lại tập hợp schema tối thiểu cần thiết;
- Thông thường chỉ tải 1-2 schema;
- Chỉ tải 3 schema khi batch rõ ràng trải rộng qua 3 miền kiến thức;
- Không được tải quá 3 schema trừ khi quá trình sửa lỗi xác thực (validation repair) yêu cầu;
- Không tải một schema chỉ vì một thực thể khác có tham chiếu tới miền đó;
- Không dựa vào việc chọn schema của batch trước làm bằng chứng cho thấy batch hiện tại cũng cần schema đó;
- Chỉ tải các Schema Skill đã chọn bằng các công cụ `load_<domain>_schema` tương ứng;
- Sử dụng ngay các hợp đồng ontology đã tải để trích xuất batch hiện tại.

Không tạo một pha định tuyến schema riêng biệt hoặc gọi thêm một model thứ hai chỉ để chọn Schema Skill.

Chọn schema, tải schema và trích xuất thuộc về cùng một luồng ngữ nghĩa cho batch đang hoạt động.

Các Schema Skill khả dụng gồm:

- `product-catalog`
  Các sản phẩm ngân hàng, ưu đãi, gói sản phẩm, thuộc tính sản phẩm, phí, lãi suất, quyền lợi, hạn mức và cấu hình sản phẩm.

- `business-rules`
  Quy tắc điều kiện (eligibility rules), chính sách, tiêu chí thẩm định/xét duyệt, ràng buộc kinh doanh, hồ sơ/tài liệu bắt buộc và các điều kiện bán hàng/sản phẩm.

- `campaign-targeting`
  Chiến dịch, chương trình khuyến mãi, mục tiêu tiếp thị, phân khúc khách hàng, ưu đãi khuyến khích và phạm vi áp dụng chiến dịch.

- `customer-recommendation`
  Nhu cầu khách hàng, ngữ cảnh khách hàng, mức độ sử dụng sản phẩm hiện tại, tính phù hợp và các đề xuất/gợi ý.

- `sales-enablement`
  Tri thức bán hàng, kịch bản tư vấn, tình huống bán hàng, câu hỏi thường gặp (FAQs), xử lý từ chối và nội dung tư vấn.

- `governance-versioning`
  Vòng đời phiên bản, phê duyệt, hiệu lực thời gian, trạng thái phát hành, lịch sử thay đổi/kiểm toán và metadata quản trị.

Không tải tất cả Schema Skill trừ khi batch thực sự yêu cầu đầy đủ.


### 4. Trích xuất một phân mảnh batch (Extract one batch fragment)

Sử dụng:

- Các chunk trong batch hiện tại;
- Các Schema Skill đã được tải;
- `canonicalGraphContext` khi được cung cấp;
- Các quy tắc bám sát nguồn tài liệu (source-grounding rules) trong skill này;

Tạo ra chính xác một `GraphPatchFragment` cho batch hiện tại.

Phân mảnh có thể chứa:

- Các node;
- Các edge;
- Thuộc tính trên các node đó;
- Báo cáo độ bao phủ (coverage) cho từng chunk trong batch đang hoạt động;
- Cảnh báo (warnings) khi có sự không chắc chắn thực sự.

Không xây dựng hoặc lưu giữ toàn bộ đồ thị của cả tài liệu trong context của model.

Các phân mảnh batch đã hoàn thành trước đó đã được tích lũy trong tầng lưu tạm (staging). Không tái tạo lại các phân mảnh trước đó chỉ để xử lý batch tiếp theo.


### 5. Gửi batch (Submit the batch)

Gọi:

```text
submit_ingestion_batch(
    ingestion_id,
    batch_index,
    graph_fragment
)
```

Công cụ sẽ thực hiện các tác vụ tiền định như:

- Xác thực phân mảnh (fragment validation);
- Xác thực phạm vi batch (batch-scope validation);
- Xử lý định danh (identity handling);
- Phân rã dữ liệu (decomposition);
- Lưu tạm tăng dần (incremental staging);
- Xử lý các liên kết đang chờ (pending-edge handling);
- Cập nhật trạng thái batch.

Việc submit phân mảnh không phải là một pha trích xuất ngữ nghĩa mới.

Nếu gửi thành công:

- Coi như batch đã được lưu tạm (staged);
- Loại bỏ phân mảnh đã hoàn thành khỏi bộ nhớ suy luận (working reasoning);
- Kiểm tra trạng thái trả về;
- Nếu có một `nextBatch` thu gọn được trả về, lặp lại các bước từ 2 đến 5 cho batch đó.

Không bao giờ chuyển sang batch tiếp theo khi batch hiện tại vẫn chưa được giải quyết dứt điểm.


### 6. Sửa lỗi batch thất bại (Repair a failed batch)

Nếu quá trình trích xuất hoặc gửi báo cáo lỗi có thể thử lại (retryable issue):

- Giữ nguyên trên cùng batch đó;
- Kiểm tra lỗi có cấu trúc (structured error);
- Chỉ sửa chữa các dữ kiện không hợp lệ hoặc chưa đầy đủ;
- Giữ nguyên các dữ kiện hợp lệ không liên quan khác;
- Giữ các Schema Skill liên quan đã tải;
- Chỉ tải thêm Schema Skill khi lỗi chỉ ra rằng thực sự cần thêm một miền ontology khác;
- Không tự động tải tất cả các Schema Skill còn lại;
- Gửi lại phân mảnh đã được sửa.

Ưu tiên sửa chữa tối thiểu thay vì ánh xạ lại toàn bộ batch từ đầu khi phân mảnh trước đó về cơ bản đã hợp lệ.


### 7. Hoàn tất Ingestion (Finalize ingestion)

Khi không còn batch nào, gọi:

`finalize_ingestion(ingestion_id)`

Bước hoàn tất sẽ xác thực toàn bộ trạng thái lưu tạm (staging) tích lũy, bao gồm:

- Tất cả các batch đã được stage;
- Hoàn tất độ bao phủ chunk ở cấp độ toàn bộ tài liệu;
- Xử lý các quan hệ đang chờ (unresolved pending relationships);
- Xử lý các xung đột chưa giải quyết (unresolved conflicts);
- Tính sẵn sàng lưu trữ (persistence readiness).

Không tái cấu trúc hoặc hợp nhất tất cả các phân mảnh batch trước đó trong context của model.

Tầng Staging là nguồn chân lý tích lũy duy nhất cho toàn bộ lượt chạy ingestion.


### 8. Sửa lỗi độ bao phủ cuối cùng khi cần thiết (Repair final coverage when necessary)

Nếu bước hoàn tất trả về `"stage": "repair_required"` với `"ready": false` và danh sách `repairBatchIndexes`:

**TUYỆT ĐỐI KHÔNG gọi `begin_ingestion()` trong quá trình repair.** Việc gọi `begin_ingestion()` trong luồng repair bị nghiêm cấm.

Thực hiện chính xác vòng lặp sửa lỗi sau cho từng chỉ số `idx` trong `repairBatchIndexes`:

1. Gọi `get_ingestion_batch(ingestion_id, idx)` để lấy payload cho batch `idx`.
2. Kiểm tra các chunk trong batch đang hoạt động và chỉ tải tập Schema Skill tối thiểu cần thiết.
3. Trích xuất các dữ kiện có căn cứ từ nguồn hoặc đưa ra quyết định `NOT_RELEVANT` có lý do xác đáng cho các chunk bị thiếu/không hợp lệ.
4. Gửi phân mảnh batch đã sửa đổi bằng `submit_ingestion_batch(ingestion_id, idx, fragment)`.

Khi tất cả các `repairBatchIndexes` đã được trích xuất lại và gửi thành công, gọi lại `finalize_ingestion(ingestion_id)`.

Khi một batch đã lưu tạm trước đó được gửi lại, tầng staging sẽ làm mới và thay thế đóng góp của batch đó thay vì giữ lại một cách mù quáng các dữ kiện cũ đã bị thay thế từ lần gửi trước.

Không tự tạo ra các dữ kiện yêu cầu bởi ontology chỉ để vượt qua kiểm tra readiness.


### 9. Lưu trữ dữ liệu khi được yêu cầu (Persist when requested)

Nếu người dùng yêu cầu lưu trữ và bước hoàn tất báo cáo rằng quá trình ingestion đã sẵn sàng để ghi dữ liệu (fill), gọi:

`fill_ingestion(ingestion_id)`

Hàm này đẩy các dữ kiện đã được xác thực trong staging vào đồ thị miền (domain graph).

Không gọi fill trước khi hoàn tất thành công.

Nếu điều kiện sẵn sàng chưa được đáp ứng, hãy báo cáo các vấn đề readiness chưa được giải quyết thay vì bịa đặt dữ kiện để ép buộc lưu trữ.


### 10. Tiêu chí hoàn thành (Completion criteria)

Đối với chỉ trích xuất (extract-only):

- Hoàn thành nghĩa là mọi batch đều đã được stage thành công;
- Quá trình hoàn tất (finalization) đã chạy;
- Các kiểm tra trích xuất/độ bao phủ ở cấp tài liệu đã hoàn tất.

Đối với ingestion có lưu trữ lâu dài (persisted ingestion):

- Hoàn thành nghĩa là thao tác persistence báo cáo kết quả đã commit thành công;
- Nếu công cụ báo cáo trạng thái kiểm tra/đọc lại (verification/readback status), nó phải chỉ ra thành công.

Không bao giờ báo cáo các trạng thái trung gian như `batching`, retry, `ready_to_finalize`, hoặc trạng thái sẵn sàng (readiness) là một lượt ingestion đã hoàn tất.


## Yêu cầu bắt buộc về Độ bao phủ (Coverage is mandatory)

Mỗi `GraphPatchFragment.coverage` phải chứa chính xác một mục coverage cho mỗi chỉ số chunk trong batch đang hoạt động.

Thiếu coverage cho bất kỳ chunk nào trong batch đang hoạt động đều bị coi là lỗi trích xuất.

Độ bao phủ cấp tài liệu được tích lũy trong tầng ingestion staging và được xác minh bởi `finalize_ingestion` sau khi tất cả các batch đã được stage.

Ví dụ:

```json
"coverage": [
  {
    "chunkIndex": 10,
    "decision": "MAPPED",
    "reason": "Product attributes and pricing"
  },
  {
    "chunkIndex": 11,
    "decision": "MAPPED",
    "reason": "Eligibility conditions"
  },
  {
    "chunkIndex": 12,
    "decision": "NOT_RELEVANT",
    "reason": "Repeated explanatory text with no additional persisted fact"
  }
]
```

Không đánh dấu một chunk là `NOT_RELEVANT` chỉ vì một chunk khác đã tạo một node cùng class nếu chunk sau này bổ sung một dữ kiện lưu trữ riêng biệt.

Nếu một chunk xuất hiện sau chỉ là một ví dụ trùng lặp hoặc giải thích lặp lại và không bổ sung dữ kiện đồ thị riêng biệt nào, `NOT_RELEVANT` là chính xác.

Nếu xác thực báo lỗi `COVERAGE_MISSING` hoặc `COVERAGE_NOT_EVIDENCED`, hãy kiểm tra từng chunk bị ảnh hưởng và thực hiện một trong hai cách:

- Bổ sung một thuộc tính/quan hệ có căn cứ và trích dẫn chính xác chunk đó; hoặc
- Đưa ra quyết định `NOT_RELEVANT` có lý do xác đáng.

Việc chỉ thêm minh chứng node chung chung (generic node evidence) không được coi là một sửa lỗi hợp lệ.


## Ưu tiên các khái niệm Ontology cụ thể thay vì nén chung chung (Prefer specific ontology concepts over generic compression)

Không gom các dữ kiện có thể truy vấn độc lập vào một node tóm tắt ngắn gọn duy nhất.

Sử dụng class/property/edge cụ thể nhất mà ontology đã nạp cho phép.

Khi nguồn và ontology hỗ trợ, hãy phân biệt rõ các khái niệm như:

- Dữ kiện sản phẩm cơ bản, lãi suất chuẩn/cơ sở, biểu phí, kỳ hạn và các điều kiện sản phẩm thông thường -> các thuộc tính của `pskg:BankingProduct` hoặc `pskg:BusinessRule` tương ứng; không tạo một `pskg:ProductOffer` chỉ để chứa biểu lãi suất chuẩn/cơ sở của sản phẩm;
- Một chương trình/ưu đãi có tên riêng làm thay đổi các điều khoản thương mại của một sản phẩm ngân hàng (ví dụ: lãi suất cộng thêm, giảm giá, quyền lợi bổ sung, khoảng thời gian áp dụng ưu đãi, hoặc điều kiện khuyến mãi) -> `pskg:ProductOffer`;
- Một sáng kiến tiếp thị/bán hàng có giới hạn thời gian nhằm quảng bá sản phẩm/ưu đãi và nhắm đến các phân khúc hoặc nhu cầu khách hàng -> `pskg:Campaign`;
- Các nhóm khách hàng -> `pskg:CustomerSegment`;
- Nhu cầu -> `pskg:CustomerNeed`;
- Điều kiện xét duyệt/bán hàng -> `pskg:BusinessRule`;
- Hồ sơ/chứng từ bắt buộc khi nộp đơn -> `pskg:RequiredDocument`;
- Kịch bản tư vấn hoặc xử lý tình huống/từ chối -> `pskg:SalesScript`;
- Tài liệu giải thích không có class cụ thể hơn -> `pskg:SalesKnowledge`.

`ProductOffer` bắt buộc phải được liên kết từ chính xác một `BankingProduct` thông qua `pskg:hasOffer`.
Các liên kết campaign/rule/segment/need như `pskg:offerInCampaign`, `pskg:offerHasRule`, `pskg:offerTargetsSegment`, và `pskg:offerAddressesNeed` đóng vai trò bổ sung ngữ cảnh tùy chọn trừ khi ontology đã nạp quy định khác; chúng không thay thế `pskg:hasOffer`.

Khi batch hiện tại tạo ra một node và tài liệu nguồn trực tiếp khẳng định mối quan hệ của nó với một thực thể đã có sẵn trong `canonicalGraphContext`, hãy xuất mối quan hệ đó trong phân mảnh hiện tại bằng cách sử dụng tham chiếu chuẩn (canonical reference). Không bỏ sót một liên kết mới có minh chứng chỉ vì một đầu liên kết đã được stage bởi một batch trước đó.
Ví dụ: Một `BusinessRule`, `CustomerNeed`, `SalesKnowledge`, `SalesScript`, `Campaign`, hoặc `ProductOffer` mới trích xuất nên được kết nối với sản phẩm/ưu đãi/chiến dịch hiện có bất cứ khi nào batch đang hoạt động hỗ trợ rõ ràng mối quan hệ đó.

`SalesKnowledge` là phương án dự phòng cho nội dung tri thức thực thụ, không phải là nơi chứa tạm để né tránh việc tạo các node ontology cụ thể hơn.

Đối với các mục lặp lại có thể truy vấn độc lập, hãy duy trì độ chi tiết (granularity) hữu ích.

Ví dụ: Các loại chứng từ bắt buộc khác nhau thông thường phải giữ thành các node `RequiredDocument` riêng biệt; các phân khúc khách hàng có tên riêng biệt phải giữ thành các segment riêng biệt.

Không máy móc tạo một node cho mỗi câu văn. Chỉ nhóm lại khi các dữ kiện cùng chia sẻ một danh tính ngữ nghĩa duy nhất.


## Hợp đồng phân mảnh batch (Batch fragment contract)

Mỗi mục bằng chứng (evidence item) phải xác định chính xác chunk đã chuẩn bị và chứa trích đoạn nguyên văn (verbatim source excerpt) từ nguồn.

Không diễn giải lại (paraphrase) bên trong `evidence.text`.

Đối với bảng Markdown, hãy giữ nguyên cấu trúc dòng của nguồn, bao gồm cả các ký tự phân cách pipe `|` ở đầu/cuối và khoảng trắng.

Ví dụ trích dẫn đúng:

`| Tên tài liệu | Hướng dẫn nghiệp vụ sản phẩm Thẻ tín dụng Flexi Rewards |`

Không trích dẫn:

`Tên tài liệu | Hướng dẫn nghiệp vụ sản phẩm Thẻ tín dụng Flexi Rewards`

Cấu trúc phân mảnh mẫu:

```json
{
  "nodes": [
    {
      "tempId": "product-1",
      "className": "pskg:BankingProduct",
      "properties": [
        {
          "propertyName": "pskg:productCode",
          "value": "CC-FLEXI-001",
          "evidence": [
            {
              "source": "example.md",
              "chunkIndex": 1,
              "section": "Product information",
              "text": "Product code: CC-FLEXI-001"
            }
          ]
        },
        {
          "propertyName": "pskg:bankingProductEffectiveFrom",
          "value": "2026-08-01",
          "evidence": [
            {
              "source": "example.md",
              "chunkIndex": 1,
              "section": "Product information",
              "text": "Effective date: 01/08/2026"
            }
          ]
        }
      ],
      "evidence": [
        {
          "source": "example.md",
          "chunkIndex": 1,
          "section": "Product information",
          "text": "Flexi Rewards credit card product"
        }
      ],
      "confidence": 0.98
    }
  ],
  "edges": [],
  "coverage": [
    {
      "chunkIndex": 1,
      "decision": "MAPPED",
      "reason": "Product metadata"
    }
  ],
  "warnings": []
}
```

Mỗi `className`, `propertyName`, và `edgeName` phải là một tên kỹ thuật đầy đủ của ontology theo định dạng `prefix:localName`.

Không hợp lệ:

- `pskg`
- `productCode`
- `pskg:`

Hợp lệ:

- `pskg:BankingProduct`
- `pskg:productCode`
- `pskg:hasEligibilityRule`

Mỗi thuộc tính (property) phải có bằng chứng riêng biệt của nó.

Bằng chứng của node không tự động chứng minh cho tất cả các thuộc tính của node đó.


## Quy tắc bám sát nguồn tài liệu (Source-grounding rules)

- Chỉ xuất ra các dữ kiện được nêu rõ ràng hoặc được suy ra một cách không mơ hồ từ nguồn.

- Bằng chứng phải trỏ đến một `chunkIndex` thực tế, khớp với source/section của nó, và sử dụng văn bản xuất hiện chính xác trong chunk đó.

- Không thay đổi chữ hoa/thường, không gộp khoảng trắng, không bỏ dấu phân cách bảng Markdown, hoặc kết hợp các dòng không liền kề trong văn bản bằng chứng.

- Không bao giờ tự bịa đặt `Published` hoặc trạng thái vòng đời khác để thỏa mãn ontology.

- Không bao giờ tự tạo mã sản phẩm, số phiên bản, ngày tháng, biểu phí, hạn mức, điều kiện, trạng thái hoặc mối quan hệ.

- Mọi trích đoạn bằng chứng quan hệ (edge evidence excerpt) phải chứng minh được mối quan hệ đó, chứ không chỉ đơn thuần cho thấy cả hai thực thể đầu mút cùng xuất hiện trong một chunk.

- Sự cùng xuất hiện của hai thực thể đầu mút mà không có bằng chứng về mối quan hệ là không đủ điều kiện làm bằng chứng cho edge.

- Chỉ chuyển đổi ngày tháng từ nguồn sang chuẩn ISO `YYYY-MM-DD` khi ý nghĩa của nó hoàn toàn rõ ràng, không mơ hồ.

- Không bao giờ phỏng đoán ngày tháng nhạy cảm theo định dạng khu vực (locale-sensitive dates).

- Giữ nguyên thứ tự danh sách khi thứ tự có thể mang ý nghĩa nghiệp vụ.

- Đưa những điểm thực sự không chắc chắn vào `warnings`; không che giấu chúng bằng các giá trị tự bịa đặt.

Mọi giá trị thuộc tính phải được chứng minh bởi bằng chứng được trích dẫn, bao gồm:

- Chuỗi ký tự/danh sách;
- Giá trị boolean;
- Số liệu;
- Biểu phí;
- Hạn mức;
- Điều kiện;
- Mã định danh;
- Trạng thái;
- Phiên bản;
- Ngày tháng.

Nếu việc xác thực trả về `PROPERTY_VALUE_NOT_GROUNDED`, hãy xóa hoặc sửa lại giá trị không có căn cứ đó.

Không bao giờ tự tạo một câu trích dẫn để hợp thức hóa giá trị.

Không xuất `pskg:ruleType` chỉ vì nó bị vắng mặt.

Nếu compiler dẫn xuất một thuộc tính theo cách tiền định, không nhân bản hoặc mâu thuẫn với sự dẫn xuất tiền định đó.


## Định danh và Tái sử dụng chuẩn (Identity and canonical reuse)

Canonical graph context giúp quá trình trích xuất ngữ nghĩa tái sử dụng các thực thể đã được stage từ các batch trước.

Không tạo một thực thể mới chỉ vì cùng một thực thể thực tế xuất hiện trong một batch mới.

Tái sử dụng tham chiếu thực thể chuẩn (canonical entity reference) khi ngữ cảnh được cung cấp chỉ rõ đó là cùng một thực thể.

Tuy nhiên, không ép buộc hai thực thể hợp nhất với nhau khi định danh từ nguồn chỉ ra rằng chúng là riêng biệt.

Quyết định định danh cuối cùng được thực thi bởi logic định danh/staging tiền định, chứ không chỉ dựa vào ngữ cảnh ngữ nghĩa đơn thuần.


## Diễn giải xác thực chính xác (Interpret validation correctly)

`validForExtraction: false` có nghĩa là kết quả trích xuất được xuất ra không hợp lệ hoặc chưa hoàn chỉnh.

Các vấn đề thường gặp bao gồm:

- Sai định dạng schema/tên kỹ thuật;
- Thuật ngữ ontology không xác định;
- Lỗi kiểu dữ liệu/miền giá trị/phạm vi (datatype/domain/range errors);
- Dữ kiện trùng lặp/mâu thuẫn;
- Tham chiếu lơ lửng (dangling references);
- Bằng chứng không khớp với nguồn;
- Giá trị hằng số (literal values) không có căn cứ;
- Độ bao phủ batch/tài liệu chưa đầy đủ.

`validForExtraction: true` đi kèm `validForPersistence: false` có nghĩa là trích xuất bám sát nguồn là chấp nhận được nhưng chưa thể lưu trữ (persist) theo các quy tắc ontology/readiness.

Các vấn đề về tính sẵn sàng (readiness) có thể gồm:

- Thiếu metadata quản trị bắt buộc;
- Các quan hệ bắt buộc chưa khả dụng;
- Định danh chưa được giải quyết;
- Các edge đang chờ chưa được xử lý;
- Xung đột chưa được giải quyết.

Không bịa đặt dữ kiện chỉ để vượt qua kiểm tra readiness.


## Vòng lặp sửa lỗi (Correction loop)

Khi batch hiện tại không vượt qua xác thực:

1. Kiểm tra các trường lỗi có cấu trúc như:
   `code`, `location`, `nodeTempId`, `propertyName`, và `edgeName`.

2. Chỉ sửa các dữ kiện bị ảnh hưởng trong batch hiện tại.

3. Đối với các vấn đề về độ bao phủ (coverage):
   - Xem lại chunk bị ảnh hưởng;
   - Thêm một dữ kiện thuộc tính/quan hệ có căn cứ trích dẫn chunk đó; hoặc
   - Đánh dấu `NOT_RELEVANT` kèm lý do dựa trên nguồn.

4. Đối với các vấn đề về căn cứ nguồn (grounding):
   - Kiểm tra chính xác chunk được trích dẫn;
   - Sửa hoặc loại bỏ các dữ kiện không có căn cứ.

5. Đối với các vấn đề về ontology:
   - Sử dụng các hợp đồng Schema Skill đã nạp;
   - Chỉ nạp thêm Schema Skill khi thực sự cần thêm một miền ontology khác.

6. Giữ nguyên các dữ kiện hợp lệ không liên quan khác.

7. Gửi lại batch đã sửa thông qua `submit_ingestion_batch`.

8. Không chuyển sang batch khác cho đến khi batch hiện tại được stage thành công.

Sau khi tất cả các batch đã được stage, sử dụng `finalize_ingestion` để xác thực toàn bộ trạng thái ingestion tích lũy.


## Cung cấp tài nguyên tăng dần (Progressive disclosure)

Sử dụng `load_skill_resource` khi cần:

- `references/graph-patch-contract.md`
  để biết cấu trúc chính xác của fragment/evidence/coverage;

- `references/validation-policy.md`
  để biết chính sách về coverage, grounding, readiness, identity và hành vi persistence;

- `references/examples.md`
  để xem các ví dụ trích xuất và sửa lỗi.

Không tải tất cả tài liệu tham khảo trừ khi thực sự cần thiết.


## Phản hồi cuối cùng (Final response)

Chỉ tuyên bố những gì thực sự đã hoàn thành.

Đối với quá trình trích xuất/hoàn tất (extraction/finalization), báo cáo:

- Liệu tất cả các batch đã được stage hay chưa;
- Trạng thái trích xuất/xác thực được trả về từ bước finalization;
- Các vấn đề chưa giải quyết về coverage/readiness, nếu có.

Đối với quá trình lưu trữ lâu dài (persistence), báo cáo các trường thực sự được trả về bởi `fill_ingestion`, chẳng hạn như:

- `commitStatus`;
- Số lượng node đã lưu trữ (persisted node count);
- Số lượng edge đã lưu trữ (persisted edge count);
- Trạng thái kiểm tra/đọc lại (verification/readback status), khi được trả về.

Chỉ khẳng định đồ thị đã được ghi vào Neo4j khi kết quả fill xác nhận thao tác commit persistence thành công.

Không mô tả các node ứng viên/lưu tạm (candidate/staged nodes) như là các node miền đã lưu trữ (persisted domain nodes) trước khi bước fill hoàn thành.

Nếu việc đọc lại/kiểm tra xác minh thất bại sau khi commit, hãy báo cáo chính xác trạng thái đó và không khẳng định đã rollback trừ khi tầng persistence xác nhận điều đó một cách rõ ràng.

Nếu một tài liệu có nhiều chunk đã chuẩn bị, không được trình bày một đồ thị rất nhỏ như thể đã hoàn chỉnh trừ khi mọi chunk đều đã vượt qua cổng kiểm soát độ bao phủ ở cấp độ dữ kiện (fact-level coverage gate).
