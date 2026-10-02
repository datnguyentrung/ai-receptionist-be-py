import os

from google.adk.agents import Agent

from app.schemas.ingestion_schema import (
    BatchExtractionInput,
    GraphPatchFragment,
)

ingestion_extractor_agent = Agent(
    name="ingestion_batch_extractor",
    model=os.getenv(
        "GOOGLE_ADK_MODEL",
        "gemini-3.5-flash-lite",
    ),
    description=(
        "Trích xuất đúng một ingestion batch thành GraphPatchFragment "
        "theo ontology đã được cung cấp."
    ),
    instruction="""
    Bạn chỉ thực hiện nhiệm vụ trích xuất tri thức cho đúng một batch.

    Đầu vào luôn tuân theo BatchExtractionInput và đã chứa:
    - ontologyVersion;
    - các scope được chọn;
    - toàn bộ chunks của batch;
    - ontology đã hợp nhất từ các scope đó.

    Yêu cầu:

    1. Chỉ sử dụng loại thực thể có trong ontology.entityTypes.
    2. Chỉ sử dụng thuộc tính có trong ontology.properties.
    3. Chỉ sử dụng quan hệ có trong ontology.relationships.
    4. Không tự tạo tên loại thực thể, thuộc tính hoặc quan hệ mới.
    5. Mọi thuộc tính được trích xuất phải có bằng chứng nguyên văn từ chunk.
    6. Mọi quan hệ phải có bằng chứng nguyên văn từ chunk.
    7. Mỗi chunk đầu vào phải có đúng một mục coverage.
    8. coverage.decision chỉ được là MAPPED hoặc NOT_RELEVANT.
    9. ontologyVersion của kết quả phải đúng phiên bản ontology trong đầu vào.
    10. Nếu ontology hiện tại không biểu diễn được một khái niệm,
        không tự mở rộng schema. Chỉ trích xuất những gì schema hiện tại hỗ trợ.
    11. Không trả lời giải thích bằng văn bản.
    12. Chỉ trả về dữ liệu đúng GraphPatchFragment.
    """,
    input_schema=BatchExtractionInput,
    output_schema=GraphPatchFragment,
    mode="single_turn",
)
