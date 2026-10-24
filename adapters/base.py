"""Resilient data access: primary agent -> direct API -> last cached result.

Each fetcher is tried in order with a timeout; a per-fetcher circuit breaker
skips fetchers that keep failing. If every fetcher fails, the last cached value
is returned marked with its age. If there is none, `ok` is False.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

log = logging.getLogger("supplypulse.adapter")

DEFAULT_TIMEOUT_S = 8.0
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 300.0


def friendly_reason(errors) -> str:
    """Plain-language reason for a failed fetch. Raw exception text and URLs stay in the logs only."""
    text = " | ".join(errors).lower()
    if "429" in text or "too many requests" in text or "rate-limit" in text or "rate limit" in text:
        return "the service is limiting requests right now"
    if "still fetching" in text:
        return "still loading, it should be ready on the next scan"
    if "timeout" in text:
        return "the service didn't answer in time"
    if "not set" in text or "not configured" in text:
        return "not set up (missing API key)"
    if "circuit open" in text:
        return "temporarily skipped after repeated failures"
    return "temporarily unavailable"


@dataclass
class Result:
    ok: bool
    value: Any = None
    source: str = ""
    age_s: float = 0.0
    stale: bool = False
    errors: tuple[str, ...] = ()


class CircuitBreaker:
    def __init__(self, threshold=BREAKER_THRESHOLD, cooldown=BREAKER_COOLDOWN_S, clock=time.monotonic):
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.cooldown:
            self.opened_at, self.failures = None, self.threshold - 1  # half-open: one probe
            return True
        return False

    def success(self):
        self.failures, self.opened_at = 0, None

    def failure(self):
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


Fetcher = tuple[str, Callable[[], Awaitable[Any]]]


class Adapter:
    """One adapter per data source. Instantiate once and reuse (holds cache + breakers)."""

    def __init__(self, name: str, ttl_s: float = 600.0, timeout_s: float = DEFAULT_TIMEOUT_S,
                 clock=time.monotonic):
        self.name, self.ttl_s, self.timeout_s, self.clock = name, ttl_s, timeout_s, clock
        self._cache: dict[str, tuple[float, Any, str]] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.disabled: set[str] = set()  # failure injection: fetcher labels forced to fail

    def _breaker(self, label: str) -> CircuitBreaker:
        return self._breakers.setdefault(label, CircuitBreaker(clock=self.clock))

    def peek(self, key: str) -> Result | None:
        """Cached value of any age, without fetching (used to keep slow sources off the hot path)."""
        hit = self._cache.get(key)
        if not hit:
            return None
        age = self.clock() - hit[0]
        return Result(True, hit[1], hit[2] + " (cached)", age, stale=age >= self.ttl_s)

    async def get(self, key: str, fetchers: list[Fetcher], force: bool = False) -> Result:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # concurrent callers share one fetch
            hit = self._cache.get(key)
            if hit and not force and self.clock() - hit[0] < self.ttl_s:
                return Result(True, hit[1], hit[2] + " (cached)", self.clock() - hit[0])
            errors: list[str] = []
            for label, fn in fetchers:
                br = self._breaker(label)
                if label in self.disabled:
                    errors.append(f"{label}: disabled")
                    continue
                if not br.allow():
                    errors.append(f"{label}: circuit open")
                    continue
                try:
                    value = await asyncio.wait_for(fn(), self.timeout_s)
                except Exception as e:  # noqa: BLE001 - any failure falls through to next source
                    br.failure()
                    errors.append(f"{label}: {type(e).__name__} {(str(e).splitlines() or [''])[0][:160]}".strip())  # one line, no URLs dump
                    log.warning("%s/%s failed: %s", self.name, label, errors[-1])
                    continue
                br.success()
                self._cache[key] = (self.clock(), value, label)
                return Result(True, value, label, 0.0, errors=tuple(errors))
            if hit:
                age = self.clock() - hit[0]
                return Result(True, hit[1], hit[2], age, stale=True, errors=tuple(errors))
            return Result(False, errors=tuple(errors))
