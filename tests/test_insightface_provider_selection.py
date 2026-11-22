import pytest

from app.utils.insightface_provider import resolve_execution_provider


def test_auto_prefers_cuda_when_runtime_is_usable():
    assert resolve_execution_provider(
        "auto",
        available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        cuda_runtime_usable=True,
    ) == "CUDAExecutionProvider"


def test_auto_uses_cpu_when_cuda_runtime_is_unavailable():
    assert resolve_execution_provider(
        "auto",
        available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        cuda_runtime_usable=False,
    ) == "CPUExecutionProvider"


def test_cpu_never_probes_cuda():
    assert resolve_execution_provider(
        "cpu",
        available_providers=[],
        cuda_runtime_usable=False,
    ) == "CPUExecutionProvider"


def test_cuda_fails_clearly_when_runtime_is_unavailable():
    with pytest.raises(RuntimeError, match="CUDAExecutionProvider is not usable"):
        resolve_execution_provider(
            "cuda",
            available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            cuda_runtime_usable=False,
        )
