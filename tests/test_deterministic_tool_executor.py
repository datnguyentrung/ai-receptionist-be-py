"""Unit tests cho DeterministicToolExecutor và kiến trúc Planner -> Executor -> Formatter."""

import pytest
from types import SimpleNamespace

from app.agent.execution.deterministic_tool_executor import DeterministicToolExecutor
from app.agent.tools.calculation import add, divide, multiply, subtract
from app.schemas.tool_plan import ToolAction, ToolPlan


from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session


def make_invocation_context(state: dict | None = None) -> InvocationContext:
    """Tạo InvocationContext hợp lệ với session state."""
    session = Session(id="test_session_1", app_name="test_app", user_id="test_user")
    if state:
        session.state.update(state)
    return InvocationContext(
        session=session,
        session_service=InMemorySessionService(),
        invocation_id="inv_test_1",
        agent=None,
    )


@pytest.mark.anyio
async def test_executor_single_action_success():
    """Một action thành công: Executor chạy tool, ghi kết quả đúng vào temp:tool_result_context."""
    plan = ToolPlan(
        actions=[
            ToolAction(id="act_1", tool_name="add", arguments={"a": 10, "b": 20})
        ]
    )
    ctx = make_invocation_context({"temp:tool_plan": plan})
    executor = DeterministicToolExecutor(tools=[add, subtract, multiply, divide])

    events = [e async for e in executor.run_async(ctx)]
    assert len(events) == 1

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx is not None
    assert result_ctx["request_status"] == "SUCCESS"
    assert len(result_ctx["actions"]) == 1
    assert result_ctx["actions"][0]["status"] == "SUCCESS"
    assert result_ctx["actions"][0]["result"]["result"] == 30
    assert result_ctx["final_action_ids"] == ["act_1"]


@pytest.mark.anyio
async def test_executor_parallel_independent_actions():
    """Hai action độc lập (không có depends_on) được dispatch và cùng có trong kết quả."""
    plan = ToolPlan(
        actions=[
            ToolAction(id="act_1", tool_name="add", arguments={"a": 5, "b": 15}, depends_on=[]),
            ToolAction(id="act_2", tool_name="multiply", arguments={"a": 4, "b": 5}, depends_on=[]),
        ]
    )
    ctx = make_invocation_context({"temp:tool_plan": plan})
    executor = DeterministicToolExecutor(tools=[add, subtract, multiply, divide])

    _ = [e async for e in executor.run_async(ctx)]

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx["request_status"] == "SUCCESS"
    assert len(result_ctx["actions"]) == 2

    res_map = result_ctx["results_by_id"]
    assert res_map["act_1"]["result"] == 20
    assert res_map["act_2"]["result"] == 20
    assert set(result_ctx["final_action_ids"]) == {"act_1", "act_2"}


@pytest.mark.anyio
async def test_executor_sequential_dependency_resolves_reference():
    """Chuỗi action phụ thuộc: resolve đúng ${id.path} và không chạy bước sau trước khi có kết quả thật."""
    # (100 + 50) / 3
    plan = ToolPlan(
        actions=[
            ToolAction(id="act_1", tool_name="add", arguments={"a": 100, "b": 50}, depends_on=[]),
            ToolAction(
                id="act_2",
                tool_name="divide",
                arguments={"a": "${act_1.result}", "b": 3},
                depends_on=["act_1"],
            ),
        ]
    )
    ctx = make_invocation_context({"temp:tool_plan": plan})
    executor = DeterministicToolExecutor(tools=[add, subtract, multiply, divide])

    _ = [e async for e in executor.run_async(ctx)]

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx["request_status"] == "SUCCESS"
    assert len(result_ctx["actions"]) == 2

    act_1_rec = result_ctx["actions"][0]
    act_2_rec = result_ctx["actions"][1]

    assert act_1_rec["status"] == "SUCCESS"
    assert act_1_rec["result"]["result"] == 150

    assert act_2_rec["status"] == "SUCCESS"
    assert act_2_rec["resolved_arguments"] == {"a": 150, "b": 3}
    assert act_2_rec["result"]["result"] == 50
    assert result_ctx["final_action_ids"] == ["act_2"]


@pytest.mark.anyio
async def test_executor_tool_outside_allowlist_fails_safely():
    """Tool không nằm trong allowlist tạo payload lỗi an toàn, không crash."""
    plan = ToolPlan(
        actions=[
            ToolAction(id="act_1", tool_name="dangerous_tool", arguments={"cmd": "rm -rf"})
        ]
    )
    ctx = make_invocation_context({"temp:tool_plan": plan})
    executor = DeterministicToolExecutor(tools=[add, subtract, multiply, divide])

    _ = [e async for e in executor.run_async(ctx)]

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx["request_status"] == "FAILED"
    assert result_ctx["actions"][0]["status"] == "FAILED"
    assert "allowlist" in result_ctx["actions"][0]["error"]


@pytest.mark.anyio
async def test_executor_dependency_failure_skips_downstream_action():
    """Khi action tiên quyết lỗi (hoặc chia cho 0 / exception), action phụ thuộc bị đánh dấu SKIPPED."""
    def broken_tool(x: int):
        raise RuntimeError("External service down")

    plan = ToolPlan(
        actions=[
            ToolAction(id="act_1", tool_name="broken_tool", arguments={"x": 10}, depends_on=[]),
            ToolAction(id="act_2", tool_name="add", arguments={"a": "${act_1.result}", "b": 5}, depends_on=["act_1"]),
        ]
    )
    ctx = make_invocation_context({"temp:tool_plan": plan})
    executor = DeterministicToolExecutor(tools=[broken_tool, add])

    _ = [e async for e in executor.run_async(ctx)]

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx["request_status"] == "FAILED"
    assert result_ctx["actions"][0]["status"] == "FAILED"
    assert result_ctx["actions"][1]["status"] == "SKIPPED"
    assert "dependency" in result_ctx["actions"][1]["error"].lower()


@pytest.mark.anyio
async def test_executor_circular_dependency_detected():
    """Phát hiện chu trình phụ thuộc (cycle) và trả về lỗi an toàn trước khi gọi tool."""
    plan = ToolPlan(
        actions=[
            ToolAction(id="act_1", tool_name="add", arguments={"a": "${act_2.result}", "b": 1}, depends_on=["act_2"]),
            ToolAction(id="act_2", tool_name="add", arguments={"a": "${act_1.result}", "b": 1}, depends_on=["act_1"]),
        ]
    )
    ctx = make_invocation_context({"temp:tool_plan": plan})
    executor = DeterministicToolExecutor(tools=[add])

    _ = [e async for e in executor.run_async(ctx)]

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx["request_status"] == "FAILED"
    assert result_ctx["plan_status"] == "CYCLE_OR_DEPENDENCY_ERROR"


@pytest.mark.anyio
async def test_executor_missing_reference_path_fails_safely():
    """Tham chiếu đến action hoặc key không tồn tại tạo lỗi an toàn."""
    plan = ToolPlan(
        actions=[
            ToolAction(id="act_1", tool_name="add", arguments={"a": 10, "b": 20}, depends_on=[]),
            ToolAction(id="act_2", tool_name="add", arguments={"a": "${non_existent.val}", "b": 5}, depends_on=["act_1"]),
        ]
    )
    ctx = make_invocation_context({"temp:tool_plan": plan})
    executor = DeterministicToolExecutor(tools=[add])

    _ = [e async for e in executor.run_async(ctx)]

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx["actions"][1]["status"] == "FAILED"
    assert "Lỗi resolve tham số" in result_ctx["actions"][1]["error"]


@pytest.mark.anyio
async def test_executor_json_string_with_markdown_codeblock():
    """Executor parse thành công chuỗi JSON được bọc bởi markdown ```json ... ``` từ LLM text."""
    raw_text = """```json
    {
      "actions": [
        {
          "id": "act_1",
          "tool_name": "multiply",
          "arguments": {"a": 6, "b": 7},
          "depends_on": []
        }
      ]
    }
    ```"""
    ctx = make_invocation_context({"temp:tool_plan": raw_text})
    executor = DeterministicToolExecutor(tools=[add, subtract, multiply, divide])

    _ = [e async for e in executor.run_async(ctx)]

    result_ctx = ctx.session.state.get("temp:tool_result_context")
    assert result_ctx["request_status"] == "SUCCESS"
    assert result_ctx["actions"][0]["result"]["result"] == 42

