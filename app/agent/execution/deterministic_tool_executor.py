"""Deterministic Tool Executor implementation for Google ADK.

Mô tả:
    DeterministicToolExecutor kế thừa BaseAgent, thực thi các hành động trong ToolPlan
    được lưu trữ tại session.state['temp:tool_plan'].
    Executor thực hiện:
    1. Kiểm tra allowlist của tool.
    2. Kiểm tra dependency graph và phát hiện chu trình (cycle detection).
    3. Thực thi song song các action độc lập (không có depends_on).
    4. Thực thi theo topological order với các action có dependency.
    5. Resolve tham chiếu `${action_id.result_path}` vào kết quả đã xác thực.
    6. Khi dependency lỗi hoặc bị skip, đánh dấu action phụ thuộc là skipped; không bịa input hay retry.
    7. Ghi duy nhất vào session.state['temp:tool_result_context'] payload JSON/Dict có cấu trúc chuẩn.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

from google.adk.agents.base_agent import BaseAgent
from google.adk.events import Event

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext

from app.schemas.tool_plan import ToolAction, ToolPlan


from pydantic import Field, PrivateAttr


class DeterministicToolExecutor(BaseAgent):
    """Agent thực thi tool một cách tất định từ kế hoạch ToolPlan."""

    input_plan_key: str = Field(
        default="temp:tool_plan",
        description="Key lưu trữ ToolPlan trong session state.",
    )
    output_context_key: str = Field(
        default="temp:tool_result_context",
        description="Key lưu trữ kết quả thực thi tool trong session state.",
    )
    _tools_map: dict[str, Callable[..., Any]] = PrivateAttr(default_factory=dict)

    def __init__(
        self,
        name: str = "deterministic_tool_executor",
        description: str = "Thực thi kế hoạch tool một cách tất định dựa trên ToolPlan.",
        tools: list[Callable[..., Any]] | None = None,
        input_plan_key: str = "temp:tool_plan",
        output_context_key: str = "temp:tool_result_context",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            input_plan_key=input_plan_key,
            output_context_key=output_context_key,
            **kwargs,
        )
        self._tools_map = {}
        if tools:
            for t in tools:
                tool_name = getattr(t, "name", None) or getattr(t, "__name__", None)
                if tool_name:
                    self._tools_map[tool_name] = t

    @property
    def allowed_tool_names(self) -> set[str]:
        return set(self._tools_map.keys())

    def _resolve_reference(
        self,
        ref_str: str,
        results_by_id: dict[str, Any],
    ) -> Any:
        """Resolve cú pháp ${action_id.path} vào results_by_id."""
        pattern = r"^\$\{([a-zA-Z0-9_\-]+)(\.([a-zA-Z0-9_\.\-]+))?\}$"
        match = re.match(pattern, ref_str.strip())
        if not match:
            # Không phải dạng tham chiếu toàn phần
            return ref_str

        action_id = match.group(1)
        subpath = match.group(3)

        if action_id not in results_by_id:
            raise KeyError(f"Tham chiếu tới action_id không tồn tại hoặc chưa thành công: '{action_id}'")

        target = results_by_id[action_id]
        if subpath is None:
            return target

        # Trích xuất theo dấu chấm
        parts = subpath.split(".")
        current = target
        for p in parts:
            if isinstance(current, dict):
                if p not in current:
                    raise KeyError(f"Khóa '{p}' không tồn tại trong kết quả của '{action_id}'")
                current = current[p]
            elif hasattr(current, p):
                current = getattr(current, p)
            else:
                try:
                    idx = int(p)
                    if isinstance(current, (list, tuple)):
                        current = current[idx]
                    else:
                        raise KeyError(f"Không thể truy cập index {idx} trên đối tượng non-sequence")
                except (ValueError, IndexError):
                    raise KeyError(f"Không thể truy cập thuộc tính '{p}' trong kết quả của '{action_id}'")
        return current

    def _resolve_arguments(
        self,
        args: Any,
        results_by_id: dict[str, Any],
    ) -> Any:
        """Đệ quy resolve các tham chiếu trong arguments."""
        if isinstance(args, str):
            # Nếu toàn bộ chuỗi là ${...}
            if args.strip().startswith("${") and args.strip().endswith("}"):
                return self._resolve_reference(args, results_by_id)
            # Nếu chuỗi chứa nội suy inline: ví dụ "prefix_${act.id}_suffix"
            pattern = r"\$\{([a-zA-Z0-9_\-]+(?:\.[a-zA-Z0-9_\.\-]+)?)\}"
            if re.search(pattern, args):
                def _sub(m: re.Match[str]) -> str:
                    full_ref = f"${{{m.group(1)}}}"
                    res = self._resolve_reference(full_ref, results_by_id)
                    return str(res)
                return re.sub(pattern, _sub, args)
            return args
        elif isinstance(args, dict):
            return {k: self._resolve_arguments(v, results_by_id) for k, v in args.items()}
        elif isinstance(args, list):
            return [self._resolve_arguments(item, results_by_id) for item in args]
        return args

    def _detect_cycle_or_invalid_dependencies(
        self,
        actions: list[ToolAction],
    ) -> str | None:
        """Kiểm tra dependency graph: thiếu dependency hoặc có cycle."""
        action_ids = {a.id for a in actions}
        adj: dict[str, list[str]] = {}
        in_degree: dict[str, int] = {a.id: 0 for a in actions}

        for a in actions:
            adj[a.id] = []

        for a in actions:
            for dep in a.depends_on:
                if dep not in action_ids:
                    return f"Action '{a.id}' phụ thuộc vào action không tồn tại: '{dep}'"
                adj[dep].append(a.id)
                in_degree[a.id] += 1

        # Kahn's algorithm for topological sorting & cycle detection
        queue = [act_id for act_id, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            curr = queue.pop(0)
            visited_count += 1
            for nxt in adj[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if visited_count < len(actions):
            return "Phát hiện chu trình phụ thuộc (circular dependency / cycle) trong ToolPlan"

        return None

    async def _invoke_tool(
        self,
        tool_fn: Callable[..., Any],
        resolved_args: dict[str, Any],
    ) -> Any:
        """Gọi tool hàm đồng bộ hoặc bất đồng bộ."""
        if inspect.iscoroutinefunction(tool_fn):
            return await tool_fn(**resolved_args)
        return tool_fn(**resolved_args)

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Thực thi ToolPlan được lưu trữ trong session state."""
        session = ctx.session
        state = session.state

        raw_plan = state.get(self.input_plan_key)

        # Chuẩn hóa đối tượng ToolPlan
        parsed_plan: ToolPlan | None = None
        if isinstance(raw_plan, ToolPlan):
            parsed_plan = raw_plan
        elif isinstance(raw_plan, dict):
            try:
                parsed_plan = ToolPlan.model_validate(raw_plan)
            except Exception as e:
                error_ctx = {
                    "request_status": "FAILED",
                    "plan_status": "INVALID_SCHEMA",
                    "error": f"Không thể parse ToolPlan từ session state: {e}",
                    "actions": [],
                    "final_action_ids": [],
                }
                state[self.output_context_key] = error_ctx
                yield Event(author=self.name)
                return
        elif isinstance(raw_plan, str):
            clean_str = raw_plan.strip()
            # Strip markdown codeblock nếu model bọc trong ```json ... ```
            if "```" in clean_str:
                code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_str)
                if code_block_match:
                    clean_str = code_block_match.group(1).strip()
            try:
                data = json.loads(clean_str)
                if isinstance(data, dict):
                    parsed_plan = ToolPlan.model_validate(data)
            except Exception as e:
                error_ctx = {
                    "request_status": "FAILED",
                    "plan_status": "INVALID_JSON",
                    "error": f"Không thể parse JSON ToolPlan: {e}",
                    "actions": [],
                    "final_action_ids": [],
                }
                state[self.output_context_key] = error_ctx
                yield Event(author=self.name)
                return

        if parsed_plan is None or not parsed_plan.actions:
            empty_ctx = {
                "request_status": "NO_ACTIONS",
                "plan_status": "EMPTY_PLAN",
                "actions": [],
                "final_action_ids": [],
            }
            state[self.output_context_key] = empty_ctx
            yield Event(author=self.name)
            return

        actions = parsed_plan.actions

        # 1. Kiểm tra cycle hoặc dependency không hợp lệ
        cycle_err = self._detect_cycle_or_invalid_dependencies(actions)
        if cycle_err:
            error_ctx = {
                "request_status": "FAILED",
                "plan_status": "CYCLE_OR_DEPENDENCY_ERROR",
                "error": cycle_err,
                "actions": [
                    {
                        "id": a.id,
                        "tool_name": a.tool_name,
                        "status": "FAILED",
                        "error": cycle_err,
                    }
                    for a in actions
                ],
                "final_action_ids": [],
            }
            state[self.output_context_key] = error_ctx
            yield Event(author=self.name)
            return

        # 2. Chuẩn bị thực thi theo dependency levels
        action_by_id = {a.id: a for a in actions}
        results_by_id: dict[str, Any] = {}
        action_records: dict[str, dict[str, Any]] = {}

        # Tính in-degree và danh sách phụ thuộc ngược
        in_degree: dict[str, int] = {a.id: len(a.depends_on) for a in actions}
        dependents: dict[str, list[str]] = {a.id: [] for a in actions}
        for a in actions:
            for dep in a.depends_on:
                dependents[dep].append(a.id)

        # Xác định các action lá (final actions - không ai phụ thuộc vào nó)
        final_action_ids = [a.id for a in actions if not dependents[a.id]]
        if not final_action_ids:
            final_action_ids = [actions[-1].id]

        ready_queue = [a.id for a in actions if in_degree[a.id] == 0]
        skipped_or_failed_ids: set[str] = set()

        while ready_queue:
            current_batch = list(ready_queue)
            ready_queue.clear()

            async def _execute_single_action(act_id: str) -> None:
                act = action_by_id[act_id]
                rec: dict[str, Any] = {
                    "id": act.id,
                    "tool_name": act.tool_name,
                    "arguments": act.arguments,
                    "depends_on": act.depends_on,
                }

                # Kiểm tra nếu bất kỳ dependency nào bị failed hoặc skipped
                failed_deps = [d for d in act.depends_on if d in skipped_or_failed_ids]
                if failed_deps:
                    rec["status"] = "SKIPPED"
                    rec["error"] = f"Bị bỏ qua do dependency '{failed_deps[0]}' thất bại hoặc bị bỏ qua"
                    skipped_or_failed_ids.add(act_id)
                    action_records[act_id] = rec
                    return

                # Kiểm tra allowlist
                if act.tool_name not in self._tools_map:
                    rec["status"] = "FAILED"
                    rec["error"] = f"Tool '{act.tool_name}' không nằm trong allowlist của domain"
                    skipped_or_failed_ids.add(act_id)
                    action_records[act_id] = rec
                    return

                # Resolve arguments
                try:
                    resolved_args = self._resolve_arguments(act.arguments, results_by_id)
                    rec["resolved_arguments"] = resolved_args
                except Exception as ex:
                    rec["status"] = "FAILED"
                    rec["error"] = f"Lỗi resolve tham số: {ex}"
                    skipped_or_failed_ids.add(act_id)
                    action_records[act_id] = rec
                    return

                # Gọi tool
                tool_fn = self._tools_map[act.tool_name]
                try:
                    tool_output = await self._invoke_tool(tool_fn, resolved_args)
                    rec["status"] = "SUCCESS"
                    rec["result"] = tool_output
                    results_by_id[act.id] = tool_output
                except Exception as ex:
                    rec["status"] = "FAILED"
                    rec["error"] = f"Lỗi khi thực thi tool '{act.tool_name}': {ex}"
                    skipped_or_failed_ids.add(act_id)

                action_records[act_id] = rec

            # Chạy song song các action độc lập trong cùng batch
            await asyncio.gather(*[_execute_single_action(act_id) for act_id in current_batch])

            # Cập nhật in_degree cho các action phụ thuộc
            for completed_id in current_batch:
                for nxt_id in dependents[completed_id]:
                    in_degree[nxt_id] -= 1
                    if in_degree[nxt_id] == 0:
                        ready_queue.append(nxt_id)

        # 3. Tổng hợp payload tool_result_context
        ordered_actions = [action_records[a.id] for a in actions if a.id in action_records]
        has_failure = any(rec.get("status") in ("FAILED", "SKIPPED") for rec in ordered_actions)

        final_request_status = "PARTIAL_SUCCESS" if (has_failure and results_by_id) else (
            "FAILED" if has_failure else "SUCCESS"
        )

        result_context = {
            "request_status": final_request_status,
            "actions": ordered_actions,
            "final_action_ids": final_action_ids,
            "results_by_id": results_by_id,
        }

        state[self.output_context_key] = result_context

        # Tạo Event hoàn thành của executor
        yield Event(author=self.name)


__all__ = ["DeterministicToolExecutor"]
