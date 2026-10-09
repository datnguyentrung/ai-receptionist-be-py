"""Bộ ghi log thời gian thực và chi tiết cho quy trình Ingestion."""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.rate_limiter import global_rate_limiter

LOG_FILE = Path(__file__).resolve().parents[2] / "docs" / "log.txt"


def reset_log_file() -> None:
    """Xóa trắng file log khi khởi động một phiên làm việc mới."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"# Ingestion Real-time Logs (Started at {now_str})\n\n")
    except Exception as e:
        print(f"Failed to reset log file: {e}")


def _json_str(val: Any) -> str:
    """Serialize giá trị thành chuỗi JSON an toàn không thoát ký tự Unicode."""
    return json.dumps(val, ensure_ascii=False)


def _format_merged_ontology(scope_data: dict[str, Any]) -> list[str]:
    """1. Định dạng chi tiết nội dung lược đồ hợp nhất sau load_ontology_scopes."""
    lines = ["  📋 NỘI DUNG LƯỢC ĐỒ HỢP NHẤT (MERGED ONTOLOGY):"]
    if not scope_data:
        lines.append("     (Không có dữ liệu schema)")
        return lines

    scope_keys = scope_data.get("scopeKeys") or [scope_data.get("scopeKey")]
    lines.append(
        f"     • Scopes: {scope_keys} | Phiên bản: {scope_data.get('version')} (Hash: {scope_data.get('digest')})"
    )

    # Entity Types
    entity_types = scope_data.get("entityTypes", [])
    lines.append(f"     • Loại Thực Thể / Entity Types ({len(entity_types)}):")
    for et in entity_types:
        name = et.get("technicalName", et.get("name", "Unknown"))
        id_strat = et.get("identityStrategy") or {}
        req_id = id_strat.get("required", [])
        lines.append(
            f"       - [{name}] (Định danh bắt buộc: {req_id if req_id else 'none'})"
        )

    # Properties
    properties = scope_data.get("properties", [])
    lines.append(f"     • Thuộc Tính / Properties ({len(properties)}):")
    for prop in properties:
        et_name = prop.get("entityType", "*")
        p_name = prop.get("technicalName", prop.get("name", "Unknown"))
        d_type = prop.get("dataType", "ANY")
        req = "bắt buộc" if prop.get("required") else "tùy chọn"
        lines.append(f"       - {et_name}.{p_name} ({d_type}, {req})")

    # Relationships
    relationships = scope_data.get("relationships", [])
    lines.append(f"     • Quan Hệ / Relationships ({len(relationships)}):")
    for rel in relationships:
        r_name = rel.get("technicalName", rel.get("name", "Unknown"))
        src = rel.get("sourceEntityType", "?")
        tgt = rel.get("targetEntityType", "?")
        lines.append(f"       - [{r_name}]: {src} -> {tgt}")

    return lines


def _format_extract_result(fragment: dict[str, Any]) -> list[str]:
    """2 & 3. Định dạng EXTRACT_RESULT bao gồm đầy đủ node, edge, properties, bằng chứng và coverage."""
    lines = ["  📊 EXTRACT_RESULT:"]
    raw_nodes = fragment.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    raw_edges = fragment.get("edges")
    edges = raw_edges if isinstance(raw_edges, list) else []
    raw_cov = fragment.get("coverage")
    coverage = raw_cov if isinstance(raw_cov, list) else []

    # Nodes
    lines.append(f"     • Nodes ({len(nodes)}):")
    for n in nodes:
        c_name = n.get("className", "Unknown")
        t_id = n.get("tempId", "?")
        ident = n.get("identity", {})
        lines.append(f"       - [{c_name}] tempId={t_id}, identity={_json_str(ident)}")
        props = n.get("properties", [])
        if props:
            lines.append(f"         Thuộc tính ({len(props)}):")
            for p in props:
                ev_str = ""
                ev_list = p.get("evidence", [])
                if ev_list:
                    ev_items = [
                        f'Chunk #{ev.get("chunkIndex")}: "{ev.get("text", "").strip()[:80]}"'
                        for ev in ev_list
                    ]
                    ev_str = f" [Bằng chứng: {'; '.join(ev_items)}]"
                lines.append(
                    f"           * {p.get('propertyName')} = {_json_str(p.get('value'))}{ev_str}"
                )
        evs = n.get("evidence", [])
        if evs:
            ev_items = [
                f'Chunk #{ev.get("chunkIndex")}: "{ev.get("text", "").strip()[:80]}"'
                for ev in evs
            ]
            lines.append(f"         Bằng chứng thực thể: {'; '.join(ev_items)}")

    # Edges
    lines.append(f"     • Edges ({len(edges)}):")
    for e in edges:
        e_name = e.get("edgeName", "Unknown")
        src = e.get("sourceTempId", "?")
        tgt = e.get("targetTempId", "?")
        lines.append(f"       - [{e_name}]: {src} -> {tgt}")
        props = e.get("properties", {})
        if props:
            lines.append(f"         Properties: {_json_str(props)}")
        evs = e.get("evidence", [])
        if evs:
            ev_items = [
                f'Chunk #{ev.get("chunkIndex")}: "{ev.get("text", "").strip()[:80]}"'
                for ev in evs
            ]
            lines.append(f"         Bằng chứng quan hệ: {'; '.join(ev_items)}")

    # Coverage
    lines.append(f"     • Coverage ({len(coverage)} Chunks):")
    for c in coverage:
        idx = c.get("chunkIndex")
        dec = c.get("decision", "UNKNOWN")
        reason = c.get("reason", "")
        lines.append(f"       - Chunk [{idx}] -> {dec} (Lý do: {reason})")

    return lines


def _compute_and_format_repair_diff(
    prev_fragment: dict[str, Any] | None,
    new_fragment: dict[str, Any],
    prev_issues: list[Any] | None,
    validation_attempts: int = 1,
) -> list[str]:
    """2. Định dạng REPAIR_DIFF chỉ ra giữ, sửa, xóa, và lý do sửa."""
    lines = [f"  🔧 REPAIR_DIFF (Lần sửa #{validation_attempts}):"]

    # 1. Lý do sửa
    if prev_issues:
        lines.append("     • Lý do cần sửa (Lỗi xác thực mẻ trước):")
        for iss in prev_issues:
            if isinstance(iss, dict):
                lines.append(
                    f"       - [{iss.get('code')}] {iss.get('message')} (vị trí: {iss.get('location')})"
                )
            else:
                lines.append(f"       - {iss}")
    else:
        lines.append("     • Lý do cần sửa: Điều chỉnh hoặc làm mịn kết quả trích xuất")

    if not prev_fragment:
        lines.append("     (Không có fragment phiên bản trước để so sánh diff)")
        return lines

    prev_nodes = {n.get("tempId"): n for n in prev_fragment.get("nodes", [])}
    new_nodes = {n.get("tempId"): n for n in new_fragment.get("nodes", [])}

    kept_nodes = []
    modified_nodes = []
    removed_nodes = []
    added_nodes = []

    for t_id, n_node in new_nodes.items():
        if t_id not in prev_nodes:
            added_nodes.append(
                f"Node [{n_node.get('className')}]: tempId={t_id}, identity={_json_str(n_node.get('identity'))}"
            )
        else:
            p_node = prev_nodes[t_id]
            diffs = []
            if p_node.get("className") != n_node.get("className"):
                diffs.append(
                    f"className: {p_node.get('className')} -> {n_node.get('className')}"
                )
            if p_node.get("identity") != n_node.get("identity"):
                diffs.append(
                    f"identity: {_json_str(p_node.get('identity'))} -> {_json_str(n_node.get('identity'))}"
                )
            p_props = {
                p.get("propertyName"): p.get("value")
                for p in p_node.get("properties", [])
            }
            n_props = {
                p.get("propertyName"): p.get("value")
                for p in n_node.get("properties", [])
            }
            if p_props != n_props:
                diffs.append(
                    f"properties: {_json_str(p_props)} -> {_json_str(n_props)}"
                )
            if diffs:
                modified_nodes.append(f"Node tempId={t_id} ({'; '.join(diffs)})")
            else:
                kept_nodes.append(f"Node [{n_node.get('className')}]: tempId={t_id}")

    for t_id, p_node in prev_nodes.items():
        if t_id not in new_nodes:
            removed_nodes.append(f"Node [{p_node.get('className')}]: tempId={t_id}")

    prev_edges = {
        (e.get("edgeName"), e.get("sourceTempId"), e.get("targetTempId")): e
        for e in prev_fragment.get("edges", [])
    }
    new_edges = {
        (e.get("edgeName"), e.get("sourceTempId"), e.get("targetTempId")): e
        for e in new_fragment.get("edges", [])
    }

    kept_edges = [
        f"Edge [{k[0]}]: {k[1]} -> {k[2]}" for k in new_edges if k in prev_edges
    ]
    added_edges = [
        f"Edge [{k[0]}]: {k[1]} -> {k[2]}" for k in new_edges if k not in prev_edges
    ]
    removed_edges = [
        f"Edge [{k[0]}]: {k[1]} -> {k[2]}" for k in prev_edges if k not in new_edges
    ]

    lines.append("     • Giữ (Unchanged):")
    if kept_nodes or kept_edges:
        for item in kept_nodes + kept_edges:
            lines.append(f"       - {item}")
    else:
        lines.append("       - (không có)")

    lines.append("     • Sửa (Modified):")
    if modified_nodes:
        for item in modified_nodes:
            lines.append(f"       - {item}")
    else:
        lines.append("       - (không có)")

    lines.append("     • Xóa (Removed):")
    if removed_nodes or removed_edges:
        for item in removed_nodes + removed_edges:
            lines.append(f"       - {item}")
    else:
        lines.append("       - (không có)")

    if added_nodes or added_edges:
        lines.append("     • Thêm mới (Added):")
        for item in added_nodes + added_edges:
            lines.append(f"       - {item}")

    return lines


def _analyze_cross_batch_merge(batches: list[Any]) -> dict[str, Any]:
    """Phân tích quá trình gộp các mảnh đồ thị giữa các mẻ (cross-batch merge)."""
    nodes_by_key: dict[tuple[str, str], dict] = {}
    total_raw_nodes = 0
    total_raw_edges = 0
    all_raw_edges: list[dict] = []

    for b in batches:
        frag = (
            b.get("graph_fragment")
            if isinstance(b, dict)
            else getattr(b, "graph_fragment", None)
        )
        if not frag:
            continue
        b_idx = (
            b.get("batch_index")
            if isinstance(b, dict)
            else getattr(b, "batch_index", "?")
        )
        b_nodes = frag.get("nodes", [])
        b_edges = frag.get("edges", [])
        total_raw_nodes += len(b_nodes)
        total_raw_edges += len(b_edges)

        for n in b_nodes:
            c_name = n.get("className")
            ident = n.get("identity") or {"tempId": n.get("tempId")}
            key = (c_name, _json_str(ident))
            if key not in nodes_by_key:
                nodes_by_key[key] = {
                    "className": c_name,
                    "identity": ident,
                    "tempId": n.get("tempId"),
                    "batches": [b_idx],
                    "properties": {},
                    "conflicts": [],
                    "evidence_count": len(n.get("evidence", [])),
                }
                for p in n.get("properties", []):
                    nodes_by_key[key]["properties"][p.get("propertyName")] = [
                        (b_idx, p.get("value"))
                    ]
            else:
                existing = nodes_by_key[key]
                existing["batches"].append(b_idx)
                existing["evidence_count"] += len(n.get("evidence", []))
                for p in n.get("properties", []):
                    p_name = p.get("propertyName")
                    p_val = p.get("value")
                    if p_name not in existing["properties"]:
                        existing["properties"][p_name] = [(b_idx, p_val)]
                    else:
                        prev_entries = existing["properties"][p_name]
                        if any(prev_val != p_val for _, prev_val in prev_entries):
                            existing["conflicts"].append(
                                {
                                    "property": p_name,
                                    "batch1": prev_entries[0][0],
                                    "val1": prev_entries[0][1],
                                    "batch2": b_idx,
                                    "val2": p_val,
                                }
                            )
                        existing["properties"][p_name].append((b_idx, p_val))

        for e in b_edges:
            all_raw_edges.append({**e, "batchIndex": b_idx})

    edges_by_key: dict[tuple, list] = {}
    for e in all_raw_edges:
        ekey = (e.get("edgeName"), e.get("sourceTempId"), e.get("targetTempId"))
        if ekey not in edges_by_key:
            edges_by_key[ekey] = [e]
        else:
            edges_by_key[ekey].append(e)

    return {
        "totalRawNodes": total_raw_nodes,
        "mergedNodesCount": len(nodes_by_key),
        "totalRawEdges": total_raw_edges,
        "mergedEdgesCount": len(edges_by_key),
        "nodes": list(nodes_by_key.values()),
        "edges": edges_by_key,
    }


def _format_merge_result(merge_data: dict[str, Any]) -> list[str]:
    """4. Định dạng MERGE_RESULT: node trước gộp, node sau gộp, thuộc tính xung đột, thuộc tính bị bỏ."""
    lines = ["  🔗 MERGE_RESULT (Trạng thái hợp nhất giữa các mẻ):"]
    lines.append(
        f"     • Nodes: {merge_data.get('totalRawNodes', 0)} nút trước gộp -> {merge_data.get('mergedNodesCount', 0)} thực thể sau gộp"
    )
    lines.append(
        f"     • Edges: {merge_data.get('totalRawEdges', 0)} quan hệ trước gộp -> {merge_data.get('mergedEdgesCount', 0)} quan hệ sau gộp"
    )

    lines.append("     • Chi tiết thực thể sau gộp (Entities):")
    for n in merge_data.get("nodes", []):
        c_name = n["className"]
        ident = _json_str(n["identity"])
        batches = sorted(set(n["batches"]))
        b_str = (
            f"từ Batch #{batches[0]}"
            if len(batches) == 1
            else f"gộp từ các Batches {batches}"
        )
        lines.append(f"       - [{c_name}] {ident} ({b_str}):")

        prop_items = []
        for p_name, p_history in n["properties"].items():
            first_b, first_v = p_history[0]
            if len(p_history) > 1:
                prop_items.append(
                    f"{p_name}={_json_str(first_v)} (trùng lặp tại {len(p_history)} mẻ)"
                )
            else:
                prop_items.append(f"{p_name}={_json_str(first_v)} (Batch #{first_b})")
        if prop_items:
            lines.append(f"         * Thuộc tính gộp: {'; '.join(prop_items)}")

        conflicts = n.get("conflicts", [])
        if conflicts:
            lines.append("         * ⚠️ Thuộc tính xung đột:")
            for cf in conflicts:
                lines.append(
                    f"           ! '{cf['property']}': Batch #{cf['batch1']}='{cf['val1']}' vs Batch #{cf['batch2']}='{cf['val2']}'"
                )

        lines.append(
            f"         * Tổng bằng chứng trích dẫn: {n.get('evidence_count', 0)}"
        )

    # Chi tiết các quan hệ sau gộp (Edges)
    edges_map = merge_data.get("edges", {})
    if edges_map:
        lines.append(f"     • Chi tiết quan hệ sau gộp ({len(edges_map)} quan hệ duy nhất):")
        for (ename, src, tgt), elist in edges_map.items():
            b_list = sorted({e.get("batchIndex") for e in elist if e.get("batchIndex") is not None})
            b_info = f" (từ Batch #{b_list[0]})" if len(b_list) == 1 else f" (gộp từ Batches {b_list})"
            lines.append(f"       - [{ename}]: {src} -> {tgt}{b_info}")
            for e in elist:
                if e.get("properties"):
                    lines.append(f"         Properties: {_json_str(e.get('properties'))}")
                    break

    return lines


def _format_cumulative_graph(cum_graph: dict[str, Any]) -> list[str]:
    """Định dạng toàn bộ đồ thị tích lũy (Cumulative Graph) cho tới thời điểm hiện tại."""
    lines = ["  🌐 ĐỒ THỊ TÍCH LŨY HIỆN TẠI (CUMULATIVE GRAPH):"]
    batches = cum_graph.get("stagedBatches", [])
    nodes = cum_graph.get("nodes", [])
    edges = cum_graph.get("edges", [])

    lines.append(f"     • Tiến độ đã gom: {len(batches)} batches thành công (Batches: {batches})")
    lines.append(f"     • Tổng thực thể tích lũy: {len(nodes)} nodes | Tổng quan hệ tích lũy: {len(edges)} edges")

    if nodes:
        lines.append(f"     • Danh sách Nodes đã trích xuất & tích lũy ({len(nodes)}):")
        for n in nodes:
            c_name = n.get("className", "Unknown")
            ident = _json_str(n.get("identity") or {"tempId": n.get("tempId")})
            t_id = n.get("tempId", "?")
            lines.append(f"       - [{c_name}] tempId={t_id}, identity={ident}")
            props = n.get("properties", [])
            if props:
                prop_strs = [f"{p.get('propertyName')}={_json_str(p.get('value'))}" for p in props]
                lines.append(f"         Thuộc tính: {'; '.join(prop_strs)}")

    if edges:
        lines.append(f"     • Danh sách Edges đã trích xuất & tích lũy ({len(edges)}):")
        for e in edges:
            ename = e.get("edgeName", "Unknown")
            src = e.get("sourceTempId", "?")
            tgt = e.get("targetTempId", "?")
            lines.append(f"       - [{ename}]: {src} -> {tgt}")
            if e.get("properties"):
                lines.append(f"         Properties: {_json_str(e.get('properties'))}")

    return lines


def _format_fill_result(fill_prep: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """5. Định dạng FILL: payload cuối trước khi ghi Neo4j và kết quả đọc lại thành công."""
    lines = ["  💾 FILL & READBACK NEO4J:"]
    lines.append("     • Payload chuẩn bị ghi vào đồ thị:")
    lines.append(f"       - entities = {fill_prep.get('entities_count', 0)}")
    entities = fill_prep.get("entities", [])
    for e in entities[:15]:
        lines.append(
            f"         * [{e.get('className')}] identity={_json_str(e.get('identity'))} (tempId={e.get('tempId')})"
        )
    if len(entities) > 15:
        lines.append(
            f"         ... và {len(entities) - 15} entities khác"
        )

    lines.append(f"       - facts = {fill_prep.get('facts_count', 0)}")
    lines.append(f"       - relations = {fill_prep.get('relations_count', 0)}")
    relations = fill_prep.get("relations", [])
    if relations:
        for r in relations[:15]:
            lines.append(
                f"         * [{r.get('edgeName', 'RELATION')}]: {r.get('sourceTempId')} -> {r.get('targetTempId')}"
            )
        if len(relations) > 15:
            lines.append(f"         ... và {len(relations) - 15} relations khác")

    lines.append(f"       - chunks = {fill_prep.get('chunks_count', 0)}")

    lines.append("     • Kết quả kiểm tra đọc lại (Readback Verification):")
    lines.append(f"       - readback = {result.get('readbackVerified')}")
    lines.append(
        f"       - nodes = {result.get('nodes')} (actual) | edges = {result.get('edges')} (actual)"
    )
    lines.append(
        f"       - chunks = {result.get('chunks')} (actual) | facts = {result.get('facts')} (actual)"
    )
    lines.append(f"       - commitStatus = {result.get('commitStatus')}")

    if result.get("readbackVerified") and (entities or relations):
        lines.append("     ✅ ĐÃ FILL VÀO NEO4J THÀNH CÔNG:")
        lines.append(f"       - Đã lưu {result.get('nodes', len(entities))} nodes thực thể")
        lines.append(f"       - Đã lưu {result.get('edges', len(relations))} edges quan hệ")

    return lines


def log_ingestion_event(
    step: str,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
    request: dict[str, Any] | None = None,
) -> None:
    """Ghi lại chi tiết request, response và các sự kiện trong vòng đời Ingestion vào docs/log.txt."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [f"[{now_str}] === {step} ==="]

        # 1. Ghi nhận Request / Đầu vào
        if request:
            lines.append("  📥 REQUEST / INPUT:")
            for k, v in request.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"     • {k}: {json.dumps(v, ensure_ascii=False)}")
                else:
                    lines.append(f"     • {k}: {v}")

        # 2. Ghi nhận Lỗi nếu có
        if error:
            lines.append(f"  ❌ ERROR / EXCEPTION: {error}")

        # 3. Ghi nhận Response / Kết quả chi tiết
        elif payload:
            stage = payload.get("stage")
            next_action = payload.get("nextAction")
            success = payload.get("success")
            stats = payload.get("workspaceStats") or {}

            if success is not None:
                lines.append(f"  • Success: {success}")
            if stage:
                lines.append(f"  • Stage: {stage}")
            if next_action:
                lines.append(f"  • Next Action: {next_action}")

            # Thống kê tổng quát về chunks & batches
            if stats:
                lines.append(
                    f"  • Tiến độ: {stats.get('stagedBatches', 0)}/{stats.get('batches', 0)} batches | Tổng cộng {stats.get('chunks', 0)} chunks"
                )
            elif payload.get("totalBatches") is not None:
                lines.append(f"  • Tổng số batches: {payload.get('totalBatches')}")

            # Thông tin batch đang xử lý (GET_BATCH)
            if "batch" in payload and isinstance(payload["batch"], dict):
                b = payload["batch"]
                chunks_info = b.get("chunks", [])
                chunk_indices = [
                    c.get("chunkIndex")
                    if isinstance(c, dict)
                    else getattr(c, "chunk_index", None)
                    for c in chunks_info
                ]
                lines.append(
                    f"  • Đang lấy Batch #{b.get('batchIndex')} (gồm các Chunk: {chunk_indices}) | Scope: '{b.get('scopeKey')}'"
                )
                if chunks_info:
                    lines.append("     Các đoạn văn bản nguồn (Chunks):")
                    for c in chunks_info:
                        if isinstance(c, dict):
                            c_idx = c.get("chunkIndex")
                            c_sec = c.get("section") or "<no-section>"
                            c_len = len(c.get("text", ""))
                            lines.append(
                                f"       - Chunk [{c_idx}] Section: '{c_sec}' ({c_len} ký tự)"
                            )

            # Thông tin batch tiếp theo
            next_b = payload.get("nextBatch")
            if isinstance(next_b, dict):
                lines.append(
                    f"  • Batch tiếp theo: Batch #{next_b.get('batchIndex')} (xử lý Chunk: {next_b.get('chunkIndexes')})"
                )
            elif next_b is not None:
                lines.append(f"  • Batch tiếp theo: #{next_b}")

            # Danh mục scope (LIST_SCOPES)
            if "scopes" in payload and isinstance(payload["scopes"], list):
                scope_keys = [
                    s.get("scopeKey")
                    if isinstance(s, dict)
                    else getattr(s, "scope_key", str(s))
                    for s in payload["scopes"]
                ]
                lines.append(f"  • Danh mục Scopes ({len(scope_keys)}): {scope_keys}")

            # 1. Nội dung lược đồ hợp nhất sau LOAD_SCOPES
            if "scope" in payload and isinstance(payload["scope"], dict):
                lines.extend(_format_merged_ontology(payload["scope"]))

            # 2 & 3. Trích xuất fragment chi tiết (EXTRACT_RESULT) & REPAIR_DIFF
            ext_res = None
            if "extractResult" in payload:
                ext_res = payload["extractResult"]
            elif "extraction" in payload and isinstance(payload["extraction"], dict):
                ext_res = payload["extraction"]
            elif "repairDelta" in payload and isinstance(payload["repairDelta"], dict):
                ext_res = payload["repairDelta"]

            if ext_res is not None:
                # Nếu có lần sửa trước đó
                if (
                    payload.get("previousFragment")
                    or payload.get("validationAttempts", 0) > 0
                ):
                    lines.extend(
                        _compute_and_format_repair_diff(
                            prev_fragment=payload.get("previousFragment"),
                            new_fragment=ext_res,
                            prev_issues=payload.get("previousIssues"),
                            validation_attempts=payload.get("validationAttempts", 1),
                        )
                    )
                lines.extend(_format_extract_result(ext_res))
            elif isinstance(payload.get("nodes"), list) or isinstance(
                payload.get("edges"), list
            ):
                # Fallback nếu payload chứa trực tiếp nodes/edges dạng list
                lines.extend(_format_extract_result(payload))

            # 4. Trạng thái hợp nhất giữa các mẻ (MERGE_RESULT)
            if "batchesForMerge" in payload and isinstance(
                payload["batchesForMerge"], list
            ):
                merge_data = _analyze_cross_batch_merge(payload["batchesForMerge"])
                lines.extend(_format_merge_result(merge_data))
            elif "mergeResult" in payload and isinstance(payload["mergeResult"], dict):
                lines.extend(_format_merge_result(payload["mergeResult"]))

            # Đồ thị tích lũy cho tới thời điểm hiện tại (CUMULATIVE_GRAPH)
            if "cumulativeGraph" in payload and isinstance(
                payload["cumulativeGraph"], dict
            ):
                lines.extend(_format_cumulative_graph(payload["cumulativeGraph"]))

            # 5. Payload trước khi ghi Neo4j & Readback (FILL)
            if "fillPreparation" in payload and isinstance(
                payload["fillPreparation"], dict
            ):
                lines.extend(_format_fill_result(payload["fillPreparation"], payload))

            # Chi tiết lỗi/cảnh báo xác thực schema (Validation Issues)
            if payload.get("validationIssues"):
                issues = payload["validationIssues"]
                lines.append(f"  ⚠️ Cảnh báo / Lỗi Schema ({len(issues)} vấn đề):")
                for issue in issues:
                    if isinstance(issue, dict):
                        lines.append(
                            f"     - [{issue.get('code')}] {issue.get('message')} (tại: {issue.get('location')})"
                        )
                    else:
                        lines.append(f"     - {issue}")

            if payload.get("errors"):
                errs = payload["errors"]
                lines.append(f"  ❌ Lỗi trả về ({len(errs)}):")
                for err_item in errs:
                    if isinstance(err_item, dict):
                        lines.append(
                            f"     - [{err_item.get('code')}] {err_item.get('message')} (vị trí: {err_item.get('location')})"
                        )
                    else:
                        lines.append(f"     - {err_item}")

            # Fingerprint và kết quả commit Neo4j
            if "readinessFingerprint" in payload:
                lines.append(
                    f"  • Sẵn sàng ghi (Fingerprint): {str(payload.get('readinessFingerprint'))[:24]}..."
                )
            if "written" in payload:
                lines.append(
                    f"  ✅ Đã lưu vào Neo4j: {json.dumps(payload.get('written'), ensure_ascii=False)}"
                )

        lines.append("")  # Dòng trống phân cách giữa các block sự kiện

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
    except Exception as e:
        print(f"Failed to write ingestion log: {e}")


def write_raw_trace(header: str, content: Any = None) -> None:
    """Ghi trực tiếp một sự kiện/trace với đầy đủ nội dung vào file log.txt ngay lập tức."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"[{now_str}] === {header} ==="]
        if content is not None:
            if isinstance(content, (dict, list)):
                formatted = json.dumps(
                    _safe_serialize(content), ensure_ascii=False, indent=2
                )
                lines.append(formatted)
            elif isinstance(content, str):
                lines.append(content)
            else:
                formatted = json.dumps(
                    _safe_serialize(content), ensure_ascii=False, indent=2
                )
                lines.append(formatted)
        lines.append("")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
    except Exception as e:
        print(f"Failed to write trace log: {e}")


def _safe_serialize(obj: Any, depth: int = 0) -> Any:
    """Chuyển đổi an toàn và nguyên vẹn 100% mọi đối tượng ADK Event, Model, Tool thành JSON."""
    if depth > 15:
        return str(obj)
    if obj is None or isinstance(obj, (int, float, str, bool)):
        return obj
    if isinstance(obj, (list, tuple, set)):
        return [_safe_serialize(item, depth + 1) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v, depth + 1) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        try:
            return _safe_serialize(
                obj.model_dump(by_alias=True, mode="json"), depth + 1
            )
        except Exception:
            pass
    if hasattr(obj, "to_dict"):
        try:
            return _safe_serialize(obj.to_dict(), depth + 1)
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return {
                k: _safe_serialize(v, depth + 1)
                for k, v in obj.__dict__.items()
                if not k.startswith("_")
            }
        except Exception:
            pass
    return str(obj)


from google.adk.plugins.base_plugin import BasePlugin


class ADKDetailedLoggerPlugin(BasePlugin):
    """Plugin tự động ghi nhận mọi Trace, Event, Request, Response, Tool Call, Model Input/Output vào log.txt."""

    def __init__(self) -> None:
        super().__init__(name="adk_detailed_logger")

    async def on_event_callback(self, *, invocation_context: Any, event: Any):
        author = getattr(event, "author", "system")
        event_id = getattr(event, "id", None)
        write_raw_trace(f"⚡ [ADK EVENT TRACE] author={author} id={event_id}", event)

    async def on_user_message_callback(self, *, invocation_context: Any, user_message: Any):
        text = ""
        for part in getattr(user_message, "parts", []):
            if hasattr(part, "text") and part.text:
                text += part.text + "\n"
        write_raw_trace(
            "👤 USER INPUT MESSAGE", {"message": text.strip(), "raw": user_message}
        )

    async def before_agent_callback(self, *, agent: Any, callback_context: Any):
        agent_name = getattr(agent, "name", "unknown_agent")
        write_raw_trace(
            f"🤖 [AGENT START] {agent_name}",
            {"agent": agent_name, "model": getattr(agent, "model", None)},
        )

    async def after_agent_callback(self, *, agent: Any, callback_context: Any):
        agent_name = getattr(agent, "name", "unknown_agent")
        write_raw_trace(f"🏁 [AGENT FINISH] {agent_name}")

    async def before_tool_callback(self, *, tool: Any, tool_args: Any, tool_context: Any):
        tool_name = getattr(tool, "name", str(tool))
        write_raw_trace(
            f"🛠️ [TOOL REQUEST] {tool_name}", {"tool": tool_name, "arguments": tool_args}
        )

    async def after_tool_callback(self, *, tool: Any, tool_args: Any, tool_context: Any, result: Any):
        tool_name = getattr(tool, "name", str(tool))
        write_raw_trace(
            f"📦 [TOOL RESPONSE] {tool_name}", {"tool": tool_name, "result": result}
        )
        if tool_name in {
            "submit_ingestion_batch",
            "repair_ingestion_batch",
            "rebase_ingestion",
            "load_ontology_scopes",
            "get_ingestion_batch",
            "apply_schema_proposal",
            "review_schema_proposal",
            "create_schema_proposal",
            "finalize_ingestion",
            "fill_ingestion",
        }:
            await asyncio.sleep(1.2)

    async def before_model_callback(self, *, callback_context: Any, llm_request: Any):
        waited = await global_rate_limiter.acquire()
        agent = getattr(callback_context, "agent", None)
        if agent is None:
            inv_ctx = getattr(callback_context, "_invocation_context", None)
            agent = getattr(inv_ctx, "agent", None)
        agent_name = getattr(agent, "name", "unknown")

        req_data = {
            "agent": agent_name,
            "model": getattr(llm_request, "model", None),
            "contents": getattr(llm_request, "contents", None),
            "tools": [
                getattr(t, "name", str(t))
                for t in (getattr(llm_request, "tools", []) or [])
            ],
        }
        if waited > 0:
            req_data["rateLimitWaitSeconds"] = round(waited, 2)
        write_raw_trace(f"📤 [MODEL CALL REQUEST] ({agent_name})", req_data)

    async def after_model_callback(self, *, callback_context: Any, llm_response: Any):
        agent_name = getattr(
            getattr(callback_context, "agent", None), "name", "unknown"
        )
        content = getattr(llm_response, "content", None)
        text = ""
        function_calls = []
        if content and hasattr(content, "parts"):
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text + "\n"
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    function_calls.append(
                        {
                            "name": getattr(fc, "name", None),
                            "args": getattr(fc, "args", None),
                        }
                    )

        resp_data = {
            "agent": agent_name,
            "text": text.strip() if text else None,
            "functionCalls": function_calls if function_calls else None,
            "usage": getattr(llm_response, "usage_metadata", None),
        }
        write_raw_trace(f"📥 [MODEL CALL RESPONSE] ({agent_name})", resp_data)
        _write_token_usage_trace(agent_name, resp_data["usage"])

    async def on_model_error_callback(
        self, *, callback_context: Any, llm_request: Any, error: Exception
    ) -> Any:
        """Retry Gemini quota failures using the delay returned by Google."""
        error_text = str(error)
        if not _is_rate_limit(error_text):
            return None

        inv_ctx = getattr(callback_context, "_invocation_context", None)
        agent = getattr(callback_context, "agent", None) or getattr(inv_ctx, "agent", None)
        agent_name = getattr(agent, "name", "root_agent")
        delay = _retry_delay_seconds(error_text)
        write_raw_trace(
            f"⏳ [RATE LIMIT 429] {agent_name}",
            {"retryDelay": delay, "error": error_text},
        )

        llm = getattr(agent, "canonical_model", None)
        if llm is None:
            # When model object is not directly available, wait out the delay to allow quota replenishment
            await asyncio.sleep(delay)
            return None

        for attempt in range(1, 4):
            await asyncio.sleep(delay)
            try:
                async for response in llm.generate_content_async(llm_request, stream=False):
                    write_raw_trace(
                        f"✅ [RATE LIMIT RECOVERED] {agent_name}",
                        {"attempt": attempt},
                    )
                    return response
            except Exception as retry_error:
                error_text = str(retry_error)
                if not _is_rate_limit(error_text):
                    return None
                delay = _retry_delay_seconds(error_text, fallback=min(delay * 1.5, 15.0))
                write_raw_trace(
                    f"⏳ [RATE LIMIT RETRY #{attempt}] {agent_name}",
                    {"retryDelay": delay, "error": error_text},
                )

        return None

    async def on_tool_error_callback(self, *, tool: Any, tool_args: Any, tool_context: Any, error: Any):
        tool_name = getattr(tool, "name", str(tool))
        write_raw_trace(
            f"❌ [TOOL ERROR] {tool_name}",
            {"tool": tool_name, "error": str(error), "arguments": tool_args},
        )

    async def on_agent_error_callback(self, *, agent: Any, callback_context: Any, error: Any):
        agent_name = getattr(agent, "name", "unknown_agent")
        write_raw_trace(
            f"❌ [AGENT ERROR] {agent_name}", {"agent": agent_name, "error": str(error)}
        )


def _is_rate_limit(error_text: str) -> bool:
    normalized = error_text.lower()
    return (
        "429" in normalized
        or "resource_exhausted" in normalized
        or "resourceexhausted" in normalized
        or "rate-limits" in normalized
    )


def _retry_delay_seconds(error_text: str, *, fallback: float = 4.0) -> float:
    retry_delay = re.search(
        r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)(ms|s)?",
        error_text,
        flags=re.IGNORECASE,
    )
    retry_in = re.search(r"retry in (\d+(?:\.\d+)?)s", error_text, re.IGNORECASE)
    match = retry_delay or retry_in
    if not match:
        return fallback
    seconds = float(match.group(1))
    if match.group(2) == "ms":
        seconds /= 1000
    return max(seconds, 3.0)


def _write_token_usage_trace(agent_name: str, usage: Any) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[{now_str}] === 📊 TOKEN USAGE ({agent_name}) ==="]
    lines.extend(_format_token_usage(usage))
    lines.append("")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write("\n".join(lines) + "\n")
            log_file.flush()
    except Exception as error:
        print(f"Failed to write token usage log: {error}")


def _format_token_usage(usage: Any) -> list[str]:
    lines = ["  📊 TOKEN USAGE BREAKDOWN:"]
    if not usage:
        return lines + ["     (Không có thông tin usage metadata)"]

    def value(*names: str) -> Any:
        for name in names:
            candidate = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if candidate is not None:
                return candidate
        return 0

    lines.extend(
        [
            f"     • promptTokenCount:        {value('prompt_token_count', 'promptTokenCount'):,}",
            f"     • candidatesTokenCount:    {value('candidates_token_count', 'candidatesTokenCount'):,}",
            f"     • thoughtsTokenCount:      {value('thoughts_token_count', 'thoughtsTokenCount'):,}",
            f"     • cachedContentTokenCount: {value('cached_content_token_count', 'cachedContentTokenCount'):,}",
            f"     • totalTokenCount:         {value('total_token_count', 'totalTokenCount'):,}",
        ]
    )
    return lines
