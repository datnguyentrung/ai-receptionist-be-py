"""Unit tests cho ToolAutoCorrectPlugin."""

import pytest
from google.genai import types

from app.agent.plugins.tool_autocorrect_plugin import ToolAutoCorrectPlugin


class DummyAgent:
    """Agent giả lập chứa danh sách tools."""

    def __init__(self, tools):
        self.tools = tools


class DummyTool:
    """Tool giả lập có thuộc tính name."""

    def __init__(self, name):
        self.name = name


class DummySkillToolset:
    """SkillToolset giả lập chứa _provided_tools_by_name."""

    def __init__(self, tools_dict):
        self._provided_tools_by_name = tools_dict


class DummyCallbackContext:
    """CallbackContext giả lập."""

    pass


class DummyLlmResponse:
    """LlmResponse giả lập chứa content và parts."""

    def __init__(self, function_calls):
        parts = []
        for call_name in function_calls:
            part = types.Part(
                function_call=types.FunctionCall(
                    name=call_name,
                    args={},
                )
            )
            parts.append(part)
        self.content = types.Content(role="model", parts=parts)


@pytest.mark.anyio
async def test_tool_autocorrect_exact_match():
    """Kiểm tra tên tool đúng thì không bị sửa đổi."""
    plugin = ToolAutoCorrectPlugin(allowed_tools=["ingestion_batch_agent", "begin_ingestion"])
    response = DummyLlmResponse(["ingestion_batch_agent"])
    ctx = DummyCallbackContext()

    result = await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    assert result is None  # Không có thay đổi
    assert response.content.parts[0].function_call.name == "ingestion_batch_agent"


@pytest.mark.anyio
async def test_tool_autocorrect_typo_missing_first_char():
    """Kiểm tra tên tool bị nuốt ký tự đầu (ngestion_batch_agent) được sửa đúng thành ingestion_batch_agent."""
    plugin = ToolAutoCorrectPlugin(allowed_tools=["ingestion_batch_agent", "begin_ingestion"])
    response = DummyLlmResponse(["ngestion_batch_agent"])
    ctx = DummyCallbackContext()

    result = await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    assert result is not None  # Có thay đổi
    assert response.content.parts[0].function_call.name == "ingestion_batch_agent"


@pytest.mark.anyio
async def test_tool_autocorrect_typo_suffix_or_transposition():
    """Kiểm tra tên tool bị typo nhẹ (ingestion_batch_agnent) được sửa đúng."""
    plugin = ToolAutoCorrectPlugin(allowed_tools=["ingestion_batch_agent", "begin_ingestion"])
    response = DummyLlmResponse(["ingestion_batch_agnent"])
    ctx = DummyCallbackContext()

    result = await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    assert result is not None
    assert response.content.parts[0].function_call.name == "ingestion_batch_agent"


@pytest.mark.anyio
async def test_tool_autocorrect_unrelated_name_not_matched():
    """Kiểm tra tên tool hoàn toàn không liên quan (random_call) thì không bị sửa bừa."""
    plugin = ToolAutoCorrectPlugin(allowed_tools=["ingestion_batch_agent", "begin_ingestion"])
    response = DummyLlmResponse(["random_call"])
    ctx = DummyCallbackContext()

    result = await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    assert result is None
    assert response.content.parts[0].function_call.name == "random_call"


@pytest.mark.anyio
async def test_tool_autocorrect_auto_discover_tools_from_agent():
    """Kiểm tra plugin tự động khám phá danh sách tools từ Agent và SkillToolset qua before_agent_callback."""
    plugin = ToolAutoCorrectPlugin()
    st = DummySkillToolset({"ingestion_batch_agent": None, "create_schema_proposal": None})
    agent = DummyAgent([DummyTool("begin_ingestion"), st])
    ctx = DummyCallbackContext()

    await plugin.before_agent_callback(agent=agent, callback_context=ctx)

    response = DummyLlmResponse(["ngestion_batch_agent"])
    result = await plugin.after_model_callback(callback_context=ctx, llm_response=response)
    assert result is not None
    assert response.content.parts[0].function_call.name == "ingestion_batch_agent"
