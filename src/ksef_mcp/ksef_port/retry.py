import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, TypeVar

from ksef_mcp.ksef_port.errors import KsefRateLimited

Result = TypeVar("Result")


@dataclass(frozen=True)
class RetryPolicy:
    """Waits only as long as KSeF asked, and only when KSeF asked.

    The SDK's own middleware caps its backoff at four seconds. Against a
    twenty-per-hour allowance `Retry-After` is measured in minutes, so that
    loop cannot outlast a real limit — it can only add attempts to a pattern
    the Ministry reads as working around one (D-017). Hence: the wait comes
    from `KSeFRateLimitError.retry_after` and from nowhere else.
    """

    attempts: int = 1
    max_wait: timedelta = timedelta(minutes=10)
    sleep: Callable[[float], None] = time.sleep

    def run(self, call: Callable[[], Result]) -> Result:
        # The final attempt is outside the loop, so a refusal on it reaches the
        # caller untouched — there is no wait left to justify swallowing it.
        for _ in range(1, self.attempts):
            try:
                return call()
            except KsefRateLimited as refusal:
                self._wait_out(refusal)
        return call()

    def _wait_out(self, refusal: KsefRateLimited) -> None:
        # No Retry-After means no ceiling we can quote, and a wait longer than
        # the policy allows is not ours to sit through. Inventing either is
        # precisely the behaviour that lengthens a block.
        if refusal.retry_after is None or refusal.retry_after > self.max_wait.total_seconds():
            raise refusal
        self.sleep(refusal.retry_after)


# The default everywhere: one attempt, then the decision goes to the person.
# An agent that waits out a minute-scale limit inside a tool call looks hung,
# and the call it finally makes is one the taxpayer never asked for.
NO_AUTOMATIC_RETRY: Final[RetryPolicy] = RetryPolicy(attempts=1)
