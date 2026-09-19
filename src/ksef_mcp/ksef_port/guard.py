from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol, TypeVar

from ksef_mcp.ksef_port.errors import (
    KsefAuthenticationFailed,
    KsefRateLimited,
    KsefRefused,
)
from ksef_mcp.ksef_port.protocol import KsefSession
from ksef_mcp.ksef_port.retry import NO_AUTOMATIC_RETRY, RetryPolicy
from ksef_mcp.ksef_port.types import (
    ExportHandle,
    ExportPart,
    ExportStatus,
    InvoiceDirection,
    KsefLimits,
    KsefNumber,
    MetadataPage,
    Period,
)

Result = TypeVar("Result")

# What counts as KSeF saying no. All three are answers from the registry: a
# spent limit, a rejected token, a refused call. `KsefUnreachable` is not here,
# because no answer existed — a flaky network is not a pattern the Ministry
# reads as working around an allowance, and treating it as one would lock a
# subject out over its own DNS.
KSEF_REFUSALS: Final[tuple[type[Exception], ...]] = (
    KsefRateLimited,
    KsefAuthenticationFailed,
    KsefRefused,
)


class RefusalBreaker(Protocol):
    """Counts refusals the way `QueryBudget` counts successes.

    The budget guards against asking too often and being answered; nothing
    guarded against asking too often and being refused — and a run of refusals
    is precisely the pattern MF analyses as an attempt to work around a limit,
    lengthening the block each time it recurs (D-020, D-031 §8).
    """

    def refuse_early(self) -> None:
        """Raise when the run of refusals says to stop asking, naming the moment."""

    def note_refusal(self, *, retry_after: int | None) -> None:
        """Record that KSeF said no, and the wait it asked for if it named one."""

    def note_success(self) -> None:
        """Record that the run is over."""


@dataclass(frozen=True)
class GuardedSession:
    """A session that will not join a pattern the Ministry punishes.

    Every call KSeF answers passes through here, which is what finally connects
    `RetryPolicy` (GH-100). It had tests and no production caller: the real
    behaviour came from `SINGLE_ATTEMPT` in the adapter, so the class that knows
    how to honour a server's `Retry-After` never ran. The default stays
    `NO_AUTOMATIC_RETRY`, so nothing about the traffic changes — one attempt,
    then the decision goes to the person (D-017). What changes is that a caller
    who wants KSeF's own wait honoured now has somewhere to say so.

    `fetch_part` is delegated untouched and deliberately. A package part comes
    from presigned external storage, carries no KSeF credential, and counts
    against no allowance; an expired link there says nothing about how often
    this subject is asking KSeF for anything.
    """

    inner: KsefSession
    breaker: RefusalBreaker
    retry: RetryPolicy = NO_AUTOMATIC_RETRY

    def read_limits(self) -> KsefLimits:
        return self._guarded(self.inner.read_limits)

    def query_metadata(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
    ) -> MetadataPage:
        return self._guarded(lambda: self.inner.query_metadata(period=period, direction=direction))

    def start_export(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
    ) -> ExportHandle:
        return self._guarded(lambda: self.inner.start_export(period=period, direction=direction))

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        return self._guarded(lambda: self.inner.check_export(handle=handle))

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        return self.inner.fetch_part(handle=handle, part=part)

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        return self._guarded(lambda: self.inner.download_invoice(ksef_number=ksef_number))

    def _guarded(self, call: Callable[[], Result]) -> Result:
        self.breaker.refuse_early()
        try:
            answer = self.retry.run(call)
        except KSEF_REFUSALS as refusal:
            # Only a 429 carries a wait, and only KSeF's own number is ever
            # passed on. Reading one off any other refusal would mean inventing
            # it, which is the behaviour that lengthens a block (D-017).
            asked = refusal.retry_after if isinstance(refusal, KsefRateLimited) else None
            self.breaker.note_refusal(retry_after=asked)
            raise
        self.breaker.note_success()
        return answer
