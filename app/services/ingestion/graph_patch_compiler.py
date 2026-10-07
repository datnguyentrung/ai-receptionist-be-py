"""Biên dịch mảnh đồ thị ngữ nghĩa từ LLM thành mảnh đồ thị chuẩn hóa (Canonical Graph Patch Fragment).

Module này chịu trách nhiệm:
- Nhận dữ liệu trích xuất đồ thị dạng ngữ nghĩa (SemanticGraphPatchFragment) từ mô hình AI.
- Xác định khóa định danh ổn định (stable_entity_key) cho từng thực thể thông qua OntologyIdentityResolver.
- Ánh xạ và kiểm tra tính hợp lệ của các liên kết/cạnh (edges) giữa các thực thể cục bộ hoặc thực thể đã staged trước đó.
- Kiểm tra các ràng buộc miền giá trị nguồn và đích (domain & range) của quan hệ theo OntologyProjection.
- Trả về GraphPatchCompileResult chứa GraphPatchFragment chuẩn hóa hoặc danh sách các lỗi ValidationIssue.

================================================================================
DANH SÁCH CÁC LỚP VÀ PHƯƠNG THỨC TRONG MODULE (GOM THEO NHÓM CHỨC NĂNG):

1. Nhóm Mô hình dữ liệu kết quả (Compile Result Models):
   - GraphPatchCompileResult: Đối tượng chứa kết quả biên dịch gồm fragment chuẩn hóa hoặc danh sách lỗi validation.

2. Nhóm Biên dịch và Chuẩn hóa đồ thị (Graph Patch Compiler):
   - GraphPatchCompiler.compile: Thực hiện toàn bộ quy trình biên dịch từ semantic fragment sang canonical graph fragment.
   - GraphPatchCompiler._resolve_endpoint: Phương thức hỗ trợ tra cứu và phân giải định danh thực thể nguồn/đích của cạnh (edge).
================================================================================
"""

from pydantic import BaseModel, Field

from app.schemas.ingestion_schema import (
    GraphEdge,
    GraphNode,
    GraphPatchFragment,
    OntologyProjection,
    SemanticGraphPatchFragment,
    ValidationIssue,
)
from app.services.ingestion.identity_resolver import (
    IdentityResolutionError,
    OntologyIdentityResolver,
)
from app.services.ingestion.repository import stable_entity_key


# ============================================================================
# 1. NHÓM MÔ HÌNH DỮ LIỆU KẾT QUẢ (COMPILE RESULT MODELS)
# ============================================================================

# ----------------------------------------------------------------------------
# Tên lớp: GraphPatchCompileResult
# Chức năng:
#   - Đóng gói kết quả đầu ra của quá trình biên dịch mảnh đồ thị.
#   - Nếu biên dịch thành công: chứa đối tượng GraphPatchFragment.
#   - Nếu có lỗi phát sinh: chứa danh sách các đối tượng ValidationIssue.
# Thuộc tính:
#   - fragment (GraphPatchFragment | None): Mảnh đồ thị chuẩn hóa sau khi biên dịch.
#   - issues (list[ValidationIssue]): Danh sách các vấn đề/lỗi kiểm tra tính hợp lệ.
# ----------------------------------------------------------------------------
class GraphPatchCompileResult(BaseModel):
    fragment: GraphPatchFragment | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


# ============================================================================
# 2. NHÓM BIÊN DỊCH VÀ CHUẨN HÓA ĐỒ THỊ (GRAPH PATCH COMPILER)
# ============================================================================

# ----------------------------------------------------------------------------
# Tên lớp: GraphPatchCompiler
# Chức năng:
#   - Thực thi logic biên dịch và kiểm tra ràng buộc toàn vẹn của mảnh đồ thị trích xuất ngữ nghĩa.
#   - Phân giải định danh thực thể, gán stable key và liên kết các cạnh quan hệ tương thích với ontology.
# ----------------------------------------------------------------------------
class GraphPatchCompiler:

    # ------------------------------------------------------------------------
    # Tên phương thức: compile
    # Chức năng:
    #   - Biên dịch toàn bộ các nút (nodes) và các cạnh (edges) trong SemanticGraphPatchFragment
    #     thành định dạng chuẩn hóa (canonical GraphPatchFragment).
    #   - Bước 1: Duyệt qua từng nút, sử dụng OntologyIdentityResolver để tính toán identity và tạo stable_entity_key.
    #   - Bước 2: Tạo bản đồ tra cứu định danh (local_entity_keys) cho các tham chiếu tạm thời (temp_id, index, thuộc tính).
    #   - Bước 3: Duyệt qua từng cạnh, phân giải điểm đầu/cuối (source/target) thông qua _resolve_endpoint.
    #   - Bước 4: Kiểm tra tính tương thích về kiểu thực thể nguồn/đích với hợp đồng quan hệ trong ontology (domain & range).
    #   - Bước 5: Tổng hợp và trả về GraphPatchCompileResult (thành công hoặc danh sách lỗi).
    # Input:
    #   - semantic_fragment (SemanticGraphPatchFragment): Dữ liệu trích xuất đồ thị ngữ nghĩa từ AI.
    #   - projection (OntologyProjection): Schema ontology đã chiếu cho phạm vi xử lý hiện tại.
    #   - staged_entities (dict[str, dict] | None): Bản đồ các thực thể đã được staged ở các batch trước để tham chiếu chéo.
    # Output:
    #   - GraphPatchCompileResult: Kết quả biên dịch chứa canonical fragment hoặc danh sách lỗi validation.
    # ------------------------------------------------------------------------
    def compile(
        self,
        semantic_fragment: SemanticGraphPatchFragment,
        projection: OntologyProjection,
        *,
        staged_entities: dict[str, dict] | None = None,
    ) -> GraphPatchCompileResult:
        # Khởi tạo bộ giải quyết định danh thực thể theo ontology projection
        resolver = OntologyIdentityResolver(projection)
        staged_entities = staged_entities or {}
        issues: list[ValidationIssue] = []
        canonical_nodes: list[GraphNode] = []
        local_entity_keys: dict[str, str] = {}

        # Ánh xạ kiểu thực thể của các thực thể đã staged trước đó: stableKey -> className
        node_types: dict[str, str] = {
            item["stableKey"]: item["className"] for item in staged_entities.values()
        }

        # --------------------------------------------------------------------
        # BƯỚC 1: XỬ LÝ VÀ CHUẨN HÓA CÁC NÚT (NODES)
        # --------------------------------------------------------------------
        for node_index, node in enumerate(semantic_fragment.nodes):
            temp_id = node.temp_id if node.temp_id else f"node_{node_index}"
            try:
                # Phân giải định danh thực thể dựa trên thuộc tính và chiến lược identity của class
                identity = resolver.resolve(
                    class_name=node.class_name,
                    properties=node.properties,
                )
            except IdentityResolutionError as exc:
                # Ghi nhận lỗi nếu loại thực thể không tồn tại trong ontology
                if exc.unknown_class:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_ENTITY_TYPE",
                            message=f"Unknown entity type: {node.class_name}",
                            location=f"nodes.{node_index}.className",
                        )
                    )
                else:
                    # Ghi nhận lỗi nếu thiếu các thuộc tính bắt buộc cấu thành định danh
                    for field in exc.missing_fields:
                        issues.append(
                            ValidationIssue(
                                code="IDENTITY_SOURCE_PROPERTY_MISSING",
                                message=(
                                    f"Ontology requires identity field '{field}' for "
                                    f"{node.class_name}, but the semantic fragment does "
                                    "not contain that property."
                                ),
                                location=f"nodes.{node_index}.properties",
                                retryable=True,
                            )
                        )
                continue

            # Tạo stable entity key duy nhất dựa trên className và identity đã phân giải
            key = stable_entity_key(node.class_name, identity)

            # Đăng ký các alias tra cứu vào bản đồ local_entity_keys
            local_entity_keys[temp_id] = key
            local_entity_keys[f"node_{node_index}"] = key
            local_entity_keys[str(node_index)] = key

            # Đăng ký thêm giá trị các thuộc tính dạng chuỗi để hỗ trợ phân giải cạnh linh hoạt
            for prop in node.properties:
                if isinstance(prop.value, str) and prop.value.strip():
                    local_entity_keys[prop.value.strip()] = key
            for ident_val in identity.values():
                if isinstance(ident_val, str) and ident_val.strip():
                    local_entity_keys[ident_val.strip()] = key

            # Lưu loại thực thể theo stable key
            node_types[key] = node.class_name

            # Thêm nút đã chuẩn hóa vào danh sách canonical_nodes
            canonical_nodes.append(
                GraphNode(
                    temp_id=key,
                    class_name=node.class_name,
                    identity=identity,
                    properties=node.properties,
                    evidence=node.evidence,
                    confidence=node.confidence,
                )
            )

        # Nếu có lỗi ở bước xử lý nút, dừng lại và trả về danh sách lỗi ngay
        if issues:
            return GraphPatchCompileResult(issues=issues)

        # --------------------------------------------------------------------
        # BƯỚC 2: XỬ LÝ VÀ CHUẨN HÓA CÁC CẠNH / QUAN HỆ (EDGES)
        # --------------------------------------------------------------------
        canonical_edges: list[GraphEdge] = []

        # Tạo bảng tra cứu chữ ký quan hệ: (technicalName, sourceEntityType, targetEntityType) -> schema
        relationships_by_sig = {
            (
                item["technicalName"],
                item["sourceEntityType"],
                item["targetEntityType"],
            ): item
            for item in projection.relationships
        }

        # Gom nhóm danh sách schema quan hệ theo tên quan hệ (technicalName)
        relationships = {}
        for item in projection.relationships:
            relationships.setdefault(item["technicalName"], []).append(item)

        for edge_index, edge in enumerate(semantic_fragment.edges):
            # Phân giải khóa thực thể nguồn (source) và đích (target)
            source_key = self._resolve_endpoint(
                edge.source_temp_id, local_entity_keys, staged_entities
            )
            target_key = self._resolve_endpoint(
                edge.target_temp_id, local_entity_keys, staged_entities
            )

            # Kiểm tra nếu không tìm thấy thực thể nguồn
            if source_key is None:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_ENTITY_REFERENCE",
                        message=f"Unknown edge source reference: {edge.source_temp_id}",
                        location=f"edges.{edge_index}.sourceTempId",
                        retryable=True,
                    )
                )

            # Kiểm tra nếu không tìm thấy thực thể đích
            if target_key is None:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_ENTITY_REFERENCE",
                        message=f"Unknown edge target reference: {edge.target_temp_id}",
                        location=f"edges.{edge_index}.targetTempId",
                        retryable=True,
                    )
                )

            # Kiểm tra ràng buộc kiểu thực thể nguồn -> đích (Domain and Range) theo ontology
            contracts = relationships.get(edge.edge_name)
            if contracts and source_key and target_key:
                source_type = node_types.get(source_key)
                target_type = node_types.get(target_key)
                if (
                    source_type
                    and target_type
                    and (edge.edge_name, source_type, target_type)
                    not in relationships_by_sig
                ):
                    expected_pairs = [
                        f"{c['sourceEntityType']} -> {c['targetEntityType']}"
                        for c in contracts
                    ]
                    issues.append(
                        ValidationIssue(
                            code="RELATIONSHIP_DOMAIN_RANGE_MISMATCH",
                            message=f"{edge.edge_name} expects {' or '.join(expected_pairs)}",
                            location=f"edges.{edge_index}",
                            retryable=True,
                        )
                    )

            # Nếu cả nguồn và đích đều hợp lệ, tạo cạnh chuẩn hóa
            if source_key and target_key:
                canonical_edges.append(
                    GraphEdge(
                        edge_name=edge.edge_name,
                        source_temp_id=source_key,
                        target_temp_id=target_key,
                        properties={
                            item.property_name: item.value for item in edge.properties
                        },
                        evidence=edge.evidence,
                        confidence=edge.confidence,
                    )
                )

        # Nếu có lỗi ở bước xử lý cạnh, trả về danh sách lỗi
        if issues:
            return GraphPatchCompileResult(issues=issues)

        # --------------------------------------------------------------------
        # BƯỚC 3: ĐÓNG GÓI KẾT QUẢ BIÊN DỊCH THÀNH CÔNG
        # --------------------------------------------------------------------
        return GraphPatchCompileResult(
            fragment=GraphPatchFragment(
                ontology_version=projection.version_id,
                nodes=canonical_nodes,
                edges=canonical_edges,
                coverage=semantic_fragment.coverage,
                warnings=semantic_fragment.warnings,
            )
        )

    # ------------------------------------------------------------------------
    # Tên phương thức: _resolve_endpoint (Static method)
    # Chức năng:
    #   - Phân giải chuỗi định danh đầu mút của cạnh (source hoặc target) thành stable key thực thể.
    #   - Tra cứu trong bản đồ thực thể cục bộ (local_entity_keys) theo temp_id hoặc alias.
    #   - Tra cứu trong bản đồ thực thể đã staged trước đó (staged_entities) nếu có tiền tố 'entity:'.
    # Input:
    #   - value (str): Chuỗi định danh cần phân giải (temp_id, alias chuỗi hoặc 'entity:...').
    #   - local_entity_keys (dict[str, str]): Bản đồ tra cứu định danh thực thể cục bộ trong batch hiện tại.
    #   - staged_entities (dict[str, dict]): Bản đồ tra cứu thực thể đã staged ở các batch trước.
    # Output:
    #   - str | None: Stable entity key nếu phân giải thành công, ngược lại trả về None.
    # ------------------------------------------------------------------------
    @staticmethod
    def _resolve_endpoint(
        value: str,
        local_entity_keys: dict[str, str],
        staged_entities: dict[str, dict],
    ) -> str | None:
        # Tra cứu trực tiếp trong danh sách thực thể cục bộ
        if value in local_entity_keys:
            return local_entity_keys[value]
        # Tra cứu sau khi cắt khoảng trắng thừa
        if isinstance(value, str) and value.strip() in local_entity_keys:
            return local_entity_keys[value.strip()]
        # Tra cứu thực thể đã staged từ các batch trước đó (tiền tố 'entity:')
        if isinstance(value, str) and value.startswith("entity:"):
            staged = staged_entities.get(value)
            return staged["stableKey"] if staged else None
        return None


__all__ = ["GraphPatchCompileResult", "GraphPatchCompiler"]
