"""Agent execution engine and loop."""

import json
import os
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.plugins.context_filter_plugin import ContextFilterPlugin
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types

from app.agent.skills.local_skill_registry import LocalSkillRegistry
from app.agent.skills.root_prompt_renderer import render_root_agent_prompt
from app.agent.skills.skill_loader import (
    discover_skill_descriptors,
    discover_skill_tools,
)
from app.core.ingestion_runtime import IngestionRuntimePlugin
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
        model=os.getenv("GOOGLE_ADK_MODEL", "gemini-3.5-flash-lite"),
        description=(
            "A root agent that dynamically routes requests to available skills."
        ),
        instruction=root_instruction,
        tools=[skill_toolset],
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
        ),
    )


def _compact_ingestion_context(
    contents: list[types.Content],
) -> list[types.Content]:
    last_submit_index = None
    last_submit_response = None

    for index, content in enumerate(contents):
        for part in content.parts or []:
            response = part.function_response

            if response is not None and response.name == "submit_ingestion_batch":
                last_submit_index = index
                last_submit_response = response.response

    if last_submit_index is None:
        return contents

    result: list[types.Content] = []

    for content in contents:
        if content.role != "user":
            continue

        has_function_response = any(
            part.function_response is not None for part in content.parts or []
        )

        if not has_function_response:
            result.append(content)
            break

    # 2. Giữ cặp load_skill để Agent vẫn có SKILL.md.
    for index, content in enumerate(contents[:last_submit_index]):
        has_load_skill = any(
            part.function_call is not None and part.function_call.name == "load_skill"
            for part in content.parts or []
        )

        if not has_load_skill:
            continue

        result.append(content)

        if index + 1 < len(contents):
            result.append(contents[index + 1])

        break

    # 3. Biến submit-result mới nhất thành checkpoint nhỏ.
    response = last_submit_response or {}

    checkpoint = {
        "ingestionId": response.get("ingestionId"),
        "success": response.get("success"),
        "stage": response.get("stage"),
        "terminal": response.get("terminal"),
        "retryRequired": response.get("retryRequired"),
        "nextAction": response.get("nextAction"),
        "processedBatches": response.get("processedBatches"),
        "remainingBatches": response.get("remainingBatches"),
        "nextBatch": response.get("nextBatch"),
        "batchIndex": response.get("batchIndex"),
        "scopeKeys": response.get("scopeKeys"),
        "affectedChunkIndexes": response.get("affectedChunkIndexes"),
        "errors": response.get("errors"),
        "validationAttempts": response.get("validationAttempts"),
        "attempt": response.get("attempt"),
        "maxAttempts": response.get("maxAttempts"),
    }

    compact_checkpoint = {
        key: value for key, value in checkpoint.items() if value is not None
    }

    checkpoint_text = "INGESTION_CHECKPOINT\n" + json.dumps(
        compact_checkpoint,
        ensure_ascii=False,
    )

    result.append(
        types.Content(
            role="user",
            parts=[types.Part(text=checkpoint_text)],
        )
    )

    # 4. Sau mọi submit, bỏ toàn bộ raw history của batch vừa xử lý.
    # Nếu cần repair, Agent sẽ lấy lại đúng batch/schema tối thiểu bằng tools.
    # Chỉ giữ các turn thực sự phát sinh sau submit mới nhất.
    result.extend(contents[last_submit_index + 1 :])

    return result


root_agent = create_root_agent()


app = App(
    name="taekwondo_ingestion",
    root_agent=root_agent,
    plugins=[
        ADKDetailedLoggerPlugin(),
        IngestionRuntimePlugin(),
        ContextFilterPlugin(custom_filter=_compact_ingestion_context),
        SaveFilesAsArtifactsPlugin(),
    ],
)
