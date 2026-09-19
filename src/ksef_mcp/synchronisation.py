"""Incremental synchronisation: package exports, high water mark, one subject type at a time.

The canonical pattern the Ministry publishes (D-031), not an invention of ours.
Everything the caller might be tempted to tune — the window, the page size, how
many packages to ask for — is decided here or by KSeF, never passed in from a
tool (D-020).

The pass runs the whole way: a package KSeF reports as ready is fetched,
decrypted and written to the subject's archive inside the same call, and the
export key stops existing as part of that (ADR-104 §2, ADR-105). What survives
a failure is deliberate — the pending record with its key and its parts stays on
disk, and the continuation point stays where KSeF's own marker put it, because
the point tracks what the registry confirmed and not what this process managed
to write (ADR-103 §3).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from ksef_mcp.allowance import Allowance
from ksef_mcp.archive import (
    ArchiveIndexUnreadable,
    ArchiveMetadataUnusable,
    InvoiceArchive,
    PackageArchivist,
)
from ksef_mcp.ksef_port.budget import Operation, QueryBudget
from ksef_mcp.ksef_port.errors import (
    KsefPortError,
    KsefRefused,
    KsefRequestRejected,
    PackageLinkExpired,
)
from ksef_mcp.ksef_port.protocol import KsefPort, KsefSession
from ksef_mcp.ksef_port.types import (
    MAX_QUERY_WINDOW,
    ContinuationPoint,
    ExportState,
    ExportStatus,
    InvoiceDirection,
    Period,
)
from ksef_mcp.package import PackageRetriever, PackageUnreadable
from ksef_mcp.sync_store import (
    DirectionState,
    ExportKeyDiscarded,
    PendingExport,
    SyncState,
    SyncStore,
)

# Every subject type, every run: a company appears in different roles on
# different invoices, and only the loop lets the period be called complete
# (D-031 §5).
SYNCHRONISED_DIRECTIONS: Final[tuple[InvoiceDirection, ...]] = (
    InvoiceDirection.SELLER,
    InvoiceDirection.BUYER,
    InvoiceDirection.THIRD_SUBJECT,
    InvoiceDirection.AUTHORIZED_SUBJECT,
)

# Subject 3 and the authorized subject appear rarely; the Ministry's guidance is
# once a day in a night window. Keeping them off the daytime rotation is what
# leaves the frequent two their share of twenty exports an hour (D-031 §5).
OCCASIONAL_DIRECTIONS: Final[frozenset[InvoiceDirection]] = frozenset(
    {InvoiceDirection.THIRD_SUBJECT, InvoiceDirection.AUTHORIZED_SUBJECT}
)

# UTC rather than the machine's local time: a window that moves with the
# operator's timezone and with daylight saving is not a rule anyone can reason
# about afterwards from the record on disk.
NIGHT_WINDOW_OPENS_UTC: Final[int] = 0

NIGHT_WINDOW_CLOSES_UTC: Final[int] = 6

# The floor D-031 §5 sets per subject type. With at most one export per type per
# run, it is also the whole allocation policy: four types times four exports an
# hour is sixteen, under the twenty the context allows, and the budget counter
# is the backstop for an allowance KSeF reports lower than that.
MINIMUM_INTERVAL: Final[timedelta] = timedelta(minutes=15)

OCCASIONAL_INTERVAL: Final[timedelta] = timedelta(days=1)

# How far back a first run reaches. This only says where the sequence starts; a
# subject with older invoices catches up over the following runs rather than in
# one oversized package.
#
# The ceiling itself, not a number that happens to sit under it (GH-84). The two
# were written independently and then drifted apart from the same value, so
# every first run asked for a window KSeF refuses outright — the drift was
# invisible precisely because the two constants looked equal. Reaching as far
# back as one window allows is also the most a first run can usefully do.
INITIAL_LOOKBACK: Final[timedelta] = MAX_QUERY_WINDOW

# An export is queued, so waiting for it inside one call is the wrong shape: a
# tool that blocks for minutes looks hung to the agent and to the person. Poll a
# few times for the packages that finish quickly, then leave the rest recorded
# on disk for the next run to pick up.
POLL_ATTEMPTS: Final[int] = 3

POLL_INTERVAL: Final[timedelta] = timedelta(seconds=10)

# Everything that can stop a ready package short of the archive: the port
# refusing or failing to hand over a part, bytes that are not the package KSeF
# described, a manifest that names no KSeF number, an unreadable index, and a
# record whose key is already gone. Each of them leaves the pending record where
# it is, so the list is the definition of "retry next pass", not of "give up".
#
# `PackageLinkExpired` is deliberately absent even though it is a
# `KsefPortError`: waiting is exactly what does not help it (GH-93). It is
# caught ahead of this list and answered by asking KSeF for the export again.
ARCHIVING_FAILURES: Final[tuple[type[Exception], ...]] = (
    KsefPortError,
    PackageUnreadable,
    ArchiveMetadataUnusable,
    ArchiveIndexUnreadable,
    ExportKeyDiscarded,
)


class SyncOutcome(StrEnum):
    ARCHIVED = "archived"
    NOT_ARCHIVED = "not_archived"
    STILL_RUNNING = "still_running"
    FAILED = "failed"
    NOT_DUE = "not_due"
    BUDGET_SPENT = "budget_spent"
    INCONCLUSIVE = "inconclusive"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class DirectionReport:
    """One subject type's result. Paths and KSeF numbers, never invoice bodies (D-011)."""

    direction: InvoiceDirection
    outcome: SyncOutcome
    detail: str
    invoice_count: int = 0
    part_count: int = 0
    reached: datetime | None = None
    archived: tuple[str, ...] = ()
    already_held: tuple[str, ...] = ()
    archive_directory: str | None = None


@dataclass(frozen=True)
class SynchronisationReport:
    directions: tuple[DirectionReport, ...]
    pending_exports: tuple[str, ...]
    state_path: str


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def in_night_window(moment: datetime) -> bool:
    return NIGHT_WINDOW_OPENS_UTC <= moment.astimezone(UTC).hour < NIGHT_WINDOW_CLOSES_UTC


def is_due(
    *,
    direction: InvoiceDirection,
    stored: DirectionState | None,
    moment: datetime,
) -> bool:
    occasional = direction in OCCASIONAL_DIRECTIONS
    if occasional and not in_night_window(moment):
        return False
    if stored is None or stored.attempted_at is None:
        return True
    interval = OCCASIONAL_INTERVAL if occasional else MINIMUM_INTERVAL
    return moment - stored.attempted_at >= interval


def advance(point: ContinuationPoint, *, status: ExportStatus) -> ContinuationPoint | None:
    """Move the point per the D-031 §4 table, or refuse when KSeF left it empty.

    The table itself belongs to the port and is called, not copied. What is
    decided here is the one case the port cannot decide: a package whose
    continuation timestamp is missing advances nowhere, because a guessed start
    skips invoices no later run ever asks for again.
    """
    hwm = status.hwm_date
    last_seen = status.last_permanent_storage_date
    if (last_seen if status.truncated else hwm) is None:
        return None
    # Only the field the table will read is ever the real one; the other is
    # inert, and repeating the choice here would put the table in two places.
    return point.advanced_to(
        hwm=hwm or point.reached,
        truncated=status.truncated,
        last_seen=last_seen or point.reached,
    )


def rolled_back_to(export: PendingExport, *, stored: DirectionState | None) -> datetime:
    """Where a subject type's point goes once an export is confirmed unreachable.

    A record that kept the window start it asked for goes back to exactly that.
    One written before that field existed goes back a whole window from when the
    export was queued — the direction to err in, because a range already held
    costs one export and deduplication by KSeF number throws the duplicates away
    (D-005), while too short a reach loses invoices nothing asks for again.

    Never forward. A subject type dormant for longer than the ceiling stands
    further back than one window reaches, and letting the guess pull it up to
    meet would skip precisely the invoices this rollback exists to recover.
    """
    # `is not None`, not truthiness: a datetime is always truthy today, so the
    # shorter spelling works by a property of the type rather than by what is
    # meant here — and it would start choosing wrongly, silently, the day the
    # field holds anything that defines its own `__bool__`.
    remembered = (
        export.started_at - MAX_QUERY_WINDOW
        if export.covering_from is None
        else export.covering_from
    )
    return remembered if stored is None else min(remembered, stored.reached)


@dataclass(frozen=True)
class Synchroniser:
    """One pass over every subject type, from "ask KSeF" to "the file is on disk".

    Re-entrant by construction. A package that is not ready when the pass ends
    stays on disk with its key and its parts, so the next pass continues it
    instead of spending another export on the same window — and so does a
    package that was fetched but could not be archived.
    """

    port: KsefPort
    store: SyncStore
    allowance: Allowance
    clock: Callable[[], datetime] = now_utc
    sleep: Callable[[float], None] = time.sleep
    poll_attempts: int = POLL_ATTEMPTS
    poll_interval: timedelta = POLL_INTERVAL

    @property
    def archive(self) -> InvoiceArchive:
        # Derived from the store rather than taken as a second argument: the
        # invoices and the synchronisation record share one root per subject and
        # per environment (ADR-105 §1), and two arguments that must agree are
        # two chances to file one client's invoices under another's NIP (D-034).
        return InvoiceArchive(
            nip=self.store.nip,
            environment=self.store.environment,
            root=self.store.root,
            clock=self.clock,
        )

    def run(self, *, nip: str, token: str) -> SynchronisationReport:
        state = self.store.load()
        reports: list[DirectionReport] = []
        with self.port.session(nip=nip, token=token) as session:
            budget = self.allowance.budget(session=session)
            retriever = PackageRetriever(session=session, store=self.store)
            for direction in SYNCHRONISED_DIRECTIONS:
                state, report = self._advance_one(
                    session=session,
                    budget=budget,
                    retriever=retriever,
                    state=state,
                    direction=direction,
                )
                # Written per subject type, not once when the loop is over. A
                # failure in a later type used to discard everything the earlier
                # ones had established: the `attempted_at` holding the
                # fifteen-minute floor open, and — far worse — the AES key of a
                # package KSeF had already accepted, which nothing can ever
                # decrypt without it (D-033, GH-96). The state is immutable and
                # goes out by temp→rename, so four writes a pass cost nothing
                # next to one unreadable package.
                self.store.save(state)
                reports.append(report)
        return SynchronisationReport(
            directions=tuple(reports),
            pending_exports=tuple(export.reference for export in state.pending),
            state_path=str(self.store.path),
        )

    def _advance_one(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        direction: InvoiceDirection,
    ) -> tuple[SyncState, DirectionReport]:
        state, report = self._attempt(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state,
            direction=direction,
        )
        if report.outcome is not SyncOutcome.RECOVERED:
            return state, report
        # The lost package is off the record and the point is back before the
        # window it covered, so this subject type is asking KSeF for nothing and
        # owed a window. Asking for it now rather than next pass is what makes
        # the recovery one run instead of two. It cannot recur: the second
        # attempt finds no queued export, and `is_due` and the export budget
        # still gate whatever it does next.
        state, resumed = self._attempt(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state,
            direction=direction,
        )
        return state, replace(resumed, detail=f"{report.detail} {resumed.detail}")

    def _attempt(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        direction: InvoiceDirection,
    ) -> tuple[SyncState, DirectionReport]:
        queued = state.pending_for(direction)
        stored = state.directions.get(direction)
        if queued is not None and queued.state is ExportState.RUNNING:
            return self._resume(
                session=session,
                budget=budget,
                retriever=retriever,
                state=state,
                export=queued,
            )
        if queued is not None and queued.state is ExportState.READY:
            # A package a previous pass fetched and could not store. Finishing
            # it costs neither an export nor a status query, so it goes first —
            # and until it lands, this subject type asks KSeF for nothing new.
            return self._archive(
                session=session,
                budget=budget,
                retriever=retriever,
                state=state,
                export=queued,
                settled=SyncOutcome.ARCHIVED,
                detail=f"Paczka {queued.reference} z poprzedniego przebiegu trafiła do archiwum.",
                reached=None if stored is None else stored.reached,
            )
        moment = self.clock()
        if not is_due(direction=direction, stored=stored, moment=moment):
            return state, DirectionReport(
                direction=direction,
                outcome=SyncOutcome.NOT_DUE,
                detail="Za wcześnie na kolejny eksport dla tego typu podmiotu.",
                reached=None if stored is None else stored.reached,
            )
        return self._start(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state,
            direction=direction,
            stored=stored,
            moment=moment,
        )

    def _start(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        direction: InvoiceDirection,
        stored: DirectionState | None,
        moment: datetime,
    ) -> tuple[SyncState, DirectionReport]:
        opening = DirectionState(reached=moment - INITIAL_LOOKBACK) if stored is None else stored
        try:
            budget.spend(Operation.EXPORT)
        except KsefRequestRejected as refusal:
            return state, DirectionReport(
                direction=direction,
                outcome=SyncOutcome.BUDGET_SPENT,
                detail=str(refusal),
                reached=opening.reached,
            )
        handle = session.start_export(
            # The pass's own `moment`, not a second reading of the clock: the
            # window's two ends have to come from one instant, or a slow run
            # widens the very span the ceiling is there to bound.
            period=Period.for_synchronisation(since=opening.reached, now=moment),
            direction=direction,
        )
        queued = PendingExport(
            reference=handle.reference,
            direction=direction,
            started_at=moment,
            encryption=handle.encryption,
            state=ExportState.RUNNING,
            # Where the point stood before this export moved it. Kept with the
            # export rather than beside the point, because it is only ever read
            # to undo this one export, and it dies with it (GH-93).
            covering_from=opening.reached,
        )
        # The attempt is recorded before the package is, so a run that dies
        # while polling still holds the fifteen-minute floor open.
        advanced = state.with_pending(queued).with_direction(
            direction,
            DirectionState(reached=opening.reached, attempted_at=moment),
        )
        return self._resume(
            session=session,
            budget=budget,
            retriever=retriever,
            state=advanced,
            export=queued,
        )

    def _resume(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        export: PendingExport,
    ) -> tuple[SyncState, DirectionReport]:
        status = self._poll(session=session, budget=budget, export=export)
        if status is None:
            return state, DirectionReport(
                direction=export.direction,
                outcome=SyncOutcome.BUDGET_SPENT,
                detail=(
                    "Godzinowy budżet odpytań o status jest wyczerpany; "
                    "eksport czeka zapisany na dysku."
                ),
            )
        if status.state is ExportState.RUNNING:
            return state, DirectionReport(
                direction=export.direction,
                outcome=SyncOutcome.STILL_RUNNING,
                detail=(
                    f"KSeF nadal buduje paczkę {export.reference}; dokończy ją kolejny przebieg."
                ),
            )
        if status.state is ExportState.FAILED:
            # The point never moved, so the same window is asked for again —
            # one export spent, nothing skipped.
            return state.with_settled(
                # Recorded rather than dropped: a reference nobody can explain
                # later is worse than one marked as the failure it was. It goes
                # to the journal and not back on the queue, because KSeF will
                # never build this package and a queue entry nothing can finish
                # blocks its subject type for good (GH-94). The key does not
                # stay with it either: this export is over, and a key that
                # outlives the export it belongs to is exactly what D-033
                # forbids.
                PendingExport(
                    reference=export.reference,
                    direction=export.direction,
                    started_at=export.started_at,
                    encryption=None,
                    state=ExportState.FAILED,
                )
            ), DirectionReport(
                direction=export.direction,
                outcome=SyncOutcome.FAILED,
                detail=f"KSeF odrzucił eksport {export.reference}.",
            )
        return self._complete(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state,
            export=export,
            status=status,
        )

    def _complete(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        export: PendingExport,
        status: ExportStatus,
    ) -> tuple[SyncState, DirectionReport]:
        stored = state.directions[export.direction]
        moved = advance(
            stored.continuation_point(direction=export.direction),
            status=status,
        )
        ready = PendingExport(
            reference=export.reference,
            direction=export.direction,
            started_at=export.started_at,
            encryption=export.encryption,
            state=ExportState.READY,
            parts=status.parts,
            invoice_count=status.invoice_count,
            # Carried, not recomputed: the window this export asked for is a fact
            # about the export, and the point has already moved past it by now.
            covering_from=export.covering_from,
        )
        carrying = state.with_pending(ready)
        if moved is None:
            return self._archive(
                session=session,
                budget=budget,
                retriever=retriever,
                state=carrying,
                export=ready,
                settled=SyncOutcome.INCONCLUSIVE,
                detail=(
                    "Paczka jest zarchiwizowana, ale KSeF nie podał znacznika "
                    "kontynuacji; punkt zostaje tam, gdzie był."
                ),
                reached=stored.reached,
            )
        # The point and the parts that justify it are written by one rename: a
        # point ahead of a package nobody recorded would declare a period
        # complete that was never fetched.
        return self._archive(
            session=session,
            budget=budget,
            retriever=retriever,
            state=carrying.with_direction(
                export.direction,
                DirectionState(reached=moved.reached, attempted_at=stored.attempted_at),
            ),
            export=ready,
            settled=SyncOutcome.ARCHIVED,
            detail=f"Paczka {export.reference} trafiła do archiwum.",
            reached=moved.reached,
        )

    def _archive(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        export: PendingExport,
        settled: SyncOutcome,
        detail: str,
        reached: datetime | None,
        renewed: bool = False,
    ) -> tuple[SyncState, DirectionReport]:
        """Fetch the parts, store the invoices, and let the key go with them.

        Downloading a part spends no allowance and so counts against no
        `Operation`: the part URLs are presigned links to external storage,
        reached without a KSeF credential, while the `invoice_download` family
        `GET /rate-limits` reports is the sixty-four-per-hour ceiling on
        fetching a single invoice by its number (D-031 §1, ADR-104). Counting
        them there would refuse an allowance nobody spent, and would abort a
        many-part package halfway through a stream whose export is already paid
        for out of twenty an hour.
        """
        # Written before the first part is fetched, and that ordering is the
        # point: `PackageRetriever.archive` drops the record by reloading this
        # very file, so a record still only in memory would be replaced by a
        # document that never knew about it — and with it would go the key.
        self.store.save(state)
        archivist = PackageArchivist(archive=self.archive)
        try:
            retriever.archive(export=export, archivist=archivist)
        except PackageLinkExpired as expiry:
            # Ahead of the list below, because this is the one refusal waiting
            # does not mend. Once already renewed, there is nothing further to
            # ask KSeF in this pass, so it falls back to the same report.
            if renewed:
                return state, self._stalled(export=export, failure=expiry, reached=reached)
            return self._renew(
                session=session,
                budget=budget,
                retriever=retriever,
                state=state,
                export=export,
                expiry=expiry,
                settled=settled,
                detail=detail,
                reached=reached,
            )
        except ARCHIVING_FAILURES as failure:
            # The record keeps its key and its parts (ADR-104 §2), and the
            # continuation point stays exactly where the package itself put it:
            # it moves on what KSeF confirmed, never on whether this run managed
            # to write the files (ADR-103 §3).
            return state, self._stalled(export=export, failure=failure, reached=reached)
        stored = archivist.reported
        return self.store.load(), DirectionReport(
            direction=export.direction,
            outcome=settled,
            detail=detail,
            invoice_count=export.invoice_count,
            part_count=len(export.parts),
            reached=reached,
            archived=stored.archived,
            already_held=stored.already_held,
            archive_directory=stored.directory,
        )

    def _stalled(
        self,
        *,
        export: PendingExport,
        failure: Exception,
        reached: datetime | None,
    ) -> DirectionReport:
        """The window is still owed and the record on disk is what says so.

        The message names the failure, never the invoice — these exceptions
        carry no package content (D-011).
        """
        return DirectionReport(
            direction=export.direction,
            outcome=SyncOutcome.NOT_ARCHIVED,
            detail=(
                f"Paczka {export.reference} czeka na dysku z kluczem, "
                f"bo archiwizacja się nie udała: {failure}"
            ),
            invoice_count=export.invoice_count,
            part_count=len(export.parts),
            reached=reached,
        )

    def _renew(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        export: PendingExport,
        expiry: PackageLinkExpired,
        settled: SyncOutcome,
        detail: str,
        reached: datetime | None,
    ) -> tuple[SyncState, DirectionReport]:
        """Ask KSeF for the export again before concluding the package is lost.

        A presigned link dies on a clock of its own, so the parts on record can
        be unusable while the export behind them is perfectly alive. Asking
        costs a status query and not an export — by far the cheaper question,
        and the only one that can hand back working links.
        """
        try:
            budget.spend(Operation.EXPORT_STATUS)
        except KsefRequestRejected:
            # No allowance left to ask with. The record keeps its key and its
            # parts, so the next pass asks instead of guessing now.
            return state, self._stalled(export=export, failure=expiry, reached=reached)
        try:
            status = session.check_export(handle=export.handle)
        except KsefRefused as refusal:
            # KSeF answered, and the answer was about this export: it is gone.
            return self._abandon(state=state, export=export, refusal=refusal)
        except KsefPortError:
            # Everything else the port can raise says nothing about whether the
            # export still exists — no answer at all, a rate limit, a session
            # that expired. Reading any of them as "KSeF no longer serves it"
            # would drop a live package with its key and roll the point back
            # for a network blip. It would also turn a 429 into a fresh export
            # request on the very next line, which is the retry pattern the
            # Ministry records and answers with a lengthening block.
            return state, self._stalled(export=export, failure=expiry, reached=reached)
        if status.state is not ExportState.READY or not status.parts:
            return self._abandon(state=state, export=export, refusal=None)
        renewed = replace(export, parts=status.parts)
        return self._archive(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state.with_pending(renewed),
            export=renewed,
            settled=settled,
            detail=detail,
            reached=reached,
            renewed=True,
        )

    def _abandon(
        self,
        *,
        state: SyncState,
        export: PendingExport,
        refusal: KsefRefused | None,
    ) -> tuple[SyncState, DirectionReport]:
        """Take a lost export off the record and put the point back before it.

        This is the only place the continuation point moves backwards, and it
        moves only for an export KSeF itself no longer serves. Leaving the
        record in place instead would hold the subject type against a door that
        will not open again, with the point already past the window behind it —
        the deadlock GH-93 was reported from.

        `refusal` is what KSeF said when asked, or `None` when it answered
        without refusing and simply had no parts to offer. Either way the
        message carries it, so the next operator reading the report knows which
        of the two happened without going back to the registry.
        """
        stored = state.directions.get(export.direction)
        returned = rolled_back_to(export, stored=stored)
        answer = "nie ma już jej części" if refusal is None else str(refusal)
        return state.without_export(reference=export.reference).with_direction(
            export.direction,
            DirectionState(
                reached=returned,
                attempted_at=None if stored is None else stored.attempted_at,
            ),
        ), DirectionReport(
            direction=export.direction,
            outcome=SyncOutcome.RECOVERED,
            detail=(
                f"Odnośniki do paczki {export.reference} wygasły, a KSeF "
                f"odpowiedział, że {answer}; wpis zdjęty, punkt kontynuacji "
                f"cofnięty na {returned.isoformat()}."
            ),
            reached=returned,
        )

    def _poll(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        export: PendingExport,
    ) -> ExportStatus | None:
        status: ExportStatus | None = None
        for attempt in range(self.poll_attempts):
            try:
                budget.spend(Operation.EXPORT_STATUS)
            except KsefRequestRejected:
                return status
            status = session.check_export(handle=export.handle)
            if status.state is not ExportState.RUNNING:
                return status
            # No wait after the last look: the package is recorded on disk, and
            # sitting in the call adds nothing the next run will not do for free.
            if attempt + 1 < self.poll_attempts:
                self.sleep(self.poll_interval.total_seconds())
        return status
