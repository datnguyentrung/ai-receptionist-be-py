"""Cloudflare Clef decision router."""

# Import module toán học để kiểm tra giá trị số thực hữu hạn (finite float)
import math

# Import thư viện requests để thực hiện cuộc gọi HTTP tới Cloudflare REST API
import requests

# Import cấu hình ứng dụng (chứa token và account_id của Cloudflare)
from app.core.config import settings

# Import schema chuẩn hóa response của Routing Agent và câu trả lời Clef
from app.schemas.agent_schema import (
    ChoiceClefResponse,
    NoulClefResponse,
    RoutingAgentResponse,
)

# Import hàm ghi nhật ký trace theo thời gian thực vào log.txt
from app.utils.ingestion_logger import write_raw_trace

# Định dạng endpoint gọi mô hình Clef của Cloudflare Workers AI
CLEF_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    "{account_id}/ai/run/@cf/cloudflare/clef"
)

# Tập hợp các routing target hợp lệ mà hệ thống hỗ trợ
VALID_ROUTES = {
    "INGESTION_AGENT",
    "CALCULATION_AGENT",
    "GRAPH_QA_AGENT",
    "ASK_CLARIFICATION",
}


def route_with_clef(
    message: str,
    history: list[dict],
) -> dict:
    # Lấy Cloudflare Account ID từ cấu hình hệ thống
    account_id = settings.CLOUDFLARE_ACCOUNT_ID
    # Lấy Cloudflare Auth Token (Bearer token) từ cấu hình hệ thống
    token = settings.CLOUDFLARE_AUTH_TOKEN

    # Kiểm tra nếu chưa cấu hình tài khoản hoặc token Cloudflare
    if not account_id or not token:
        # Ghi log cảnh báo thiếu cấu hình xác thực vào log.txt
        write_raw_trace(
            "⚠️ [CLEF ROUTER] CREDENTIALS MISSING",
            {"error": "Cloudflare account_id or auth_token is not set."},
        )
        # Báo lỗi để kích hoạt cơ chế fallback sang LLM router
        raise RuntimeError("Cloudflare credentials unavailable")

    # Xây dựng payload request theo đúng định dạng mô hình Clef yêu cầu
    payload = {
        # Tên mô hình Clef của Cloudflare
        "model": "clef",
        # Trạng thái ngữ cảnh gửi cho mô hình
        "state": {
            # Ngữ cảnh hệ thống về nghiệp vụ trợ lý học viện Taekwondo
            "system_context": (
                "Bạn đang giao tiếp với trợ lý ảo của học viện Taekwondo. Học viện có các lớp học Taekwondo, các kỳ thi đấu, và các quy định về học phí."
            ),
            # Chỉ lấy tối đa 8 lượt trao đổi gần nhất trong lịch sử hội thoại
            "conversation_history": history[-8:],
            # Tin nhắn hiện tại của người dùng cần phân loại
            "current_user_message": message,
        },
        # Danh sách câu hỏi cần Clef đưa ra phán quyết
        "questions": {
            # Câu hỏi 1: Xác định tin nhắn có mang ý định hợp lệ không (dạng noul)
            "meaningful": {
                "type": "noul",
                "instructions": (
                    "Dựa trên câu hỏi hiện tại và lịch sử hội thoại, có xác định được ý định hợp lệ của người dùng không?"
                ),
            },
            # Câu hỏi 2: Lựa chọn domain agent phù hợp nhất (dạng choice)
            "decision": {
                "type": "choice",
                "instructions": (
                    "Chọn đúng một target dựa trên ý định ngữ nghĩa của câu hỏi hiện tại và lịch sử hội thoại. Không chỉ dựa vào việc câu hỏi có chứa đúng từ khóa hay không. Các câu hỏi ngắn nhưng có nghĩa vẫn phải được phân loại."
                ),
                "criteria": {
                    "INGESTION_AGENT": (
                        "Yêu cầu nạp tài liệu mới, xử lý file, trích xuất tri thức từ tài liệu, tạo hoặc cập nhật schema/ontology/Knowledge Graph, hoặc thực hiện quy trình ingestion."
                    ),
                    "CALCULATION_AGENT": (
                        "Yêu cầu thực hiện phép tính số học hoặc tính toán dựa trên các giá trị cụ thể, bao gồm cộng trừ nhân chia về học phí, số buổi, điểm số, thời lượng hoặc các tham số định lượng."
                    ),
                    "GRAPH_QA_AGENT": (
                        "Yêu cầu tra cứu hoặc trả lời thông tin dựa trên tri thức đã có về Taekwondo, võ đường, học viện hoặc câu lạc bộ: tên CLB/võ đường, lịch sử, người sáng lập, cơ sở, thông tin liên hệ, lớp học, lịch học, học phí, quy định, kỳ thi, kỹ thuật, bài quyền và luật thi đấu. Bao gồm câu hỏi tri thức chung, chào hỏi, và câu hỏi ngắn như 'CLB tên gì?', 'võ đường tên gì?', 'lịch học?'. Nếu câu hỏi có thể trả lời bằng cách tra cứu Knowledge Graph thì chọn GRAPH_QA_AGENT."
                    ),
                    "ASK_CLARIFICATION": (
                        "Chỉ chọn khi câu hỏi có ít nhất hai cách hiểu nghiệp vụ cạnh tranh giữa các agent và không thể xác định ý định chính từ câu hiện tại hoặc lịch sử. Không chọn chỉ vì câu hỏi ngắn, viết tắt, thiếu tham số chi tiết, hoặc không chứa keyword rõ ràng."
                    ),
                },
            },
        },
    }

    # Ghi lại trace chi tiết request chuẩn bị gửi sang Cloudflare vào log.txt
    write_raw_trace(
        "⚡ [CLEF ROUTER REQUEST]",
        {
            "message": message,
            "historyCount": len(history),
            "payloadState": payload["state"],
        },
    )

    try:
        # Gửi POST request tới Cloudflare API với timeout kết nối 2s, đọc 6s
        response = requests.post(
            CLEF_URL.format(account_id=account_id),
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=(2, 6),
        )
        # Bắn lỗi nếu status code là lỗi 4xx hoặc 5xx
        response.raise_for_status()
        # Parse dữ liệu response trả về từ Cloudflare dạng JSON
        raw_json = response.json()
    except Exception as exc:
        # Ghi log lỗi kết nối hoặc HTTP error vào file log.txt
        write_raw_trace(
            "❌ [CLEF ROUTER HTTP/NETWORK ERROR]",
            {"error": str(exc)},
        )
        # Ném lại ngoại lệ để tầng ngoài chuyển sang LLM fallback
        raise

    try:
        # Validate và parse dữ liệu JSON thô theo quy chuẩn RoutingAgentResponse
        parsed_response = RoutingAgentResponse.model_validate(raw_json)
    except Exception as exc:
        # Ghi log lỗi khi response trả về không khớp cấu trúc schema chuẩn
        write_raw_trace(
            "❌ [CLEF ROUTER SCHEMA VALIDATION ERROR]",
            {"error": str(exc), "raw_response": raw_json},
        )
        # Báo lỗi cấu trúc dữ liệu không hợp lệ
        raise ValueError(f"Cloudflare Clef response invalid schema: {exc}") from exc

    # Kiểm tra cờ thành công của API Cloudflare
    if parsed_response.success is False:
        # Ghi nhận lỗi API thất bại vào log.txt
        write_raw_trace(
            "❌ [CLEF ROUTER API FAILURE]",
            {"errors": parsed_response.errors, "messages": parsed_response.messages},
        )
        # Báo lỗi khi API trả về success = False
        raise ValueError("Cloudflare API returned failure")

    # Lấy dictionary chứa các câu trả lời đã parse từ kết quả mô hình
    answers = parsed_response.result.answers

    # Lấy câu trả lời cho câu hỏi decision
    decision_answer = answers.get("decision")
    # Lấy câu trả lời cho câu hỏi meaningful
    meaningful_answer = answers.get("meaningful")

    # Kiểm tra xem câu trả lời decision có đúng loại ChoiceClefResponse không
    if not isinstance(decision_answer, ChoiceClefResponse):
        # Ghi log lỗi định dạng kiểu dữ liệu của decision vào log.txt
        write_raw_trace(
            "❌ [CLEF ROUTER FORMAT ERROR]",
            {
                "reason": "Decision answer is not of choice type",
                "decision": str(decision_answer),
            },
        )
        # Báo lỗi nếu kiểu câu trả lời decision không hợp lệ
        raise ValueError("Unexpected Clef decision type")

    # Kiểm tra xem câu trả lời meaningful có đúng loại NoulClefResponse không
    if not isinstance(meaningful_answer, NoulClefResponse):
        # Ghi log lỗi định dạng kiểu dữ liệu của meaningful vào log.txt
        write_raw_trace(
            "❌ [CLEF ROUTER FORMAT ERROR]",
            {
                "reason": "Meaningful answer is not of noul type",
                "meaningful": str(meaningful_answer),
            },
        )
        # Báo lỗi nếu kiểu câu trả lời meaningful không hợp lệ
        raise ValueError("Unexpected Clef meaningful type")

    # Lấy domain agent được Clef lựa chọn trực tiếp từ schema ChoiceClefResponse
    target = decision_answer.choice
    # Lấy độ tin cậy của lựa chọn trực tiếp từ schema ChoiceClefResponse
    confidence = float(decision_answer.confidence)
    # Lấy điểm số xác định ý định trực tiếp từ schema NoulClefResponse
    meaningful = float(meaningful_answer.noul)

    # Kiểm tra xem domain được chọn có nằm trong danh sách domain hợp lệ không
    if target not in VALID_ROUTES:
        # Ghi log target không hợp lệ vào log.txt
        write_raw_trace(
            "❌ [CLEF ROUTER INVALID ROUTE]",
            {"target": target, "validRoutes": list(VALID_ROUTES)},
        )
        # Báo lỗi route không hợp lệ
        raise ValueError(f"Invalid Clef route: {target}")

    # Kiểm tra tính hợp lệ của xác suất: phải là số thực hữu hạn trong đoạn [0, 1]
    if not (
        math.isfinite(confidence)
        and 0 <= confidence <= 1
        and math.isfinite(meaningful)
        and 0 <= meaningful <= 1
    ):
        # Ghi log lỗi giá trị xác suất ngoài khoảng hợp lệ vào log.txt
        write_raw_trace(
            "❌ [CLEF ROUTER INVALID RANGE]",
            {"confidence": confidence, "meaningful": meaningful},
        )
        # Báo lỗi giá trị xác suất không hợp lệ
        raise ValueError("Invalid Clef probability values")

    # Tạo dict kết quả định tuyến đã được chuẩn hóa để chuyển tiếp cho HybridRootAgent
    routing_result = {
        # Tên target agent được chọn
        "target": target,
        # Độ tin cậy của lựa chọn
        "confidence": confidence,
        # Điểm số ý nghĩa ngữ cảnh
        "meaningful": meaningful,
    }

    # Ghi log kết quả định tuyến thành công của Clef vào log.txt
    write_raw_trace(
        "🎯 [CLEF ROUTER RESPONSE]",
        routing_result,
    )

    # Trả về kết quả định tuyến đã chuẩn hóa
    return routing_result
