"""Agent execution engine and loop."""

import os
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.plugins.context_filter_plugin import ContextFilterPlugin
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin
from google.adk.tools.skill_toolset import SkillToolset
from google.adk.models.google_llm import Gemini
from google.genai import types
from google.genai.types import HttpRetryOptions

from app.agent.skills.local_skill_registry import LocalSkillRegistry
from app.agent.skills.root_prompt_renderer import render_root_agent_prompt
from app.agent.skills.skill_loader import (
    discover_skill_descriptors,
    discover_skill_tools,
)
from app.utils.ingestion_logger import ADKDetailedLoggerPlugin

BASE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = BASE_DIR / "skills"
ROOT_AGENT_PROMPT_PATH = BASE_DIR / "prompts" / "root_agent_prompt.md"


def create_root_agent() -> Agent:
    descriptors = discover_skill_descriptors(SKILLS_DIR)

    if not descriptors:
        raise RuntimeError(f"No valid SKILL.md files were found under {SKILLS_DIR}.")

    additional_tools = discover_skill_tools(descriptors)
    registry = LocalSkillRegistry(descriptors)

    root_instruction = render_root_agent_prompt(
        prompt_path=ROOT_AGENT_PROMPT_PATH,
        skill_descriptors=descriptors,
    )

    skill_toolset = SkillToolset(
        skills=[],
        registry=registry,
        additional_tools=additional_tools,
    )

    return Agent(
        name="root_agent",
        model=Gemini(
            model=os.getenv("GOOGLE_ADK_MODEL", "gemini-3.5-flash-lite"),
            retry_options=HttpRetryOptions(
                attempts=5,
                initial_delay=3.0,
                max_delay=30.0,
                http_status_codes=[429, 503],
            ),
        ),
        description=(
            "A root agent that dynamically routes requests to available skills."
        ),
        instruction=root_instruction,
        tools=[skill_toolset],
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.MEDIUM
            ),
        ),
    )


root_agent = create_root_agent()


def _compact_ingestion_context(contents: list[types.Content]) -> list[types.Content]:
    """
    Rút gọn lịch sử hội thoại của Root Agent trong quá trình ingestion.

    Mục đích:
        - Giảm context window, token cost và nhiễu từ các batch cũ.
        - Giữ nguyên yêu cầu ban đầu của User.
        - Giữ cặp Function Call/Response của load_skill.
        - Giữ cặp Function Call/Response của ingestion_batch_agent gần nhất.
        - Giữ toàn bộ nội dung phát sinh sau batch gần nhất.
        - Bảo toàn thứ tự thời gian của các Content được giữ lại.

    Nguyên tắc:
        Chỉ loại bỏ lịch sử không cần thiết, không chỉnh sửa nội dung
        của các Content được giữ lại.

    Lưu ý:
        Giả định các tool call được xử lý tuần tự và mỗi call có response.
        Chưa xử lý đầy đủ parallel tool calls hoặc thought signatures.
    """

    # Lưu vị trí Function Call đang chờ Response và các cặp đã hoàn tất.
    # Key: tên tool; Value: index trong contents hoặc tuple (call_index, response_index).
    calls, pairs = {}, {}

    # Duyệt toàn bộ lịch sử để xác định các cặp Function Call/Response.
    for i, content in enumerate(contents):
        for part in content.parts or []:
            # Ghi nhận vị trí gọi tool.
            if part.function_call:
                calls[part.function_call.name] = i

            # Khi nhận Response, ghép với Call tương ứng trước đó.
            elif part.function_response:
                name = part.function_response.name
                if name in calls:
                    pairs[name] = (calls.pop(name), i)

    # Lấy cặp Call/Response của AgentTool ingestion gần nhất.
    batch = pairs.get("ingestion_batch_agent")

    # Chưa có batch hoàn tất thì giữ nguyên toàn bộ context.
    if not batch:
        return contents

    # Tìm User turn đầu tiên, bỏ qua các turn chứa thuần Function Response.
    # Đây là yêu cầu gốc giúp Root Agent duy trì mục tiêu ingestion.
    first_user = next(
        (
            i
            for i, c in enumerate(contents)
            if c.role == "user"
            and any(p.function_response is None for p in c.parts or [])
        ),
        None,
    )

    # Đánh dấu các Content cần giữ:
    # 1. Call/Response của AgentTool gần nhất.
    # 2. Call/Response của load_skill gần nhất (nếu có).
    keep = set(batch) | set(pairs.get("load_skill", ()))

    # Bổ sung yêu cầu User ban đầu nếu tìm thấy.
    if first_user is not None:
        keep.add(first_user)

    # Lọc theo thứ tự gốc của contents:
    # - Giữ các Content đã đánh dấu.
    # - Giữ toàn bộ Content sau Response của batch gần nhất.
    # - Loại bỏ các Content cũ còn lại để giảm token.
    return [c for i, c in enumerate(contents) if i in keep or i > batch[1]]


app = App(
    name="taekwondo_assistant",
    root_agent=root_agent,
    plugins=[
        ADKDetailedLoggerPlugin(),
        ContextFilterPlugin(custom_filter=_compact_ingestion_context),
        SaveFilesAsArtifactsPlugin(),
    ],
)
