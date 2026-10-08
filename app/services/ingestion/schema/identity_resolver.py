"""Phân giải định danh thực thể chuẩn hóa (Canonical Entity Identity) độc quyền từ metadata Ontology.

Module này chịu trách nhiệm:
- Đọc cấu hình chiến lược định danh (identityStrategy) của từng loại thực thể trong OntologyProjection.
- Xác định các trường thuộc tính bắt buộc (required identity fields) để nhận diện một thực thể duy nhất.
- Trích xuất, kiểm tra tính đầy đủ và chuẩn hóa các giá trị định danh từ danh sách PropertyFact.
- Ném ngoại lệ IdentityResolutionError chi tiết khi thiếu trường định danh hoặc loại thực thể không tồn tại.

================================================================================
DANH SÁCH CÁC LỚP VÀ PHƯƠNG THỨC TRONG MODULE (GOM THEO NHÓM CHỨC NĂNG):

1. Nhóm Ngoại lệ xử lý định danh (Identity Exceptions):
   - IdentityResolutionError: Ngoại lệ ném ra khi không tìm thấy loại thực thể hoặc thiếu thuộc tính định danh bắt buộc.

2. Nhóm Bộ phân giải định danh Ontology (Ontology Identity Resolver):
   - OntologyIdentityResolver.__init__: Khởi tạo bộ phân giải và lập chỉ mục hợp đồng thực thể từ OntologyProjection.
   - OntologyIdentityResolver.required_fields: Lấy danh sách các trường thuộc tính bắt buộc cấu thành định danh của class.
   - OntologyIdentityResolver.resolve: Phân giải và chuẩn hóa giá trị định danh từ danh sách PropertyFact thực tế.
   - OntologyIdentityResolver._is_empty: Phương thức tĩnh kiểm tra giá trị thuộc tính có rỗng/None hay không.
   - OntologyIdentityResolver._normalize_identity_value: Phương thức tĩnh chuẩn hóa giá trị thuộc tính định danh.
================================================================================
"""

from typing import Any

from app.schemas import OntologyProjection, PropertyFact

# ============================================================================
# 1. NHÓM NGOẠI LỆ XỬ LÝ ĐỊNH DANH (IDENTITY EXCEPTIONS)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên lớp: IdentityResolutionError
# Chức năng:
#   - Ngoại lệ đại diện cho các lỗi xảy ra trong quá trình phân giải định danh thực thể theo ontology.
#   - Xử lý hai trường hợp lỗi chính:
#     1. Loại thực thể (class_name) không tồn tại trong ontology (unknown_class = True).
#     2. Thiếu các trường thuộc tính bắt buộc để cấu thành định danh (missing_fields có phần tử).
# ----------------------------------------------------------------------------
class IdentityResolutionError(ValueError):
    # ------------------------------------------------------------------------
    # Tên phương thức: __init__
    # Chức năng:
    #   - Khởi tạo đối tượng ngoại lệ và định dạng thông điệp lỗi tương ứng.
    # Input:
    #   - class_name (str): Tên kỹ thuật của loại thực thể gây ra lỗi.
    #   - missing_fields (list[str]): Danh sách tên các trường thuộc tính định danh bị thiếu.
    #   - unknown_class (bool): Cờ đánh dấu loại thực thể hoàn toàn không tồn tại trong ontology (mặc định False).
    # Output:
    #   - Không có (Khởi tạo instance ngoại lệ).
    # ------------------------------------------------------------------------
    def __init__(
        self,
        *,
        class_name: str,
        missing_fields: list[str],
        unknown_class: bool = False,
    ) -> None:
        # Lưu các thuộc tính lỗi để phục vụ xử lý và phân loại lỗi ở tầng gọi
        self.class_name = class_name
        self.missing_fields = missing_fields
        self.unknown_class = unknown_class

        # Xây dựng thông điệp lỗi chi tiết tùy theo nguyên nhân
        message = (
            f"Unknown ontology entity type: {class_name}"
            if unknown_class
            else f"Missing ontology identity fields for {class_name}: {missing_fields}"
        )
        super().__init__(message)


# ============================================================================
# 2. NHÓM BỘ PHÂN GIẢI ĐỊNH DANH ONTOLOGY (ONTOLOGY IDENTITY RESOLVER)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên lớp: OntologyIdentityResolver
# Chức năng:
#   - Đọc hợp đồng định danh (identity contract) từ OntologyProjection.
#   - Phân giải các giá trị thuộc tính của node thành cặp key-value định danh duy nhất cho thực thể.
# ----------------------------------------------------------------------------
class OntologyIdentityResolver:
    # ------------------------------------------------------------------------
    # Tên phương thức: __init__
    # Chức năng:
    #   - Khởi tạo bộ phân giải và xây dựng bản đồ tra cứu nhanh hợp đồng thực thể theo technicalName.
    # Input:
    #   - projection (OntologyProjection): Đối tượng chứa schema ontology đã được chiếu cho scope tương ứng.
    # Output:
    #   - Không có (Khởi tạo instance).
    # ------------------------------------------------------------------------
    def __init__(self, projection: OntologyProjection) -> None:
        # Tạo bảng tra cứu hợp đồng thực thể: technicalName -> entity_type_schema
        self._entity_contracts = {
            item["technicalName"]: item for item in projection.entity_types
        }

    # ------------------------------------------------------------------------
    # Tên phương thức: required_fields
    # Chức năng:
    #   - Truy vấn danh sách các trường thuộc tính bắt buộc để cấu thành định danh của một class.
    #   - Ném ngoại lệ IdentityResolutionError nếu class_name không có trong ontology.
    # Input:
    #   - class_name (str): Tên kỹ thuật của loại thực thể cần tra cứu.
    # Output:
    #   - list[str]: Danh sách tên các trường thuộc tính bắt buộc (identityStrategy.required).
    # ------------------------------------------------------------------------
    def required_fields(self, class_name: str) -> list[str]:
        # Tra cứu hợp đồng của class trong ontology
        contract = self._entity_contracts.get(class_name)
        if contract is None:
            # Ném lỗi nếu class không tồn tại trong schema ontology đã nạp
            raise IdentityResolutionError(
                class_name=class_name,
                missing_fields=[],
                unknown_class=True,
            )

        # Trích xuất chiến lược định danh (identityStrategy) và lấy danh sách các trường bắt buộc
        strategy = contract.get("identityStrategy") or {}
        return list(strategy.get("required") or [])

    # ------------------------------------------------------------------------
    # Tên phương thức: resolve
    # Chức năng:
    #   - Trích xuất và xác thực các thuộc tính thực tế của một thực thể so với yêu cầu định danh của ontology.
    #   - Kiểm tra xem có trường bắt buộc nào bị thiếu hoặc mang giá trị rỗng không.
    #   - Trả về dictionary các trường định danh đã được chuẩn hóa giá trị.
    # Input:
    #   - class_name (str): Tên loại thực thể.
    #   - properties (list[PropertyFact]): Danh sách các sự kiện thuộc tính do AI trích xuất cho thực thể.
    # Output:
    #   - dict[str, Any]: Dictionary ánh xạ từ tên trường định danh sang giá trị định danh đã chuẩn hóa.
    # ------------------------------------------------------------------------
    def resolve(
        self,
        *,
        class_name: str,
        properties: list[PropertyFact],
    ) -> dict[str, Any]:
        # Lấy danh sách các trường bắt buộc cấu thành định danh từ ontology
        required = self.required_fields(class_name)

        # Chuyển đổi danh sách PropertyFact thành dictionary để tra cứu nhanh theo tên thuộc tính
        property_values = {item.property_name: item.value for item in properties}

        # Tìm các trường bắt buộc nhưng không có trong thuộc tính hoặc mang giá trị rỗng
        missing = [
            field
            for field in required
            if field not in property_values or self._is_empty(property_values[field])
        ]

        # Ném ngoại lệ nếu thiếu bất kỳ trường định danh nào
        if missing:
            raise IdentityResolutionError(
                class_name=class_name,
                missing_fields=missing,
            )

        # Trả về dictionary chứa các trường định danh với giá trị đã được chuẩn hóa
        return {
            field: self._normalize_identity_value(property_values[field])
            for field in required
        }

    # ------------------------------------------------------------------------
    # Tên phương thức: _is_empty (Static method)
    # Chức năng:
    #   - Kiểm tra xem một giá trị thuộc tính có bị coi là rỗng hay không (None hoặc chuỗi toàn khoảng trắng).
    # Input:
    #   - value (Any): Giá trị thuộc tính cần kiểm tra.
    # Output:
    #   - bool: True nếu giá trị rỗng/None, ngược lại trả về False.
    # ------------------------------------------------------------------------
    @staticmethod
    def _is_empty(value: Any) -> bool:
        # Giá trị được coi là rỗng nếu là None hoặc là chuỗi rỗng sau khi strip khoảng trắng
        return value is None or (isinstance(value, str) and not value.strip())

    # ------------------------------------------------------------------------
    # Tên phương thức: _normalize_identity_value (Static method)
    # Chức năng:
    #   - Chuẩn hóa giá trị định danh (loại bỏ khoảng trắng thừa ở đầu/cuối nếu là chuỗi ký tự).
    # Input:
    #   - value (Any): Giá trị thuộc tính gốc.
    # Output:
    #   - Any: Giá trị sau khi được chuẩn hóa.
    # ------------------------------------------------------------------------
    @staticmethod
    def _normalize_identity_value(value: Any) -> Any:
        # Cắt bỏ khoảng trắng thừa ở 2 đầu nếu là kiểu chuỗi (string)
        return value.strip() if isinstance(value, str) else value


__all__ = ["IdentityResolutionError", "OntologyIdentityResolver"]
