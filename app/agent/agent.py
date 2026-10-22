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
from app.agent.routing.agent import create_root_agent
from app.utils.ingestion_logger import (
    ADKDetailedLoggerPlugin,
    reset_log_file,
)

# Khởi tạo Root Router đa domain
root_agent = create_root_agent()

# Reset file log đúng 1 lần khi server ADK khởi động (adk web / adk run)
reset_log_file()

# Cấu hình ứng dụng ADK đa domain
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
    "create_root_agent",
    "root_agent",
]
