from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from ksef_mcp.ksef_port.errors import KsefRequestRejected
from ksef_mcp.ksef_port.types import OperationLimit, RateLimits

HOUR: Final[timedelta] = timedelta(hours=1)


class Operation(StrEnum):
    METADATA_QUERY = "metadata_query"
    EXPORT = "export"
    EXPORT_STATUS = "export_status"
    INVOICE_DOWNLOAD = "invoice_download"


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class QueryBudget:
    """What is left of the hourly allowance, counted rather than guessed.

    The Ministry logs breaches and reads repeated ones as an attempt to work
    around the limit, lengthening the block each time (D-020, D-031 §8). So the
    ceiling is read from KSeF — an allowance raised on request is invisible to
    any hard-coded default, and so is one lowered.
    """

    limits: RateLimits
    clock: Callable[[], datetime] = now_utc
    spent: dict[Operation, deque[datetime]] = field(default_factory=dict)

    def allowance(self, operation: Operation) -> OperationLimit:
        return {
            Operation.METADATA_QUERY: self.limits.metadata_queries,
            Operation.EXPORT: self.limits.exports,
            Operation.EXPORT_STATUS: self.limits.export_statuses,
            Operation.INVOICE_DOWNLOAD: self.limits.invoice_downloads,
        }[operation]

    def remaining(self, operation: Operation) -> int | None:
        per_hour = self.allowance(operation).per_hour
        if per_hour is None:
            return None
        return max(per_hour - len(self._recent(operation)), 0)

    def spend(self, operation: Operation) -> None:
        left = self.remaining(operation)
        if left == 0:
            raise KsefRequestRejected(
                f"The hourly allowance for {operation} is spent "
                f"({self.allowance(operation).per_hour} per hour). Waiting is "
                f"cheaper than a call KSeF will refuse."
            )
        self._recent(operation).append(self.clock())

    def _recent(self, operation: Operation) -> deque[datetime]:
        window = self.spent.setdefault(operation, deque())
        horizon = self.clock() - HOUR
        while window and window[0] <= horizon:
            window.popleft()
        return window
