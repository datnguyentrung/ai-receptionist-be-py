import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.utils import ingestion_logger


def test_model_response_logs_token_usage(tmp_path: Path, monkeypatch) -> None:
    log_file = tmp_path / "log.txt"
    monkeypatch.setattr(ingestion_logger, "LOG_FILE", log_file)
    plugin = ingestion_logger.ADKDetailedLoggerPlugin()

    asyncio.run(
        plugin.after_model_callback(
            callback_context=SimpleNamespace(agent=SimpleNamespace(name="batch")),
            llm_response=SimpleNamespace(
                content=None,
                usage_metadata={
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 15,
                },
            ),
        )
    )

    content = log_file.read_text(encoding="utf-8")
    assert "TOKEN USAGE (batch)" in content
    assert "promptTokenCount:        12" in content
    assert "totalTokenCount:         15" in content
    assert '"usage"' not in content


def test_logger_omits_raw_event_prompt_and_user_payload(tmp_path: Path, monkeypatch) -> None:
    log_file = tmp_path / "log.txt"
    monkeypatch.setattr(ingestion_logger, "LOG_FILE", log_file)
    plugin = ingestion_logger.ADKDetailedLoggerPlugin()

    asyncio.run(
        plugin.on_event_callback(
            invocation_context=object(), event=SimpleNamespace(secret="event-secret")
        )
    )
    asyncio.run(
        plugin.on_user_message_callback(
            invocation_context=object(),
            user_message=SimpleNamespace(
                parts=[SimpleNamespace(text="hello")], secret="user-secret"
            ),
        )
    )
    asyncio.run(
        plugin.before_model_callback(
            callback_context=SimpleNamespace(agent=SimpleNamespace(name="root")),
            llm_request=SimpleNamespace(
                model="model",
                contents=[SimpleNamespace(parts=[SimpleNamespace(text="prompt-secret")])],
                tools=[],
            ),
        )
    )

    content = log_file.read_text(encoding="utf-8")
    assert "ADK EVENT TRACE" not in content
    assert "event-secret" not in content
    assert '"message": "hello"' in content
    assert "user-secret" not in content
    assert '"contentCount": 1' in content
    assert '"contentPartCount": 1' in content
    assert "prompt-secret" not in content


def test_tool_request_and_response_remain_detailed(tmp_path: Path, monkeypatch) -> None:
    log_file = tmp_path / "log.txt"
    monkeypatch.setattr(ingestion_logger, "LOG_FILE", log_file)
    plugin = ingestion_logger.ADKDetailedLoggerPlugin()
    tool = SimpleNamespace(name="example_tool")

    asyncio.run(
        plugin.before_tool_callback(
            tool=tool, tool_args={"request": {"id": 1}}, tool_context=object()
        )
    )
    asyncio.run(
        plugin.after_tool_callback(
            tool=tool,
            tool_args={},
            tool_context=object(),
            result={"result": {"id": 1}},
        )
    )

    content = log_file.read_text(encoding="utf-8")
    assert '"arguments": {' in content
    assert '"result": {' in content
    assert '"id": 1' in content


def test_model_error_retries_using_retry_delay(tmp_path: Path, monkeypatch) -> None:
    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    async def response_stream(*_args, **_kwargs):
        yield "recovered"

    monkeypatch.setattr(ingestion_logger, "LOG_FILE", tmp_path / "log.txt")
    monkeypatch.setattr(ingestion_logger.asyncio, "sleep", fake_sleep)
    plugin = ingestion_logger.ADKDetailedLoggerPlugin()
    agent = SimpleNamespace(name="batch", canonical_model=SimpleNamespace(generate_content_async=response_stream))

    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=SimpleNamespace(agent=agent),
            llm_request=object(),
            error=RuntimeError("429 RESOURCE_EXHAUSTED {'retryDelay': '57s'}"),
        )
    )

    assert result == "recovered"
    assert waits == [57.0]


def test_model_error_does_not_retry_non_quota_error(monkeypatch) -> None:
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("must not retry")

    plugin = ingestion_logger.ADKDetailedLoggerPlugin()
    agent = SimpleNamespace(name="batch", canonical_model=SimpleNamespace(generate_content_async=fail_if_called))

    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=SimpleNamespace(agent=agent),
            llm_request=object(),
            error=RuntimeError("invalid request"),
        )
    )

    assert result is None


def test_model_error_stops_after_three_quota_retries(
    tmp_path: Path, monkeypatch
) -> None:
    attempts = 0

    async def fake_sleep(_delay: float) -> None:
        return None

    async def always_rate_limited(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
        yield  # pragma: no cover - keeps this an async generator for the model API.

    monkeypatch.setattr(ingestion_logger.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ingestion_logger, "LOG_FILE", tmp_path / "log.txt")
    plugin = ingestion_logger.ADKDetailedLoggerPlugin()
    agent = SimpleNamespace(
        name="batch",
        canonical_model=SimpleNamespace(generate_content_async=always_rate_limited),
    )

    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=SimpleNamespace(agent=agent),
            llm_request=object(),
            error=RuntimeError("429 RESOURCE_EXHAUSTED"),
        )
    )

    assert result is None
    assert attempts == 3
