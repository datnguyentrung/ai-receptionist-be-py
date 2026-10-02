"""Agent execution engine and loop."""

import json
import os
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.plugins.context_filter_plugin import ContextFilterPlugin
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin
from google.adk.tools import AgentTool
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types

from app.agent.ingestion_extractor_agent import ingestion_extractor_agent
from app.agent.skills.local_skill_registry import LocalSkillRegistry
from app.agent.skills.root_prompt_renderer import render_root_agent_prompt
from app.agent.skills.skill_loader import (
    discover_skill_descriptors,
    discover_skill_tools,
)
from app.core.ingestion_runtime import IngestionRuntimePlugin
from app.utils.ingestion_logger import reset_log_file

# Clean log file every time ADK Web / CLI loads the agent
reset_log_file()

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
        sub_agents=[
            ingestion_extractor_agent,
        ],
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
        ),
    )


# quét ngược từ submit
# ↓
# tìm lần gọi get_ingestion_batch gần nhất
# ↓
# đó là đầu của ngữ cảnh batch hiện tại
def _find_current_batch_start(
    contents: list[types.Content],
    submit_index: int,
) -> int | None:
    for index in range(submit_index - 1, -1, -1):
        content = contents[index]

        for part in content.parts or []:
            call = part.function_call

            if call is not None and call.name == "get_ingestion_batch":
                return index

    return None


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
        "stage": response.get("stage"),
        "nextAction": response.get("nextAction"),
        "processedBatches": response.get("processedBatches"),
        "remainingBatches": response.get("remainingBatches"),
        "nextBatch": response.get("nextBatch"),
        "workspaceStats": response.get("workspaceStats"),
        "ontologyVersionId": response.get("ontologyVersionId"),
        # Thông tin cần để sửa đúng batch hiện tại
        "batchIndex": response.get("batchIndex"),
        "scopeKeys": response.get("scopeKeys"),
        "snapshotHashes": response.get("snapshotHashes"),
        "mergedSchemaHash": response.get("mergedSchemaHash"),
        "graphFragment": response.get("graphFragment"),
        "errors": response.get("errors"),
        "validationAttempts": response.get("validationAttempts"),
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

    # 4. Giữ context cho đến khi hoàn thành batch hiện tại
    ## Khi submit thành công
    # STAGED
    # ↓
    # compact mạnh
    # ↓
    # bỏ dữ liệu batch cũ
    # ↓
    # tiết kiệm token

    ## Khi submit lỗi
    # REPAIR_REQUIRED
    # ↓
    # giữ:
    # GET_BATCH
    # LIST_SCOPES
    # LOAD_SCOPES
    # EXTRACT
    # SUBMIT ERROR
    # ↓
    # model có đủ ngữ cảnh để sửa

    stage = response.get("stage")
    next_action = response.get("nextAction")

    needs_batch_context = stage in {
        "repair_required",
        "schema_gap_candidate",
    } or next_action in {
        "repair_batch",
        "assess_schema_gap",
    }

    if needs_batch_context:
        batch_start_index = _find_current_batch_start(
            contents,
            last_submit_index,
        )

        if batch_start_index is not None:
            result.extend(contents[batch_start_index : last_submit_index + 1])
    else:
        result.extend(contents[last_submit_index + 1 :])

    return result


root_agent = create_root_agent()


app = App(
    name="taekwondo_ingestion",
    root_agent=root_agent,
    plugins=[
        IngestionRuntimePlugin(),
        ContextFilterPlugin(custom_filter=_compact_ingestion_context),
        SaveFilesAsArtifactsPlugin(),
    ],
)
