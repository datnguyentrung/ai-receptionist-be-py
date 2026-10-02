"""Bộ ghi log thời gian thực cho quy trình Ingestion."""

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
    step: str, payload: dict[str, Any] | None = None, error: str | None = None
) -> None:
    """Ghi lại các sự kiện chính trong vòng đời Ingestion vào docs/log.txt."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [f"[{now_str}] === {step} ==="]
        if error:
            lines.append(f"  ❌ ERROR: {error}")
        elif payload:
            stage = payload.get("stage")
            next_action = payload.get("nextAction")
            stats = payload.get("workspaceStats") or {}

            # Thống kê tổng quát về chunks & batches
            if stats:
                lines.append(
                    f"  • Tiến độ: {stats.get('stagedBatches', 0)}/{stats.get('batches', 0)} batches | Tổng cộng {stats.get('chunks', 0)} chunks"
                )
            elif payload.get("totalBatches") is not None:
                lines.append(f"  • Tổng số batches: {payload.get('totalBatches')}")

            # Thông tin batch đang xử lý
            if "batch" in payload and isinstance(payload["batch"], dict):
                b = payload["batch"]
                chunks_info = b.get("chunks", [])
                chunk_indices = [
                    c.get("chunkIndex") for c in chunks_info if isinstance(c, dict)
                ]
                lines.append(
                    f"  • Đang lấy Batch #{b.get('batchIndex')} (gồm các Chunk: {chunk_indices}) | Scope: '{b.get('scopeKey')}'"
                )

            # Thông tin batch kế tiếp
            next_b = payload.get("nextBatch")
            if isinstance(next_b, dict):
                lines.append(
                    f"  • Batch tiếp theo: Batch #{next_b.get('batchIndex')} (xử lý Chunk: {next_b.get('chunkIndexes')})"
                )
            elif next_b is not None:
                lines.append(f"  • Batch tiếp theo: #{next_b}")

            if stage:
                lines.append(f"  • Trạng thái (Stage): {stage}")
            if next_action:
                lines.append(f"  • Hành động tiếp theo (Next Action): {next_action}")

            # Chi tiết lỗi/cảnh báo hoặc kết quả ghi Neo4j
            if payload.get("validationIssues"):
                lines.append(
                    f"  ⚠️ Cảnh báo Schema: {json.dumps(payload['validationIssues'], ensure_ascii=False)}"
                )

            if payload.get("errors"):
                lines.append(
                    f"  ❌ Lỗi trả về: {json.dumps(payload['errors'], ensure_ascii=False)}"
                )

            if "readinessFingerprint" in payload:
                lines.append(
                    f"  • Sẵn sàng ghi (Fingerprint): {payload.get('readinessFingerprint')[:16]}..."
                )

            if "written" in payload:
                lines.append(
                    f"  ✅ Đã lưu vào Neo4j: {json.dumps(payload.get('written'), ensure_ascii=False)}"
                )

        lines.append("")  # Dòng trống phân cách

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"Failed to write ingestion log: {e}")
