"""Kiểm soát và bảo vệ tính toàn vẹn của dữ liệu trong quá trình sửa chữa batch (Repair Guard).

Module này ngăn chặn việc mô hình AI âm thầm xóa bỏ các nút (nodes), thuộc tính (properties) hoặc cạnh (edges)
đã được xác thực là hợp lệ trong các lần chạy trước khi chỉ yêu cầu sửa chữa một phần lỗi nhỏ.

Danh sách các hàm / phương thức trong module:
- `RepairGuard._node_key(...)`: Tạo khóa định danh ổn định duy nhất cho một nút (node).
- `RepairGuard.compare(...)`: So sánh mảnh đồ thị cũ và mới, phát hiện các thành phần hợp lệ bị xóa ngoài ý muốn.
- `RepairGuard.extract_validated_baseline(...)`: Trích xuất baseline chỉ gồm các nút, thuộc tính và cạnh hợp lệ từ fragment.
- `RepairGuard._edge_key(...)`: Tạo bộ ba khóa định danh cho một cạnh (edgeName, source, target).
- `RepairGuard._location_was_invalid(...)`: Kiểm tra vị trí trường dữ liệu có nằm trong danh sách lỗi trước đó hay không.
"""

import logging
from typing import Any

from app.schemas import GraphNode, GraphPatchFragment, ValidationIssue
from app.utils.ingestion_helpers import stable_entity_key

logger = logging.getLogger(__name__)


class RepairGuard:
    """
    Bộ bảo vệ dữ liệu sửa chữa nhằm đảm bảo AI không vô tình xóa các tri thức đã được xác thực trước đó.
    """

    @classmethod
    def _node_key(cls, node: GraphNode) -> str:
        """
        Tạo khóa định danh duy nhất cho một GraphNode.

        Args:
            node: Nút đồ thị cần tạo khóa.

        Returns:
            str: Khóa định danh thực thể ổn định (stable key) hoặc temp_id.
        """
        # 1. Nếu node có cấu trúc identity thì tạo stable_entity_key
        if node.identity:
            return stable_entity_key(node.class_name, node.identity)
        # 2. Ngược lại dùng temp_id tạm thời
        return node.temp_id

    @classmethod
    def compare(
        cls,
        *,
        previous_canonical_fragment: GraphPatchFragment,
        new_canonical_fragment: GraphPatchFragment,
        previous_validation_issues: list[dict] | None = None,
    ) -> list[ValidationIssue]:
        """
        So sánh fragment mới được nộp lại với fragment baseline trước đó để phát hiện việc xóa dữ liệu hợp lệ.

        Args:
            previous_canonical_fragment: Fragment chuẩn hóa ở lần thử trước (baseline).
            new_canonical_fragment: Fragment chuẩn hóa mới vừa được sửa chữa.
            previous_validation_issues: Danh sách các lỗi xác thực ở lần thử trước.

        Returns:
            list[ValidationIssue]: Danh sách các lỗi nếu phát hiện tri thức hợp lệ bị xóa bỏ.
        """
        # 1. Tập hợp các vị trí lỗi ở lần chạy trước
        issue_locations = {
            str(item.get("location") or "")
            for item in (previous_validation_issues or [])
        }
        issues: list[ValidationIssue] = []

        # 2. Lập chỉ mục các nút cũ và mới theo stable key
        old_nodes = {
            cls._node_key(node): (index, node)
            for index, node in enumerate(previous_canonical_fragment.nodes)
        }
        new_nodes = {cls._node_key(node): node for node in new_canonical_fragment.nodes}

        # 3. Kiểm tra từng nút cũ xem có bị xóa khi nó không phải nguyên nhân gây lỗi không
        for stable_key, (node_index, old_node) in old_nodes.items():
            node_location = f"nodes.{node_index}"
            if stable_key not in new_nodes:
                # Nếu nút bị xóa nhưng không nằm trong danh sách lỗi trước đó -> Báo lỗi REPAIR_DROPPED_VALID_NODE
                if not cls._location_was_invalid(node_location, issue_locations):
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_NODE",
                            message=(
                                f"Repair removed a previously valid node ({old_node.class_name}) "
                                "that was not part of the validation failure."
                            ),
                            location=node_location,
                            retryable=True,
                        )
                    )
                continue

            # 4. Kiểm tra các thuộc tính (properties) của nút
            new_properties = {
                item.property_name: item for item in new_nodes[stable_key].properties
            }
            for property_index, old_fact in enumerate(old_node.properties):
                if old_fact.property_name in new_properties:
                    continue
                property_location = f"{node_location}.properties.{property_index}"
                # Nếu thuộc tính bị xóa nhưng không bị lỗi ở lần trước -> Báo lỗi REPAIR_DROPPED_VALID_FACT
                if not cls._location_was_invalid(property_location, issue_locations):
                    issues.append(
                        ValidationIssue(
                            code="REPAIR_DROPPED_VALID_FACT",
                            message=(
                                f"Repair removed a previously valid property '{old_fact.property_name}' "
                                f"from {old_node.class_name}."
                            ),
                            location=property_location,
                            retryable=True,
                        )
                    )

        # 5. Kiểm tra các mối quan hệ (edges) cũ xem có bị xóa ngoài ý muốn không
        new_edges = {cls._edge_key(edge) for edge in new_canonical_fragment.edges}
        for edge_index, old_edge in enumerate(previous_canonical_fragment.edges):
            edge_location = f"edges.{edge_index}"
            if cls._edge_key(
                old_edge
            ) not in new_edges and not cls._location_was_invalid(
                edge_location, issue_locations
            ):
                issues.append(
                    ValidationIssue(
                        code="REPAIR_DROPPED_VALID_EDGE",
                        message=(
                            f"Repair removed a previously valid edge '{old_edge.edge_name}' "
                            "that was not part of the validation failure."
                        ),
                        location=edge_location,
                        retryable=True,
                    )
                )

        # 6. Ghi log tổng kết sự khác biệt và số lượng vi phạm
        logger.info(
            "REPAIR_DIFF previousNodes=%s newNodes=%s rejectedRemovals=%s",
            len(previous_canonical_fragment.nodes),
            len(new_canonical_fragment.nodes),
            len(issues),
        )
        return issues

    @classmethod
    def extract_validated_baseline(
        cls,
        fragment: GraphPatchFragment,
        issues: list[dict] | list[ValidationIssue],
    ) -> GraphPatchFragment | None:
        """
        Trích xuất baseline chỉ gồm các nút, thuộc tính và cạnh hợp lệ từ fragment đã biên dịch.

        Args:
            fragment: Fragment vừa được biên dịch.
            issues: Danh sách lỗi xác thực phát sinh.

        Returns:
            GraphPatchFragment | None: Fragment baseline đã lọc sạch lỗi hoặc None nếu không còn gì hợp lệ.
        """
        # 1. Tập hợp các vị trí có lỗi
        issue_locs = {
            str(item.get("location") if isinstance(item, dict) else item.location or "")
            for item in issues
        }
        valid_nodes = []
        valid_node_keys = set()

        # 2. Duyệt qua từng node và lọc thuộc tính hợp lệ
        for idx, node in enumerate(fragment.nodes):
            node_loc = f"nodes.{idx}"
            if cls._location_was_invalid(node_loc, issue_locs):
                valid_props = []
                for prop_idx, prop in enumerate(node.properties):
                    prop_loc = f"{node_loc}.properties.{prop_idx}"
                    if not cls._location_was_invalid(prop_loc, issue_locs):
                        valid_props.append(prop)
                # Chỉ giữ lại node nếu className không bị lỗi và còn thuộc tính hợp lệ
                if valid_props and not any(
                    loc == node_loc or loc == f"{node_loc}.className"
                    for loc in issue_locs
                ):
                    valid_nodes.append(
                        node.model_copy(update={"properties": valid_props})
                    )
                    valid_node_keys.add(node.temp_id)
            else:
                valid_nodes.append(node)
                valid_node_keys.add(node.temp_id)

        # 3. Duyệt qua các cạnh và chỉ giữ lại cạnh không lỗi có cả source và target hợp lệ
        valid_edges = []
        for idx, edge in enumerate(fragment.edges):
            edge_loc = f"edges.{idx}"
            if not cls._location_was_invalid(edge_loc, issue_locs):
                if (
                    edge.source_temp_id in valid_node_keys
                    and edge.target_temp_id in valid_node_keys
                ):
                    valid_edges.append(edge)

        # 4. Nếu không có phần tử nào hợp lệ, trả về None
        if not valid_nodes and not valid_edges:
            return None

        # 5. Đóng gói thành GraphPatchFragment baseline mới
        return GraphPatchFragment(
            ontology_version=fragment.ontology_version,
            nodes=valid_nodes,
            edges=valid_edges,
            coverage=[],
            warnings=[],
        )

    @staticmethod
    def _edge_key(edge: Any) -> tuple[str, str, str]:
        """
        Tạo khóa tuple nhận diện duy nhất cho một cạnh quan hệ.

        Args:
            edge: Cạnh đồ thị (GraphEdge).

        Returns:
            tuple[str, str, str]: Gồm (edge_name, source_temp_id, target_temp_id).
        """
        return edge.edge_name, edge.source_temp_id, edge.target_temp_id

    @staticmethod
    def _location_was_invalid(prefix: str, locations: set[str]) -> bool:
        """
        Kiểm tra xem một đường dẫn vị trí có thuộc phạm vi của các vị trí lỗi trước đó hay không.

        Args:
            prefix: Đường dẫn vị trí cần kiểm tra (ví dụ 'nodes.0.properties.1').
            locations: Tập hợp các đường dẫn vị trí báo lỗi.

        Returns:
            bool: True nếu vị trí trùng khớp hoặc là cha/con của vị trí lỗi, ngược lại False.
        """
        return any(
            location == prefix
            or location.startswith(prefix + ".")
            or prefix.startswith(location + ".")
            for location in locations
            if location
        )


__all__ = ["RepairGuard"]
