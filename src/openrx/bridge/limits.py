"""Per-source rate limits and the per-execution call budget (D-012).

Both live in memory. That is exact because the bridge runs as a single container
(reserved concurrency 1), so every request passes through this process.

- Rate limits protect the free public APIs: each source refills tokens at its catalog
  `per_second`. A request waits for a token instead of failing, since a short wait is
  better than handing the model an error to retry.
- The budget is a backup to Monty's own limit in the runner (max_suspensions): the
  51st call in one execution is refused even if the runner's limit failed.
"""

import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from openrx.catalog.loader import Catalog

CALL_BUDGET = 50  # docs/design.md §8.4
BUDGET_TTL_SECONDS = 3600.0  # forget executions not seen for an hour
MAX_TRACKED_EXECUTIONS = 10_000
_EXECUTION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class BudgetExceeded(Exception):  # noqa: N818 - reads as a state, like RequestRejected
    """The execution has used up its request() calls. `code` is for metrics."""

    def __init__(self, code: str, message: str) -> None:
        """Create the error with a short machine-readable code and a message."""
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class TokenBucket:
    """Allows `rate` requests per second, with bursts of up to one second's worth."""

    rate: float
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    tokens: float = field(init=False)
    capacity: float = field(init=False)
    _updated: float = field(init=False)

    def __post_init__(self) -> None:
        """Start full, so the first request of a quiet period goes out at once."""
        self.capacity = max(1.0, self.rate)
        self.tokens = self.capacity
        self._updated = self.clock()

    def acquire(self) -> float:
        """Take one token, waiting if none is free. Returns the seconds waited."""
        self._refill()
        waited = 0.0
        if self.tokens < 1:
            waited = (1 - self.tokens) / self.rate
            self.sleep(waited)
            self._refill()
            # The clock may tick slightly short of the wait; never go negative.
            self.tokens = max(self.tokens, 1.0)
        self.tokens -= 1
        return waited

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._updated) * self.rate)
        self._updated = now


class RateLimits:
    """One token bucket per catalog source."""

    def __init__(
        self,
        catalog: Catalog,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a bucket for every source at its catalog rate."""
        self._buckets = {
            s.id: TokenBucket(s.rate_limit.per_second, clock=clock, sleep=sleep) for s in catalog
        }

    def acquire(self, source_id: str) -> float:
        """Wait for a free slot to call `source_id`. Returns the seconds waited."""
        return self._buckets[source_id].acquire()


class CallBudget:
    """Counts request() calls per execution and refuses those over the limit."""

    def __init__(
        self,
        limit: int = CALL_BUDGET,
        ttl: float = BUDGET_TTL_SECONDS,
        max_tracked: int = MAX_TRACKED_EXECUTIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty budget book."""
        self.limit = limit
        self._ttl = ttl
        self._max_tracked = max_tracked
        self._clock = clock
        # execution id -> (calls so far, last seen); oldest-seen first.
        self._seen: OrderedDict[str, tuple[int, float]] = OrderedDict()

    def charge(self, execution_id: object) -> int:
        """Record one call for `execution_id` and return how many it has left.

        Raises:
            BudgetExceeded: if the id is missing or malformed, or the budget is used up.
        """
        if not isinstance(execution_id, str) or not _EXECUTION_ID.fullmatch(execution_id):
            raise BudgetExceeded(
                "bad_execution_id", "execution_id must be 1-128 letters, digits, '_' or '-'"
            )
        now = self._clock()
        self._forget_expired(now)
        if execution_id not in self._seen and len(self._seen) >= self._max_tracked:
            # Make room for a new id only. Never evict the id being charged: that
            # would reset its count and let it get past the budget.
            self._seen.popitem(last=False)
        calls, _ = self._seen.pop(execution_id, (0, now))
        calls += 1
        self._seen[execution_id] = (calls, now)  # re-insert as most recently seen
        if calls > self.limit:
            raise BudgetExceeded(
                "budget_exceeded",
                f"this execution has used all {self.limit} request() calls; "
                "combine requests (e.g. larger limit, several ids at once) or filter in code",
            )
        return self.limit - calls

    def _forget_expired(self, now: float) -> None:
        # Oldest-seen first, so stop at the first one still within the TTL.
        while self._seen:
            oldest, (_, last_seen) = next(iter(self._seen.items()))
            if now - last_seen <= self._ttl:
                break
            del self._seen[oldest]
