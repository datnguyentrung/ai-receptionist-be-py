"""FastAPI Router for ADK Agent SSE chat streaming and session endpoints."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from app.agent.agent import app as adk_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["Agent Chat (SSE)"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    SEARCHING = "SEARCHING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


_STATUS_MESSAGES: dict[ChatStatus, str] = {
    ChatStatus.RECEIVED: "TBot đang xử lý...",
    ChatStatus.PROCESSING: "TBot đang suy nghĩ...",
    ChatStatus.SEARCHING: "TBot đang tìm thông tin...",
    ChatStatus.GENERATING: "TBot đang soạn câu trả lời...",
    ChatStatus.COMPLETED: "",
    ChatStatus.FAILED: "TBot chưa thể trả lời lúc này. Vui lòng thử lại.",
}


def _status_frame(request_id: str, chat_status: ChatStatus) -> str:
    return _sse_frame(
        "status",
        {
            "type": "status",
            "requestId": request_id,
            "status": chat_status.value,
            "message": _STATUS_MESSAGES[chat_status],
        },
    )


def _commit_final_response(
    *,
    request_id: str,
    content: str,
) -> str:
    """Single commit point allowed to emit the atomic `final` SSE message."""
    cleaned = content.strip()
    if not cleaned:
        raise ValueError("Empty agent response")
    return _sse_frame(
        "final",
        {
            "type": "final",
            "requestId": request_id,
            "messageId": f"msg_{uuid4().hex[:12]}",
            "content": cleaned,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        },
    )


class AgentFileAttachment(BaseModel):
    """Tệp đính kèm gửi vào phiên chat (ví dụ tài liệu .md/.txt để ingestion)."""

    filename: str = Field(min_length=1, description="Tên file hiển thị (artifact_name).")
    mime_type: str = Field(default="text/markdown", description="Định dạng MIME của tệp.")
    text: str | None = Field(default=None, description="Nội dung văn bản UTF-8 trực tiếp.")
    content_base64: str | None = Field(default=None, description="Nội dung nhị phân mã hóa Base64.")
    file_path: str | None = Field(
        default=None,
        description="Đường dẫn tương đối trong repo (ví dụ: 'docs/cam_nang_taekwondo_van_quan_chi_tiet.md').",
    )


class AgentChatRequest(BaseModel):
    """Yêu cầu chat streaming qua SSE với hệ thống ADK Agent."""

    message: str = Field(default="", description="Tin nhắn của người dùng gửi tới Agent.")
    user_id: str = Field(default="default_user", min_length=1, description="Mã định danh người dùng.")
    session_id: str | None = Field(
        default=None,
        description="Mã phiên hội thoại (nếu bỏ trống hệ thống sẽ tự tạo UUID mới).",
    )
    attachments: list[AgentFileAttachment] = Field(
        default_factory=list,
        description="Danh sách tệp đính kèm được lưu làm ADK Artifact trong session.",
    )
    stream_tokens: bool = Field(
        default=True,
        description="Bật StreamingMode.SSE để nhận từng chunk token ngay khi LLM sinh ra.",
    )


@lru_cache(maxsize=1)
def get_agent_runner() -> Runner:
    """Khởi tạo singleton ADK Runner gắn với `adk_app` (HybridRootAgent + 4 plugins)."""
    return Runner(
        app=adk_app,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
        auto_create_session=True,
    )


def _sse_frame(event_name: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {data}\n\n"


def _resolve_attachment_bytes(attachment: AgentFileAttachment) -> bytes:
    if attachment.text is not None:
        return attachment.text.encode()
    if attachment.content_base64 is not None:
        try:
            return base64.b64decode(attachment.content_base64, validate=True)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid base64 content for attachment '{attachment.filename}': {exc}",
            ) from exc
    if attachment.file_path is not None:
        workspace_root = Path.cwd().resolve()
        candidate = (workspace_root / attachment.file_path).resolve()
        if not candidate.is_relative_to(workspace_root) or not candidate.is_file():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Attachment file_path is invalid or does not exist: {attachment.file_path}",
            )
        return candidate.read_bytes()
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Attachment '{attachment.filename}' must provide text, content_base64, or file_path.",
    )


def _build_user_content(
    message: str,
    attachments: list[tuple[str, str, bytes]],
) -> types.Content:
    parts: list[types.Part] = []
    if message.strip():
        parts.append(types.Part(text=message))
    for filename, mime_type, raw_bytes in attachments:
        parts.append(
            types.Part(
                inline_data=types.Blob(
                    display_name=filename,
                    mime_type=mime_type or "text/plain",
                    data=raw_bytes,
                )
            )
        )
    if not parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either message or at least one file attachment is required.",
        )
    return types.Content(role="user", parts=parts)


async def _stream_agent_events(
    *,
    runner: Runner,
    user_id: str,
    session_id: str,
    user_content: types.Content,
    stream_tokens: bool,
) -> AsyncGenerator[str, None]:
    del stream_tokens  # Non-streaming atomic response; SSE carries status only.
    request_id = f"req_{uuid4().hex[:12]}"
    started_at = time.perf_counter()

    logger.info(
        "CHAT_REQUEST_RECEIVED | requestId=%s | sessionId=%s | userId=%s",
        request_id,
        session_id,
        user_id,
    )

    yield _sse_frame(
        "session",
        {
            "sessionId": session_id,
            "userId": user_id,
            "requestId": request_id,
        },
    )
    yield _status_frame(request_id, ChatStatus.RECEIVED)
    yield _status_frame(request_id, ChatStatus.PROCESSING)

    run_config = RunConfig(streaming_mode=StreamingMode.NONE)
    last_status = ChatStatus.PROCESSING
    draft_response = ""
    terminal_status = ChatStatus.FAILED

    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
            run_config=run_config,
        ):
            content = getattr(event, "content", None)
            parts = getattr(content, "parts", None) or []

            text_chunks: list[str] = []
            has_tool_activity = False

            for part in parts:
                if getattr(part, "thought", False):
                    continue
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text:
                    text_chunks.append(part_text)

                if (
                    getattr(part, "function_call", None) is not None
                    or getattr(part, "function_response", None) is not None
                ):
                    has_tool_activity = True

            if has_tool_activity and last_status != ChatStatus.SEARCHING:
                last_status = ChatStatus.SEARCHING
                yield _status_frame(request_id, ChatStatus.SEARCHING)

            if text_chunks:
                # Internal draft only — never streamed to Mobile before commit.
                draft_response = "".join(text_chunks)
                if last_status != ChatStatus.GENERATING:
                    last_status = ChatStatus.GENERATING
                    yield _status_frame(request_id, ChatStatus.GENERATING)

        final_frame = _commit_final_response(
            request_id=request_id,
            content=draft_response,
        )
        terminal_status = ChatStatus.COMPLETED
        total_ms = round((time.perf_counter() - started_at) * 1000)
        logger.info(
            "FINAL_RESPONSE_COMMITTED | requestId=%s | totalMs=%s | status=SUCCEEDED",
            request_id,
            total_ms,
        )
        yield final_frame
    except Exception as exc:
        terminal_status = ChatStatus.FAILED
        total_ms = round((time.perf_counter() - started_at) * 1000)
        logger.exception(
            "CHAT_REQUEST_FAILED | requestId=%s | sessionId=%s | userId=%s | totalMs=%s | status=FAILED | error=%s",
            request_id,
            session_id,
            user_id,
            total_ms,
            exc,
        )
        yield _sse_frame(
            "error",
            {
                "type": "error",
                "requestId": request_id,
                "code": "AI_PROCESSING_FAILED",
                "message": _STATUS_MESSAGES[ChatStatus.FAILED],
            },
        )

    yield _sse_frame(
        "done",
        {
            "type": "done",
            "requestId": request_id,
            "sessionId": session_id,
            "userId": user_id,
            "status": terminal_status.value,
        },
    )


@router.post("/chat/stream")
async def stream_agent_chat(request: AgentChatRequest) -> StreamingResponse:
    """Chat với ADK Agent qua luồng Server-Sent Events (SSE) dùng JSON payload."""
    session_id = (request.session_id or "").strip() or str(uuid4())
    resolved_attachments = [
        (item.filename, item.mime_type, _resolve_attachment_bytes(item))
        for item in request.attachments
    ]
    user_content = _build_user_content(request.message, resolved_attachments)
    runner = get_agent_runner()

    return StreamingResponse(
        _stream_agent_events(
            runner=runner,
            user_id=request.user_id,
            session_id=session_id,
            user_content=user_content,
            stream_tokens=request.stream_tokens,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat/upload/stream")
async def stream_agent_chat_with_upload(
    message: Annotated[str, Form()] = "",
    user_id: Annotated[str, Form()] = "default_user",
    session_id: Annotated[str | None, Form()] = None,
    stream_tokens: Annotated[bool, Form()] = True,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> StreamingResponse:
    """Chat với ADK Agent qua SSE kèm upload file trực tiếp (multipart/form-data)."""
    resolved_session_id = (session_id or "").strip() or str(uuid4())
    resolved_attachments: list[tuple[str, str, bytes]] = []
    for upload in files or []:
        raw_bytes = await upload.read()
        filename = upload.filename or f"upload_{uuid4().hex[:8]}.md"
        mime_type = upload.content_type or "text/markdown"
        resolved_attachments.append((filename, mime_type, raw_bytes))

    user_content = _build_user_content(message, resolved_attachments)
    runner = get_agent_runner()

    return StreamingResponse(
        _stream_agent_events(
            runner=runner,
            user_id=user_id,
            session_id=resolved_session_id,
            user_content=user_content,
            stream_tokens=stream_tokens,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/sessions/{session_id}")
async def get_agent_session(
    session_id: str,
    user_id: Annotated[str, Query(min_length=1)] = "default_user",
) -> dict[str, Any]:
    """Lấy trạng thái (`state`) và số lượng sự kiện của một phiên chat ADK."""
    runner = get_agent_runner()
    session = await runner.session_service.get_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found for user '{user_id}'.",
        )
    return {
        "sessionId": session.id,
        "userId": session.user_id,
        "appName": session.app_name,
        "state": dict(session.state),
        "eventCount": len(session.events),
    }
