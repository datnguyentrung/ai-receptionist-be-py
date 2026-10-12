"""Unit tests cho SystemInstructionDiagnosticsPlugin."""

import logging
from types import SimpleNamespace

import pytest
from google.genai import types

from app.agent.plugins.system_instruction_diagnostics_plugin import (
    SystemInstructionDiagnosticsPlugin,
    compute_instruction_fingerprint,
)


def make_dummy_request(system_instruction: str | None) -> SimpleNamespace:
    """Tạo request giả lập với GenerateContentConfig."""
    config = types.GenerateContentConfig(
        system_instruction=system_instruction
    ) if system_instruction is not None else None
    return SimpleNamespace(config=config)


def make_dummy_context(agent_name: str, session_id: str = "sess_001") -> SimpleNamespace:
    """Tạo context giả lập có session và agent_name."""
    session = SimpleNamespace(id=session_id)
    return SimpleNamespace(
        agent_name=agent_name,
        session=session,
        agent=SimpleNamespace(name=agent_name),
    )


@pytest.mark.anyio
async def test_compute_instruction_fingerprint_consistency():
    """Kiểm tra fingerprint SHA-256 tính toán nhất quán cho cùng nội dung."""
    fp1 = compute_instruction_fingerprint("Instruction A")
    fp2 = compute_instruction_fingerprint("Instruction A")
    fp3 = compute_instruction_fingerprint("Instruction B")

    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 64


@pytest.mark.anyio
async def test_different_agents_consecutive_runs_no_drift_warning(caplog):
    """Hai agent khác nhau chạy liên tiếp không tạo cảnh báo drift chéo agent."""
    plugin = SystemInstructionDiagnosticsPlugin()
    caplog.set_level(logging.WARNING, logger="system_instruction_diagnostics")

    ctx_tool = make_dummy_context("calculation_tool_agent")
    req_tool = make_dummy_request("System instruction for calculation tool agent")

    ctx_format = make_dummy_context("calculation_format_agent")
    req_format = make_dummy_request("System instruction for calculation format agent")

    # Lượt 1: Tool Agent chạy
    await plugin.before_model_callback(callback_context=ctx_tool, llm_request=req_tool)

    # Lượt 2: Format Agent chạy (có system instruction khác hẳn)
    await plugin.before_model_callback(callback_context=ctx_format, llm_request=req_format)

    # Không được có log warning nào vì đây là hai agent khác nhau
    drift_logs = [record for record in caplog.records if "[SYSTEM_INSTRUCTION_DRIFT]" in record.message]
    assert len(drift_logs) == 0


@pytest.mark.anyio
async def test_same_agent_stable_across_turns_no_warning(caplog):
    """Cùng một agent qua nhiều lượt có fingerprint ổn định không tạo cảnh báo."""
    plugin = SystemInstructionDiagnosticsPlugin()
    caplog.set_level(logging.WARNING, logger="system_instruction_diagnostics")

    ctx = make_dummy_context("calculation_tool_agent")
    req = make_dummy_request("Constant system prompt")

    # Chạy 3 lượt liên tiếp với cùng prompt
    await plugin.before_model_callback(callback_context=ctx, llm_request=req)
    await plugin.before_model_callback(callback_context=ctx, llm_request=req)
    await plugin.before_model_callback(callback_context=ctx, llm_request=req)

    drift_logs = [record for record in caplog.records if "[SYSTEM_INSTRUCTION_DRIFT]" in record.message]
    assert len(drift_logs) == 0


@pytest.mark.anyio
async def test_same_agent_instruction_drift_generates_single_warning_without_raw_prompt(caplog):
    """Cùng một agent bị thay đổi instruction sẽ phát ra đúng 1 cảnh báo drift, không lộ prompt thô."""
    plugin = SystemInstructionDiagnosticsPlugin()
    caplog.set_level(logging.WARNING, logger="system_instruction_diagnostics")

    ctx = make_dummy_context("calculation_tool_agent")
    secret_prompt_v1 = "CONFIDENTIAL_SYSTEM_PROMPT_VERSION_1"
    secret_prompt_v2 = "CONFIDENTIAL_SYSTEM_PROMPT_VERSION_2"

    req_v1 = make_dummy_request(secret_prompt_v1)
    req_v2 = make_dummy_request(secret_prompt_v2)

    # Lượt 1: Khởi tạo với prompt v1
    await plugin.before_model_callback(callback_context=ctx, llm_request=req_v1)

    # Lượt 2: Prompt bị đổi sang v2 trong cùng session
    await plugin.before_model_callback(callback_context=ctx, llm_request=req_v2)

    drift_logs = [record for record in caplog.records if "[SYSTEM_INSTRUCTION_DRIFT]" in record.message]
    assert len(drift_logs) == 1

    warning_msg = drift_logs[0].message
    assert "calculation_tool_agent" in warning_msg
    assert "sess_001" in warning_msg
    assert "status: DRIFT_DETECTED" in warning_msg

    # Tuyệt đối không log nội dung prompt thô (CONFIDENTIAL_SYSTEM_PROMPT...)
    assert secret_prompt_v1 not in warning_msg
    assert secret_prompt_v2 not in warning_msg
