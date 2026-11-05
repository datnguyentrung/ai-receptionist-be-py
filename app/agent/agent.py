"""Entrypoint của hệ thống Google ADK đa domain."""

from google.adk.apps.app import App
from google.adk.plugins.save_files_as_artifacts_plugin import (
    SaveFilesAsArtifactsPlugin,
)

from app.agent.domains.ingestion.context_filter import (
    compact_ingestion_context as _compact_ingestion_context,
)
from app.agent.plugins import (
    SystemInstructionDiagnosticsPlugin,
    ToolAutoCorrectPlugin,
)
from app.agent.routing.hybrid_root_agent import HybridRootAgent
from app.agent.routing.routing_agent import create_llm_fallback_agent
from app.utils.ingestion_logger import (
    ADKDetailedLoggerPlugin,
    reset_log_file,
)

# Root Agent cũ trở thành LLM fallback.
llm_fallback_agent = create_llm_fallback_agent()

# Root Agent mới được ADK Web sử dụng.
root_agent = HybridRootAgent(
    name="root_agent",
    description="Hybrid Clef + LLM Router",
    sub_agents=[llm_fallback_agent],
)

reset_log_file()

app = App(
    name="taekwondo_assistant",
    root_agent=root_agent,
    plugins=[
        ToolAutoCorrectPlugin(),
        SystemInstructionDiagnosticsPlugin(),
        ADKDetailedLoggerPlugin(),
        SaveFilesAsArtifactsPlugin(),
    ],
)

__all__ = [
    "_compact_ingestion_context",
    "app",
    "root_agent",
]
