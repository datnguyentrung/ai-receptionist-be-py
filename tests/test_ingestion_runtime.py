import asyncio

from app.core import ingestion_runtime


def test_lazy_runtime_initializes_once_and_shutdown_is_idempotent(monkeypatch) -> None:
    calls = {"build": 0, "shutdown": 0}

    class FakeContainer:
        ready = True

        async def shutdown(self) -> None:
            calls["shutdown"] += 1
            self.ready = False

    async def fake_build():
        calls["build"] += 1
        await asyncio.sleep(0)
        return FakeContainer()

    monkeypatch.setattr(ingestion_runtime, "_container", None)
    monkeypatch.setattr(ingestion_runtime, "_container_lock", asyncio.Lock())
    monkeypatch.setattr(ingestion_runtime, "build_service_container", fake_build)
    monkeypatch.setattr(
        ingestion_runtime.settings,
        "INGESTION_SHUTDOWN_TIMEOUT_SECONDS",
        1,
    )

    async def scenario() -> None:
        left, right = await asyncio.gather(
            ingestion_runtime.get_service_container(),
            ingestion_runtime.get_service_container(),
        )
        assert left is right
        await ingestion_runtime.shutdown_service_container()
        await ingestion_runtime.shutdown_service_container()

    asyncio.run(scenario())
    assert calls == {"build": 1, "shutdown": 1}


def test_runtime_initialization_error_names_dependency() -> None:
    error = ingestion_runtime.RuntimeInitializationError("neo4j", ValueError("bad uri"))
    assert error.stage == "neo4j"
    assert "neo4j" in str(error)
    assert "ValueError" in str(error)
