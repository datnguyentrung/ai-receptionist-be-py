import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def cuda_runtime_is_usable() -> bool:
    """Check the NVIDIA runtime before asking ONNX Runtime to load CUDA."""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return False
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def resolve_execution_provider(
    requested_provider: str,
    *,
    available_providers: list[str],
    cuda_runtime_usable: bool | None = None,
) -> str:
    """Resolve auto/cpu/cuda without probing CUDA on CPU-only machines."""
    requested = requested_provider.strip().lower()
    if requested not in {"auto", "cpu", "cuda"}:
        raise ValueError(
            "FACE_EXECUTION_PROVIDER must be one of: auto, cpu, cuda"
        )
    if requested == "cpu":
        return "CPUExecutionProvider"

    cuda_usable = (
        cuda_runtime_is_usable()
        if cuda_runtime_usable is None
        else cuda_runtime_usable
    )
    if "CUDAExecutionProvider" in available_providers and cuda_usable:
        return "CUDAExecutionProvider"
    if requested == "cuda":
        raise RuntimeError(
            "FACE_EXECUTION_PROVIDER=cuda was requested, but "
            "CUDAExecutionProvider is not usable on this runtime."
        )
    logger.info(
        "insightface_cuda_unavailable_falling_back_to_cpu | "
        "availableProviders=%s | cudaRuntimeUsable=%s",
        available_providers,
        cuda_usable,
    )
    return "CPUExecutionProvider"
