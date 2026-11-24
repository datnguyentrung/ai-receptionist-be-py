"""Unit tests for the FastAPI SSE Agent Chat endpoints."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

_SPEC = importlib.util.spec_from_file_location(
    "app_api_agent_api",
    Path(__file__).resolve().parents[1] / "app" / "api" / "agent_api.py",
)
assert _SPEC is not None and _SPEC.loader is not None
agent_api = importlib.util.module_from_spec(_SPEC)
sys.modules["app_api_agent_api"] = agent_api
_SPEC.loader.exec_module(agent_api)


def _parse_sse_stream(raw_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_payload = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data_payload = json.loads(line.removeprefix("data: ").strip())
        events.append((event_name, data_payload))
    return events


class _FakeRunner:
    app_name = "taekwondo_assistant"

    def __init__(self) -> None:
        self.last_call: dict = {}
        self.session_service = SimpleNamespace(
            get_session=self._get_session,
        )

    async def _get_session(self, *, app_name: str, user_id: str, session_id: str):
        if session_id == "missing":
            return None
        return SimpleNamespace(
            id=session_id,
            user_id=user_id,
            app_name=app_name,
            state={"active_ingestion_id": "ing-123"},
            events=[1, 2],
        )

    async def run_async(self, *, user_id: str, session_id: str, new_message, run_config):
        self.last_call = {
            "user_id": user_id,
            "session_id": session_id,
            "new_message": new_message,
            "run_config": run_config,
        }
        # 1. Tool call event
        yield SimpleNamespace(
            author="INGESTION_AGENT",
            invocation_id="inv-1",
            partial=False,
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        thought=False,
                        text=None,
                        function_call=SimpleNamespace(
                            id="call-1",
                            name="begin_ingestion",
                            args={"artifact_name": "doc.md"},
                        ),
                        function_response=None,
                    )
                ]
            ),
        )
        # 2. Tool response event
        yield SimpleNamespace(
            author="INGESTION_AGENT",
            invocation_id="inv-1",
            partial=False,
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        thought=False,
                        text=None,
                        function_call=None,
                        function_response=SimpleNamespace(
                            id="call-1",
                            name="begin_ingestion",
                            response={"success": True, "stage": "batching"},
                        ),
                    )
                ]
            ),
        )
        # 3. Partial token chunk
        yield SimpleNamespace(
            author="INGESTION_AGENT",
            invocation_id="inv-1",
            partial=True,
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        thought=False,
                        text="Đang xử lý ",
                        function_call=None,
                        function_response=None,
                    )
                ]
            ),
        )
        # 4. Final message
        yield SimpleNamespace(
            author="INGESTION_AGENT",
            invocation_id="inv-1",
            partial=False,
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        thought=False,
                        text="Đang xử lý tài liệu của bạn.",
                        function_call=None,
                        function_response=None,
                    )
                ]
            ),
        )


def test_stream_agent_chat_sse_events_and_attachments(monkeypatch) -> None:
    fake_runner = _FakeRunner()
    monkeypatch.setattr(agent_api, "get_agent_runner", lambda: fake_runner)

    test_app = FastAPI()
    test_app.include_router(agent_api.router)
    client = TestClient(test_app)

    response = client.post(
        "/api/v1/agent/chat/stream",
        json={
            "message": "ingestion",
            "user_id": "coach-1",
            "session_id": "sess-99",
            "attachments": [
                {
                    "filename": "doc.md",
                    "mime_type": "text/markdown",
                    "text": "# Cẩm nang Taekwondo",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_stream(response.text)
    event_types = [name for name, _ in events]
    assert event_types == [
        "session",
        "tool_call",
        "tool_response",
        "token",
        "message",
        "done",
    ]
    assert events[0][1] == {"sessionId": "sess-99", "userId": "coach-1"}
    assert events[1][1]["toolCalls"][0]["name"] == "begin_ingestion"
    assert events[2][1]["toolResponses"][0]["response"]["stage"] == "batching"
    assert events[3][1]["text"] == "Đang xử lý "
    assert events[3][1]["partial"] is True
    assert events[4][1]["text"] == "Đang xử lý tài liệu của bạn."
    assert events[4][1]["partial"] is False

    sent_parts = fake_runner.last_call["new_message"].parts
    assert len(sent_parts) == 2
    assert sent_parts[0].text == "ingestion"
    assert sent_parts[1].inline_data.display_name == "doc.md"
    assert sent_parts[1].inline_data.data == "# Cẩm nang Taekwondo".encode()


def test_get_agent_session_endpoint(monkeypatch) -> None:
    fake_runner = _FakeRunner()
    monkeypatch.setattr(agent_api, "get_agent_runner", lambda: fake_runner)

    test_app = FastAPI()
    test_app.include_router(agent_api.router)
    client = TestClient(test_app)

    ok_res = client.get("/api/v1/agent/sessions/sess-99", params={"user_id": "coach-1"})
    assert ok_res.status_code == 200
    assert ok_res.json()["state"] == {"active_ingestion_id": "ing-123"}

    missing_res = client.get("/api/v1/agent/sessions/missing")
    assert missing_res.status_code == 404
