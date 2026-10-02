"""Bộ ghi log thời gian thực và chi tiết cho quy trình Ingestion."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

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
                    c.get("chunkIndex") if isinstance(c, dict) else getattr(c, "chunk_index", None)
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
                            lines.append(f"       - Chunk [{c_idx}] Section: '{c_sec}' ({c_len} ký tự)")

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
                    s.get("scopeKey") if isinstance(s, dict) else getattr(s, "scope_key", str(s))
                    for s in payload["scopes"]
                ]
                lines.append(f"  • Danh mục Scopes ({len(scope_keys)}): {scope_keys}")

            # Thông tin trích xuất đồ thị (SUBMIT_BATCH kết quả)
            if "nodes" in payload or "edges" in payload:
                nodes = payload.get("nodes") or []
                edges = payload.get("edges") or []
                lines.append(f"  • Đồ thị trích xuất: {len(nodes)} Nodes, {len(edges)} Edges")
                for n in nodes:
                    if isinstance(n, dict):
                        n_id = n.get("identity") or n.get("tempId")
                        lines.append(f"     - Node [{n.get('className')}]: tempId={n.get('tempId')}, identity={json.dumps(n_id, ensure_ascii=False)}")
                for e in edges:
                    if isinstance(e, dict):
                        lines.append(f"     - Edge [{e.get('edgeName')}]: {e.get('sourceTempId')} -> {e.get('targetTempId')}")

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
    except Exception as e:
        print(f"Failed to write ingestion log: {e}")


__all__ = ["LOG_FILE", "log_ingestion_event", "reset_log_file"]
