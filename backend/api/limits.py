"""Per-client rate limits: a sliding one-minute window in memory, and a daily cap counted from
stored messages (so it survives restarts). The day is the Asia/Kolkata calendar day."""

import math
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DAY_ZONE = ZoneInfo("Asia/Kolkata")  # users are mainly in India; the free LLM quota resets around then too
WINDOW_S = 60.0


class MinuteLimiter:
    """At most `limit` hits per client in any 60 s; returns how long to wait when over."""

    def __init__(self, limit: int, clock=time.monotonic) -> None:
        self.limit, self.clock = limit, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> int:
        """0 if allowed (and recorded), else seconds until the next hit is allowed."""
        now = self.clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= WINDOW_S:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(1, math.ceil(WINDOW_S - (now - hits[0])))
        hits.append(now)
        return 0


def day_start(now: datetime | None = None) -> datetime:
    """Local midnight (Asia/Kolkata) at the start of today."""
    now = (now or datetime.now(DAY_ZONE)).astimezone(DAY_ZONE)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_to_next_day(now: datetime | None = None) -> int:
    now = (now or datetime.now(DAY_ZONE)).astimezone(DAY_ZONE)
    return max(1, math.ceil((day_start(now) + timedelta(days=1) - now).total_seconds()))
