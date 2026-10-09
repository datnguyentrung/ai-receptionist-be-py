"""Async sliding-window rate limiter for LLM requests.

Deep module conforming to codebase-design principles:
- Small interface: `acquire()`
- Deep implementation: manages sliding window history, locks, and automatic throttling.
"""

import asyncio
import os
import time
from collections import deque
from typing import Any


class AsyncRateLimiter:
    """Sliding-window in-memory rate limiter for coordinating LLM calls."""

    def __init__(self, rpm: int | None = None, interval_seconds: float = 60.0) -> None:
        configured_rpm = os.getenv("GEMINI_RPM_LIMIT")
        if rpm is not None:
            self._rpm = max(1, rpm)
        elif configured_rpm and configured_rpm.strip().isdigit():
            self._rpm = max(1, int(configured_rpm.strip()))
        else:
            # Default to 14 requests per minute to stay safely below 15 RPM Free Tier limit
            self._rpm = 14

        self._interval = interval_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    @property
    def rpm(self) -> int:
        return self._rpm

    @property
    def interval_seconds(self) -> float:
        return self._interval

    async def acquire(self) -> float:
        """Wait until a request slot is available in the current time window.

        Returns:
            float: Number of seconds waited (0.0 if slot was immediately available).
        """
        waited = 0.0
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._interval

            # Evict timestamps older than the sliding window interval
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) >= self._rpm:
                # Need to wait until the oldest timestamp exits the window
                oldest = self._timestamps[0]
                sleep_duration = max(0.0, (oldest + self._interval) - now)
                if sleep_duration > 0.0:
                    await asyncio.sleep(sleep_duration)
                    waited = sleep_duration
                    now = time.monotonic()

                # Re-clean timestamps after sleeping
                cutoff = now - self._interval
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()

            self._timestamps.append(time.monotonic())

        return waited

    def get_status(self) -> dict[str, Any]:
        """Observability helper returning current limiter state."""
        now = time.monotonic()
        cutoff = now - self._interval
        active_in_window = sum(1 for ts in self._timestamps if ts > cutoff)
        return {
            "rpm_limit": self._rpm,
            "interval_seconds": self._interval,
            "current_window_requests": active_in_window,
            "available_slots": max(0, self._rpm - active_in_window),
        }


# Global singleton instance shared across the application process
global_rate_limiter = AsyncRateLimiter()

__all__ = ["AsyncRateLimiter", "global_rate_limiter"]
