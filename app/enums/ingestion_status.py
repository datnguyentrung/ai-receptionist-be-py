"""Lifecycle and status enums for ingestion jobs and source document versions."""

from enum import StrEnum


class IngestionJobStatus(StrEnum):
    """Trạng thái vòng đời chi tiết của một tác vụ Ingestion (Job Lifecycle).

    Quy trình chuyển đổi trạng thái tiêu chuẩn:
    RECEIVED -> PARSING -> DEDUPLICATING -> CLEANING -> VALIDATING_INPUT ->
    CHUNKING -> BATCHING -> VALIDATING_GRAPH -> READY -> WRITING -> VERIFYING -> COMMITTED
    (Hoặc chuyển sang FAILED / DELETED / ROLLED_BACK nếu xảy ra lỗi hoặc rollback).
    """

    RECEIVED = "RECEIVED"                  # Đã tiếp nhận yêu cầu nạp tài liệu
    PARSING = "PARSING"                    # Đang phân tích cú pháp tài liệu gốc (Markdown, PDF, Text)
    DEDUPLICATING = "DEDUPLICATING"        # Đang kiểm tra trùng lặp nội dung
    CLEANING = "CLEANING"                  # Đang làm sạch và chuẩn hóa văn bản
    VALIDATING_INPUT = "VALIDATING_INPUT"  # Đang kiểm tra tính hợp lệ của dữ liệu đầu vào
    CHUNKING = "CHUNKING"                  # Đang cắt nhỏ văn bản thành các đoạn (chunks)
    BATCHING = "BATCHING"                  # Đang gom nhóm các chunks thành batch để gửi LLM
    VALIDATING_GRAPH = "VALIDATING_GRAPH"  # Đang kiểm tra tính hợp lệ của đồ thị trích xuất đối chiếu Ontology
    READY = "READY"                        # Dữ liệu đã sẵn sàng để ghi vào cơ sở dữ liệu đồ thị
    WRITING = "WRITING"                    # Đang ghi các nodes/edges vào database/storage
    VERIFYING = "VERIFYING"                # Đang đối soát và xác thực sau khi ghi
    COMMITTED = "COMMITTED"                # Nạp thành công toàn bộ, đã hoàn tất lưu trữ
    FAILED = "FAILED"                      # Quá trình nạp thất bại do lỗi xử lý hoặc validation
    DELETED = "DELETED"                    # Dữ liệu của job đã bị xóa
    ROLLED_BACK = "ROLLED_BACK"            # Đã rollback toàn bộ thay đổi về trạng thái trước đó


class SourceVersionStatus(StrEnum):
    """Trạng thái phiên bản dữ liệu của một tài liệu nguồn (Source Document Version)."""

    PENDING = "PENDING"          # Đang chờ xử lý hoặc đang nạp
    WRITTEN = "WRITTEN"          # Đã ghi tạm thời vào staging
    COMMITTED = "COMMITTED"      # Đã xác nhận áp dụng chính thức vào Knowledge Graph
    FAILED = "FAILED"            # Phiên bản bị lỗi trong quá trình nạp
    SUPERSEDED = "SUPERSEDED"    # Đã bị thay thế bởi phiên bản tài liệu mới hơn
    DELETED = "DELETED"          # Đã bị xóa
    ROLLED_BACK = "ROLLED_BACK"  # Đã bị hoàn tác khỏi hệ thống
