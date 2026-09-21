from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Protocol

from ksef_mcp.clock import now_utc
from ksef_mcp.ksef_port.errors import BudgetExhausted
from ksef_mcp.ksef_port.types import Operation, OperationLimit, RateLimits

HOUR: Final[timedelta] = timedelta(hours=1)

MINUTE: Final[timedelta] = timedelta(minutes=1)

SECOND: Final[timedelta] = timedelta(seconds=1)


class BudgetJournal(Protocol):
    """Where the counter below survives the process that incremented it.

    A protocol rather than a class, because the file it writes belongs to the
    application layer and this module is inside the anticorruption boundary
    (ADR-102). `PeriodMetadataReader` and `QueryBudget` already relate this way
    round; inverting it here would make the port import its own callers.
    """

    def load(self) -> dict[Operation, tuple[datetime, ...]]:
        """Return what has already been spent, oldest moment first."""

    def record(self, spent: dict[Operation, tuple[datetime, ...]]) -> None:
        """Persist the whole counter, so a reader sees one consistent picture."""


@dataclass
class QueryBudget:
    """What is left of the allowance, counted rather than guessed.

    The Ministry logs breaches and reads repeated ones as an attempt to work
    around the limit, lengthening the block each time (D-020, D-031 §8). So the
    ceiling is read from KSeF — an allowance raised on request is invisible to
    any hard-coded default, and so is one lowered.

    With a `journal` the count spans the hour it is named after rather than one
    tool call. The MCP server under `uvx` is started and killed per agent
    session, so three tools inside a minute used to spend three full allowances
    and the counter never refused once (D-021 gave `PeriodCache` and `SyncStore`
    durability for exactly this reason; the counter had been left out).
    """

    limits: RateLimits
    clock: Callable[[], datetime] = now_utc
    spent: dict[Operation, deque[datetime]] = field(default_factory=dict)
    journal: BudgetJournal | None = None

    def __post_init__(self) -> None:
        if self.journal is None or self.spent:
            return
        self.spent = {
            operation: deque(moments) for operation, moments in self.journal.load().items()
        }

    def allowance(self, operation: Operation) -> OperationLimit:
        return self.limits.allowance(operation)

    def remaining(self, operation: Operation) -> int | None:
        """The tightest of the three windows, because KSeF enforces all three.

        The hourly ceiling used to be the only one consulted, so the per-second
        and per-minute allowances were read from KSeF, stored, and never able to
        refuse anything — a burst well inside the hour could still earn the
        breach the counter exists to prevent.
        """
        windows = tuple(self._headroom(operation))
        if not windows:
            return None
        return min(windows)

    def spend(self, operation: Operation) -> None:
        left = self.remaining(operation)
        if left == 0:
            raise BudgetExhausted(
                f"Refusing locally: this server's own counter for {operation} is "
                f"spent ({self._ceilings(operation)}). Nothing was sent to KSeF. "
                f"{self._when_it_frees(operation)}"
            )
        self._recent(operation).append(self.clock())
        self._persist()

    def frees_at(self, operation: Operation) -> datetime | None:
        """When the tightest spent window lets a call through again.

        Arithmetic on the local counter and never a guess: the moment a window
        frees is the moment the oldest call inside it leaves that window. A
        ceiling of zero frees at no moment at all, which is why this answers
        `None` rather than inventing a wait — inventing one is the behaviour the
        Ministry answers with a lengthening block (D-017).
        """
        releases = tuple(self._releases(operation))
        if not releases:
            return None
        return max(releases)

    def _headroom(self, operation: Operation) -> Iterable[int]:
        allowance = self.allowance(operation)
        recent = self._recent(operation)
        for ceiling, span in (
            (allowance.per_hour, HOUR),
            (allowance.per_minute, MINUTE),
            (allowance.per_second, SECOND),
        ):
            if ceiling is None:
                continue
            horizon = self.clock() - span
            yield max(ceiling - sum(1 for moment in recent if moment > horizon), 0)

    def _releases(self, operation: Operation) -> Iterable[datetime]:
        allowance = self.allowance(operation)
        moments = tuple(self._recent(operation))
        for ceiling, span in (
            (allowance.per_hour, HOUR),
            (allowance.per_minute, MINUTE),
            (allowance.per_second, SECOND),
        ):
            if ceiling is None or ceiling == 0:
                continue
            horizon = self.clock() - span
            inside = tuple(moment for moment in moments if moment > horizon)
            if len(inside) >= ceiling:
                yield inside[len(inside) - ceiling] + span

    def _when_it_frees(self, operation: Operation) -> str:
        frees_at = self.frees_at(operation)
        if frees_at is None:
            return "This allowance is zero, so no wait frees it."
        return f"The window frees up at {frees_at.isoformat()}."

    def _ceilings(self, operation: Operation) -> str:
        allowance = self.allowance(operation)
        return (
            f"{allowance.per_second} per second, {allowance.per_minute} per "
            f"minute, {allowance.per_hour} per hour"
        )

    def _recent(self, operation: Operation) -> deque[datetime]:
        # The hour is the widest window, so it is also what the deque is pruned
        # to; the narrower two are counted inside what it keeps.
        window = self.spent.setdefault(operation, deque())
        horizon = self.clock() - HOUR
        while window and window[0] <= horizon:
            window.popleft()
        return window

    def _persist(self) -> None:
        if self.journal is None:
            return
        self.journal.record({operation: tuple(window) for operation, window in self.spent.items()})
