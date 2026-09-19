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
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from platformdirs import user_cache_path, user_data_path

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port.budget import HOUR, Operation, QueryBudget
from ksef_mcp.ksef_port.protocol import KsefSession
from ksef_mcp.ksef_port.types import KsefLimits, OperationLimit, RateLimits, SessionCeilings
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.sync_store import SUBJECT_DIRECTORY

LEDGER_FILE: Final[str] = "budget.json"

LIMITS_FILE: Final[str] = "limits.json"

STAGING_SUFFIX: Final[str] = ".tmp"

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


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _write_document(*, path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    # temp → rename (D-006), as everywhere else that state reaches disk: a
    # half-written counter read back as a miss would hand out an allowance that
    # has already been spent.
    staging = path.with_suffix(STAGING_SUFFIX)
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    staging.chmod(FILE_MODE)
    os.replace(staging, path)


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
        base = user_data_path(appname=SERVER_NAME) if self.root is None else self.root
        return base / SUBJECT_DIRECTORY / self.nip / str(self.environment) / LEDGER_FILE

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
        base = user_cache_path(appname=SERVER_NAME) if self.root is None else self.root
        return base / SUBJECT_DIRECTORY / self.nip / str(self.environment) / LIMITS_FILE

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
