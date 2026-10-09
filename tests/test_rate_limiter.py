"""Unit tests for AsyncRateLimiter deep module."""

import asyncio
import time

from app.core.rate_limiter import AsyncRateLimiter


def test_rate_limiter_immediate_acquire() -> None:
    limiter = AsyncRateLimiter(rpm=10, interval_seconds=60.0)
    waited = asyncio.run(limiter.acquire())
    assert waited == 0.0

    status = limiter.get_status()
    assert status["current_window_requests"] == 1
    assert status["available_slots"] == 9


def test_rate_limiter_throttles_when_limit_reached() -> None:
    # 2 requests per 0.15 seconds
    interval = 0.15
    limiter = AsyncRateLimiter(rpm=2, interval_seconds=interval)

    async def run_calls() -> list[float]:
        w1 = await limiter.acquire()
        w2 = await limiter.acquire()
        start = time.monotonic()
        w3 = await limiter.acquire()
        elapsed = time.monotonic() - start
        return [w1, w2, w3, elapsed]

    w1, w2, w3, elapsed = asyncio.run(run_calls())
    assert w1 == 0.0
    assert w2 == 0.0
    # The 3rd request must have waited for the 1st request to roll off
    assert w3 > 0.0
    assert elapsed >= interval * 0.8


def test_rate_limiter_status_metrics() -> None:
    limiter = AsyncRateLimiter(rpm=5, interval_seconds=60.0)
    status_initial = limiter.get_status()
    assert status_initial["rpm_limit"] == 5
    assert status_initial["current_window_requests"] == 0
    assert status_initial["available_slots"] == 5

    asyncio.run(limiter.acquire())
    status_after = limiter.get_status()
    assert status_after["current_window_requests"] == 1
    assert status_after["available_slots"] == 4


def test_rate_limiter_respects_env_var(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RPM_LIMIT", "25")
    limiter = AsyncRateLimiter()
    assert limiter.rpm == 25
