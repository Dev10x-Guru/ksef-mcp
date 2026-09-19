"""What keeps the subject inside the allowance once the process has exited.

`QueryBudget` counts an hour; nothing made that hour outlive the tool call that
opened it. The MCP server under `uvx` is started and killed together with the
agent session, so an agent calling three tools in a minute spent three full
allowances and the counter never refused once — the breach the Ministry logs,
arrived at by a counter designed to prevent it (D-020, D-031 §8).

Two roots, and the split is the load-bearing part (D-032):

- data root — the spent counter. It is a guard against a block, and the cache
  convention reads "safe to delete at any moment"; a disk cleaner honouring it
  would disarm the protection silently.
- cache root — the limits read back from KSeF. Losing those costs one re-read,
  which is exactly what the cache convention promises.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.budget import HOUR, QueryBudget
from ksef_mcp.ksef_port.errors import KsefRequestRejected
from ksef_mcp.ksef_port.guard import GuardedSession
from ksef_mcp.ksef_port.protocol import KsefSession
from ksef_mcp.ksef_port.types import (
    KsefLimits,
    Operation,
    OperationLimit,
    RateLimits,
    SessionCeilings,
)
from ksef_mcp.paths import SubjectScope
from ksef_mcp.storage import json_written_atomically

LEDGER_FILE: Final[str] = "budget.json"

LIMITS_FILE: Final[str] = "limits.json"

REFUSALS_FILE: Final[str] = "refusals.json"

SCHEMA_VERSION: Final[int] = 1

DIRECTORY_MODE: Final[int] = 0o700

# Neither document names a counterparty, but both sit in a per-subject directory
# whose very path is the NIP (D-034), so they are created with the same mode as
# their neighbours rather than left to the umask.
FILE_MODE: Final[int] = 0o600

# How long a limits answer stays usable. MF changes an allowance in the scale of
# weeks and raises one on request, so an hour is short enough to notice either
# and long enough that opening ten sessions costs one pair of reads instead of
# twenty (D-031 §8).
LIMITS_FRESHNESS: Final[timedelta] = HOUR

# How many refusals in a row before this server stops asking on its own. Low
# enough that a run is caught while it is still short, high enough that a single
# unlucky call, or one expired token noticed and fixed, never trips it.
REFUSAL_LIMIT: Final[int] = 5

# How long the local refusal lasts when KSeF named no wait of its own. The same
# hour the allowance is counted in — not a number invented for the occasion, and
# not a guess at when the registry will relent (D-017).
REFUSAL_COOLDOWN: Final[timedelta] = HOUR


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _write_document(*, path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    # temp → rename (D-006), as everywhere else that state reaches disk: a
    # half-written counter read back as a miss would hand out an allowance that
    # has already been spent. The staging name is unique per writer and the
    # directory entry is persisted after the swap (ADR-107 §3, §4).
    json_written_atomically(
        path,
        document=document,
        file_mode=FILE_MODE,
    )


@dataclass(frozen=True)
class BudgetLedger:
    """One subject's spent moments, beside its synchronisation record.

    Per subject and per environment for the reason every other root here is: a
    shared directory is the main vector for mixing an accounting office's
    clients (D-034), and one client's spending must never refuse another's call
    — nor, worse, let it through.
    """

    nip: str
    environment: KsefEnvironment
    root: Path | None = None
    clock: Callable[[], datetime] = now_utc

    @property
    def path(self) -> Path:
        scope = SubjectScope.parsed(nip=self.nip, environment=self.environment)
        return scope.data_root(override=self.root) / LEDGER_FILE

    def load(self) -> dict[Operation, tuple[datetime, ...]]:
        """What is still inside the hour. Anything older is not worth carrying.

        A damaged document reads as an empty counter and never as an error. The
        alternative is an MCP server that refuses to start over a truncated
        file, and the cost of the shrug is bounded: one hour of undercounting,
        against an outage that lasts until somebody deletes the file by hand.
        """
        if not self.path.is_file():
            return {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document["schema_version"] != SCHEMA_VERSION:
                return {}
            return self._decode(document["spent"])
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def record(self, spent: dict[Operation, tuple[datetime, ...]]) -> None:
        _write_document(
            path=self.path,
            document={
                "schema_version": SCHEMA_VERSION,
                "nip": self.nip,
                "environment": str(self.environment),
                "spent": {
                    str(operation): [moment.isoformat() for moment in moments]
                    for operation, moments in spent.items()
                },
            },
        )

    def _decode(self, stored: dict[str, list[str]]) -> dict[Operation, tuple[datetime, ...]]:
        horizon = self.clock() - HOUR
        counted: dict[Operation, tuple[datetime, ...]] = {}
        for operation, moments in stored.items():
            recent = tuple(
                moment
                for moment in (datetime.fromisoformat(spelling) for spelling in moments)
                if moment > horizon
            )
            if recent:
                counted[Operation(operation)] = recent
        return counted


def _encode_operation_limit(limit: OperationLimit) -> dict[str, object]:
    return {
        "per_second": limit.per_second,
        "per_minute": limit.per_minute,
        "per_hour": limit.per_hour,
    }


def _decode_operation_limit(stored: dict[str, object]) -> OperationLimit:
    return OperationLimit(
        per_second=None if stored["per_second"] is None else int(stored["per_second"]),  # type: ignore[arg-type]
        per_minute=None if stored["per_minute"] is None else int(stored["per_minute"]),  # type: ignore[arg-type]
        per_hour=None if stored["per_hour"] is None else int(stored["per_hour"]),  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class LimitsCache:
    """The allowances KSeF last reported, kept for an hour.

    Opening a session costs two requests before the errand starts, and no
    `Operation` counts them — so the reads meant to protect the allowance were
    themselves outside it (GH-98). They change in the scale of weeks, which is
    what makes remembering them safe.

    A degraded answer is never remembered. `degraded` means the SDK could not
    parse what production sent and the conservative fallback was substituted
    (GH-76); caching that would turn one unlucky response into an hour of
    counting against numbers KSeF never gave.
    """

    nip: str
    environment: KsefEnvironment
    root: Path | None = None
    clock: Callable[[], datetime] = now_utc
    freshness: timedelta = LIMITS_FRESHNESS

    @property
    def path(self) -> Path:
        scope = SubjectScope.parsed(nip=self.nip, environment=self.environment)
        return scope.cache_root(override=self.root) / LIMITS_FILE

    def remembered(self) -> KsefLimits | None:
        if not self.path.is_file():
            return None
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document["schema_version"] != SCHEMA_VERSION:
                return None
            if datetime.fromisoformat(document["read_at"]) <= self.clock() - self.freshness:
                return None
            return self._decode(document["limits"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def remember(self, limits: KsefLimits) -> None:
        if limits.degraded:
            return
        rates = limits.rates
        ceilings = limits.ceilings
        _write_document(
            path=self.path,
            document={
                "schema_version": SCHEMA_VERSION,
                "nip": self.nip,
                "environment": str(self.environment),
                "read_at": self.clock().isoformat(),
                "limits": {
                    "rates": {
                        "metadata_queries": _encode_operation_limit(rates.metadata_queries),
                        "exports": _encode_operation_limit(rates.exports),
                        "export_statuses": _encode_operation_limit(rates.export_statuses),
                        "invoice_downloads": _encode_operation_limit(rates.invoice_downloads),
                    },
                    "ceilings": {
                        "max_invoice_megabytes": ceilings.max_invoice_megabytes,
                        "max_invoice_with_attachment_megabytes": (
                            ceilings.max_invoice_with_attachment_megabytes
                        ),
                        "max_invoices_per_session": ceilings.max_invoices_per_session,
                    },
                },
            },
        )

    def _decode(self, stored: dict[str, object]) -> KsefLimits:
        rates: dict[str, dict[str, object]] = stored["rates"]  # type: ignore[assignment]
        ceilings: dict[str, object] = stored["ceilings"]  # type: ignore[assignment]
        return KsefLimits(
            rates=RateLimits(
                metadata_queries=_decode_operation_limit(rates["metadata_queries"]),
                exports=_decode_operation_limit(rates["exports"]),
                export_statuses=_decode_operation_limit(rates["export_statuses"]),
                invoice_downloads=_decode_operation_limit(rates["invoice_downloads"]),
            ),
            ceilings=SessionCeilings(
                max_invoice_megabytes=int(ceilings["max_invoice_megabytes"]),  # type: ignore[arg-type]
                max_invoice_with_attachment_megabytes=int(
                    ceilings["max_invoice_with_attachment_megabytes"]  # type: ignore[arg-type]
                ),
                max_invoices_per_session=int(ceilings["max_invoices_per_session"]),  # type: ignore[arg-type]
            ),
            degraded=False,
        )


@dataclass(frozen=True)
class RefusalRun:
    """How many times in a row KSeF has said no, and until when to stop asking."""

    consecutive: int = 0
    blocked_until: datetime | None = None


@dataclass(frozen=True)
class RefusalLedger:
    """The local fuse, beside the spent counter and for the same reason.

    `QueryBudget` protects against being answered too often. Nothing protected
    against being refused too often — and a run of refusals is the pattern MF
    analyses as working around a limit, answering it with a block that grows
    longer each time it recurs (D-020, D-031 §8). The harm there comes from the
    client's persistence, not from any one call, so the fuse has to outlive the
    process: a counter reset by `uvx` would let every restart resume the run.

    Data root, never cache, for the same reason the spent counter is there: this
    is a guard, and a guard a disk cleaner may delete is not one.
    """

    nip: str
    environment: KsefEnvironment
    root: Path | None = None
    clock: Callable[[], datetime] = now_utc
    limit: int = REFUSAL_LIMIT
    cooldown: timedelta = REFUSAL_COOLDOWN

    @property
    def path(self) -> Path:
        scope = SubjectScope.parsed(nip=self.nip, environment=self.environment)
        return scope.data_root(override=self.root) / REFUSALS_FILE

    def load(self) -> RefusalRun:
        if not self.path.is_file():
            return RefusalRun()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document["schema_version"] != SCHEMA_VERSION:
                return RefusalRun()
            blocked = document["blocked_until"]
            return RefusalRun(
                consecutive=int(document["consecutive"]),
                blocked_until=None if blocked is None else datetime.fromisoformat(str(blocked)),
            )
        except (OSError, ValueError, KeyError, TypeError):
            # A damaged fuse reads as an intact one. Refusing to start would
            # turn a truncated file into an outage; the cost of the shrug is
            # one run of refusals counted from zero.
            return RefusalRun()

    def refuse_early(self) -> None:
        blocked_until = self.load().blocked_until
        if blocked_until is None or blocked_until <= self.clock():
            return
        raise KsefRequestRejected(
            f"Refusing locally: KSeF has said no {self.limit} times in a row, "
            f"and asking again is the pattern that lengthens a block. Nothing "
            f"will be sent before {blocked_until.isoformat()}."
        )

    def note_refusal(self, *, retry_after: int | None) -> None:
        consecutive = self.load().consecutive + 1
        self._save(
            RefusalRun(
                consecutive=consecutive,
                blocked_until=self._blocked_until(retry_after)
                if consecutive >= self.limit
                else None,
            )
        )

    def note_success(self) -> None:
        """Clear the run — and only when there is one, so a healthy pass writes nothing."""
        if self.load() == RefusalRun():
            return
        self._save(RefusalRun())

    def _blocked_until(self, retry_after: int | None) -> datetime:
        """The later of what KSeF asked and this server's own floor.

        Honouring `Retry-After` is a requirement, not a detail (D-017), so a
        longer wait named by the registry is always obeyed. But the fuse is a
        stop, not a retry: after a run this long, coming back in the thirty
        seconds a 429 sometimes names is resuming the very pattern being
        guarded against, so the floor stands underneath it.
        """
        moment = self.clock()
        floor = moment + self.cooldown
        if retry_after is None:
            return floor
        return max(floor, moment + timedelta(seconds=retry_after))

    def _save(self, run: RefusalRun) -> None:
        _write_document(
            path=self.path,
            document={
                "schema_version": SCHEMA_VERSION,
                "nip": self.nip,
                "environment": str(self.environment),
                "consecutive": run.consecutive,
                "blocked_until": (
                    None if run.blocked_until is None else run.blocked_until.isoformat()
                ),
            },
        )


@dataclass(frozen=True)
class Allowance:
    """One subject's protection against a block, as a single collaborator.

    Two things have to agree about whose allowance is being counted: the limits
    KSeF granted this context, and the moments already spent against them.
    Handing a service two collaborators that must share a NIP is two chances to
    count one client's calls against another's (D-034), so both are derived
    from one object and the NIP is stated once.

    Both roots are named separately because they mean opposite things (D-032):
    losing the remembered limits costs one re-read, losing the spent counter
    costs the protection itself.
    """

    nip: str
    environment: KsefEnvironment
    data_root: Path | None = None
    cache_root: Path | None = None
    clock: Callable[[], datetime] = now_utc

    @property
    def ledger(self) -> BudgetLedger:
        return BudgetLedger(
            nip=self.nip,
            environment=self.environment,
            root=self.data_root,
            clock=self.clock,
        )

    @property
    def limits(self) -> LimitsCache:
        return LimitsCache(
            nip=self.nip,
            environment=self.environment,
            root=self.cache_root,
            clock=self.clock,
        )

    @property
    def breaker(self) -> RefusalLedger:
        return RefusalLedger(
            nip=self.nip,
            environment=self.environment,
            root=self.data_root,
            clock=self.clock,
        )

    def guarded(self, *, session: KsefSession) -> GuardedSession:
        """The session a tool should actually talk to.

        Wrapping at the composition root rather than inside the adapter keeps
        the port ignorant of where the fuse is kept, and keeps the wrapping
        visible at the four places that open a session.
        """
        return GuardedSession(inner=session, breaker=self.breaker)

    def read_limits(self, *, session: KsefSession) -> KsefLimits:
        cache = self.limits
        remembered = cache.remembered()
        if remembered is not None:
            return remembered
        limits = session.read_limits()
        cache.remember(limits)
        return limits

    def budget(self, *, session: KsefSession) -> QueryBudget:
        """The one way a tool obtains a counter, so no tool obtains one that forgets.

        Four services built `QueryBudget` themselves, each from a fresh
        `read_limits()` and each starting from zero. Both halves of that are
        fixed here rather than at the four call sites, because a fifth caller is
        exactly how the first four drifted apart.
        """
        return QueryBudget(
            limits=self.read_limits(session=session).rates,
            clock=self.clock,
            journal=self.ledger,
        )
