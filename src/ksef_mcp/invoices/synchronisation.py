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
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from ksef_mcp.allowance import Allowance, CeilingNotice
from ksef_mcp.clock import now_utc
from ksef_mcp.diagnostics import technical_log
from ksef_mcp.invoices.package import PackageRetriever, PackageUnreadable
from ksef_mcp.ksef_port.budget import QueryBudget
from ksef_mcp.ksef_port.errors import (
    KsefPortError,
    KsefRefused,
    KsefRequestRejected,
    PackageLinkExpired,
)
from ksef_mcp.ksef_port.protocol import KsefPort, KsefSession
from ksef_mcp.ksef_port.types import (
    MAX_QUERY_WINDOW,
    SYNCHRONISED_SUBJECT_ROLES,
    ContinuationPoint,
    Credential,
    ExportState,
    ExportStatus,
    Operation,
    Period,
    SubjectRole,
)
from ksef_mcp.storage.archive import (
    ArchiveIndexUnreadable,
    ArchiveMetadataUnusable,
    InvoiceArchive,
    PackageArchivist,
)
from ksef_mcp.storage.audit import (
    XML_FORMAT,
    AuditedOperation,
    AuditEntry,
    Authorisation,
    Disclosure,
)
from ksef_mcp.storage.sync_store import (
    ContinuationPointMissing,
    ExportKeyDiscarded,
    PendingExport,
    SubjectRoleState,
    SyncState,
    SyncStore,
)

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

# How long an export may stay in `running` before a pass stops believing in it,
# takes the record off the queue and asks for the same window again.
#
# From observed behaviour rather than from taste (GH-190). In the production run
# this came from, the exports that carried invoices were requested, built,
# fetched and archived inside one evening, while two that carried none were
# still `running` more than thirteen hours later — across four releases of this
# connector. A day leaves a healthy export many times the room it has ever
# needed, and it costs the rare subject types nothing at all: `OCCASIONAL_INTERVAL`
# already asks them once a day, so the retry lands on a pass that was due anyway.
#
# Abandoning is one export request, exactly what the `FAILED` branch below
# already spends. Nothing here polls harder than before — a shorter ceiling
# would, and repeated requests are the pattern the Ministry answers with a
# lengthening block (D-031 §5).
ABANDONED_AFTER: Final[timedelta] = timedelta(hours=24)

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
    EMPTY_WINDOW = "empty_window"
    NOT_DUE = "not_due"
    BUDGET_SPENT = "budget_spent"
    INCONCLUSIVE = "inconclusive"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class SubjectRoleReport:
    """One subject type's result. Paths and KSeF numbers, never invoice bodies (D-011)."""

    subject_role: SubjectRole
    outcome: SyncOutcome
    message: str
    invoice_count: int = 0
    part_count: int = 0
    reached: datetime | None = None
    archived: tuple[str, ...] = ()
    already_held: tuple[str, ...] = ()
    archive_directory: str | None = None


@dataclass(frozen=True)
class SynchronisationReport:
    subject_roles: tuple[SubjectRoleReport, ...]
    pending_exports: tuple[str, ...]
    state_path: str
    # Required rather than defaulted: the only honest default would be a
    # granted ceiling, and a pass that ran on an assumed one would then report
    # the opposite of what happened (GH-118).
    ceilings: CeilingNotice


def _subject_role_entries(
    reported: SubjectRoleReport,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    reached = "unknown" if reported.reached is None else reported.reached.isoformat()
    common = {
        "recorded_at": moment,
        "operation": AuditedOperation.SYNCHRONISATION,
        "authorisation": authorisation,
        "subject_role": str(reported.subject_role),
        "criteria": f"export packages up to {reached}",
        "output_path": reported.archive_directory,
    }
    written = (
        AuditEntry(
            disclosure=Disclosure.DISK,
            document_count=len(reported.archived),
            ksef_numbers=reported.archived,
            formats=(XML_FORMAT,),
            **common,  # type: ignore[arg-type]
        ),
    )
    # Logged as its own event rather than folded into the write or left out
    # altogether: a trail that records only what was stored would read as though
    # these invoices had never been touched, when in fact each one was seen and
    # correctly recognised as already held (#38, #57).
    skipped = (
        AuditEntry(
            disclosure=Disclosure.DEDUPLICATION_SKIP,
            document_count=len(reported.already_held),
            ksef_numbers=reported.already_held,
            formats=(),
            **common,  # type: ignore[arg-type]
        ),
    )
    return (
        *(written if reported.archived else ()),
        *(skipped if reported.already_held else ()),
    )


def synchronisation_entries(
    report: SynchronisationReport,
    *,
    authorisation: Authorisation,
    moment: datetime,
) -> tuple[AuditEntry, ...]:
    """One entry per subject type per outcome — stored, and seen but already held.

    Beside the report it translates rather than in the tool that calls it, for
    the reason `purge_entry` is beside `PurgeReport`: the module that knows what
    a pass did is the one that can say what the trail should record about it,
    and a second delivery surface asking the same question does not get to
    rebuild the answer by hand (#135).
    """
    return tuple(
        entry
        for reported in report.subject_roles
        for entry in _subject_role_entries(
            reported,
            authorisation=authorisation,
            moment=moment,
        )
    )


def advance(point: ContinuationPoint, *, status: ExportStatus) -> ContinuationPoint | None:
    """Move the point to the marker the export names, or nowhere when it names none."""
    marker = status.continuation_marker
    if marker is None:
        return None
    return point.advanced_to(marker=marker)


def rolled_back_to(export: PendingExport, *, stored: SubjectRoleState | None) -> datetime:
    """Where a subject type's point goes once an export is confirmed unreachable.

    A record that kept the window start it asked for goes back to exactly that.
    One written before that field existed goes back a whole window from when the
    export was queued — the safer way to err, because a range already held
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


def in_hours(span: timedelta) -> int:
    """The ceiling spelled out for a reader, so the message cannot drift from it."""
    return int(span.total_seconds() // 3600)


def standing_at(state: SyncState, *, export: PendingExport) -> SubjectRoleState:
    """Where the subject type this export belongs to stands, or a refusal.

    The point does not move while an export waits, so this is also where the
    export's own window began — which is why both endings that advance it read
    the point from here rather than from anything the export carries.

    A record carrying a queued export for a subject type that has no
    continuation point says nothing about where that window started, and a
    guessed start skips invoices no later run ever asks for again. Refusing by
    name beats the `KeyError` this used to raise from the middle of a pass,
    which said nothing about what was wrong.
    """
    stored = state.subject_roles.get(export.subject_role)
    if stored is None:
        raise ContinuationPointMissing(
            f"Eksport {export.reference} czeka na typ podmiotu, dla którego "
            f"zapis synchronizacji nie ma punktu kontynuacji. Punkt musi "
            f"wynikać z zapisu, nie ze zgadywania — usuń wpis oczekującego "
            f"eksportu, żeby ten typ podmiotu zaczął sekwencję od nowa."
        )
    return stored


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

    def run(self, *, nip: str, token: Credential) -> SynchronisationReport:
        """Advance every subject type, as the only writer this subject has.

        The hold spans the whole pass, not each write. A pass reads the record
        once and writes it four times, and a second pass starting from the same
        snapshot would overwrite the first one's continuation points and queued
        keys with a document that never knew about them (GH-101). Two passes for
        one subject also spend one allowance twice over, so the second one
        refusing outright is the better outcome on both counts (ADR-107 §2).
        """
        with self.store.exclusively():
            return self._advance_all(nip=nip, token=token)

    def _advance_all(self, *, nip: str, token: Credential) -> SynchronisationReport:
        state = self.store.load()
        reports: list[SubjectRoleReport] = []
        with self.port.session(nip=nip, token=token) as opened:
            session = self.allowance.guarded(session=opened)
            reading = self.allowance.reading(session=session)
            budget = reading.budget
            retriever = PackageRetriever(session=session, store=self.store)
            for subject_role in SYNCHRONISED_SUBJECT_ROLES:
                state, report = self._advance_one(
                    session=session,
                    budget=budget,
                    retriever=retriever,
                    state=state,
                    subject_role=subject_role,
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
                technical_log().info(
                    "Subject role %s finished as %s.",
                    report.subject_role,
                    report.outcome,
                )
                reports.append(report)
        return SynchronisationReport(
            subject_roles=tuple(reports),
            pending_exports=tuple(export.reference for export in state.pending),
            state_path=str(self.store.path),
            ceilings=reading.ceilings,
        )

    def _advance_one(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        subject_role: SubjectRole,
    ) -> tuple[SyncState, SubjectRoleReport]:
        state, report = self._attempt(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state,
            subject_role=subject_role,
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
            subject_role=subject_role,
        )
        return state, replace(resumed, message=f"{report.message} {resumed.message}")

    def _attempt(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        subject_role: SubjectRole,
    ) -> tuple[SyncState, SubjectRoleReport]:
        queued = state.pending_for(subject_role)
        stored = state.subject_roles.get(subject_role)
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
                message=f"Paczka {queued.reference} z poprzedniego przebiegu trafiła do archiwum.",
                reached=None if stored is None else stored.reached,
            )
        moment = self.clock()
        # Where a subject type with no record on disk stands, built before the
        # due check rather than inside `_start`: the night window governs a
        # subject type nobody has ever asked for exactly as it governs one with
        # a record, so the question has to be put to a state either way.
        opening = SubjectRoleState(reached=moment - INITIAL_LOOKBACK) if stored is None else stored
        if not opening.is_due(subject_role=subject_role, moment=moment):
            return state, SubjectRoleReport(
                subject_role=subject_role,
                outcome=SyncOutcome.NOT_DUE,
                message="Za wcześnie na kolejny eksport dla tego typu podmiotu.",
                reached=None if stored is None else stored.reached,
            )
        return self._start(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state,
            subject_role=subject_role,
            opening=opening,
            moment=moment,
        )

    def _start(
        self,
        *,
        session: KsefSession,
        budget: QueryBudget,
        retriever: PackageRetriever,
        state: SyncState,
        subject_role: SubjectRole,
        opening: SubjectRoleState,
        moment: datetime,
    ) -> tuple[SyncState, SubjectRoleReport]:
        try:
            budget.spend(Operation.EXPORT)
        except KsefRequestRejected as refusal:
            return state, SubjectRoleReport(
                subject_role=subject_role,
                outcome=SyncOutcome.BUDGET_SPENT,
                message=str(refusal),
                reached=opening.reached,
            )
        handle = session.start_export(
            # The pass's own `moment`, not a second reading of the clock: the
            # window's two ends have to come from one instant, or a slow run
            # widens the very span the ceiling is there to bound.
            period=Period.for_synchronisation(since=opening.reached, now=moment),
            subject_role=subject_role,
        )
        queued = PendingExport.queued(
            handle=handle,
            subject_role=subject_role,
            started_at=moment,
            # Where the point stood before this export moved it. Kept with the
            # export rather than beside the point, because it is only ever read
            # to undo this one export, and it dies with it (GH-93).
            covering_from=opening.reached,
        )
        # The attempt is recorded before the package is, so a run that dies
        # while polling still holds the fifteen-minute floor open.
        advanced = state.with_pending(queued).with_subject_role(
            subject_role,
            SubjectRoleState(reached=opening.reached, attempted_at=moment),
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
    ) -> tuple[SyncState, SubjectRoleReport]:
        status = self._poll(session=session, budget=budget, export=export)
        if status is None:
            return state, SubjectRoleReport(
                subject_role=export.subject_role,
                outcome=SyncOutcome.BUDGET_SPENT,
                message=(
                    "Godzinowy budżet odpytań o status jest wyczerpany; "
                    "eksport czeka zapisany na dysku."
                ),
            )
        if status.state is ExportState.RUNNING:
            waiting = self.clock() - export.started_at
            if waiting >= ABANDONED_AFTER:
                # Past the ceiling the record stops being a promise and starts
                # being a lock: the point cannot move until the package closes,
                # and a package that has not closed in a day is one nothing
                # here can distinguish from one that never will (GH-190). The
                # same road GH-93 built for a package KSeF no longer serves —
                # entry off the queue, point back before its window, one export
                # spent asking again.
                return self._abandon(
                    state=state,
                    export=export,
                    reason=(
                        f"KSeF buduje paczkę {export.reference} od "
                        f"{export.started_at.isoformat()}, czyli dłużej niż "
                        f"{in_hours(ABANDONED_AFTER)} h; przebieg uznaje eksport za porzucony."
                    ),
                )
            # The moment it was asked for, not a bare „nadal buduje": that
            # sentence read the same after one minute and after thirteen hours,
            # so a stuck subject type looked healthy in the report and only the
            # state file on disk said otherwise (GH-190).
            return state, SubjectRoleReport(
                subject_role=export.subject_role,
                outcome=SyncOutcome.STILL_RUNNING,
                message=(
                    f"KSeF buduje paczkę {export.reference} od "
                    f"{export.started_at.isoformat()}; dokończy ją kolejny przebieg."
                ),
            )
        if status.state is ExportState.EMPTY:
            return self._settle_empty(state=state, export=export)
        if status.state is ExportState.FAILED:
            # The point never moved, so the same window is asked for again —
            # one export spent, nothing skipped.
            # Recorded rather than dropped: a reference nobody can explain later
            # is worse than one marked as the failure it was. It goes to the
            # journal and not back on the queue, because KSeF will never build
            # this package and a queue entry nothing can finish blocks its
            # subject type for good (GH-94).
            return state.with_settled(export.refused()), SubjectRoleReport(
                subject_role=export.subject_role,
                outcome=SyncOutcome.FAILED,
                message=f"KSeF odrzucił eksport {export.reference}.",
            )
        return self._complete(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state,
            export=export,
            status=status,
        )

    def _settle_empty(
        self,
        *,
        state: SyncState,
        export: PendingExport,
    ) -> tuple[SyncState, SubjectRoleReport]:
        """KSeF closed this export and there was nothing in the window to build.

        The point moves although KSeF named no continuation marker, which is the
        one exception to the rule everywhere else in this module — and it is not
        a guess. A closed export answers for the whole window it was given, so
        the window's own end is where the subject type now stands.

        That end is computed from the record rather than from `started_at`
        alone: `Period.for_synchronisation` closes a window at
        `min(now, since + MAX_QUERY_WINDOW)`, so a subject type that had fallen
        far behind was handed a clipped window and moving it to the moment of
        the request would skip everything between (D-031 §4).
        """
        stored = standing_at(state, export=export)
        reached = min(export.started_at, stored.reached + MAX_QUERY_WINDOW)
        return state.with_settled(export.emptied()).with_subject_role(
            export.subject_role,
            SubjectRoleState(reached=reached, attempted_at=stored.attempted_at),
        ), SubjectRoleReport(
            subject_role=export.subject_role,
            outcome=SyncOutcome.EMPTY_WINDOW,
            message=(
                f"KSeF zamknął eksport {export.reference} bez żadnej faktury; "
                f"okno było puste, punkt kontynuacji przesunięty na {reached.isoformat()}."
            ),
            reached=reached,
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
    ) -> tuple[SyncState, SubjectRoleReport]:
        stored = standing_at(state, export=export)
        moved = advance(
            stored.continuation_point(subject_role=export.subject_role),
            status=status,
        )
        # The parts arrive by transition rather than by rewriting the record:
        # the window this export asked for is a fact about the export, and it is
        # carried rather than recomputed once the point has moved past it.
        ready = export.built(status=status)
        carrying = state.with_pending(ready)
        if moved is None:
            return self._archive(
                session=session,
                budget=budget,
                retriever=retriever,
                state=carrying,
                export=ready,
                settled=SyncOutcome.INCONCLUSIVE,
                message=(
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
            state=carrying.with_subject_role(
                export.subject_role,
                SubjectRoleState(reached=moved.reached, attempted_at=stored.attempted_at),
            ),
            export=ready,
            settled=SyncOutcome.ARCHIVED,
            message=f"Paczka {export.reference} trafiła do archiwum.",
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
        message: str,
        reached: datetime | None,
        renewed: bool = False,
    ) -> tuple[SyncState, SubjectRoleReport]:
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
                message=message,
                reached=reached,
            )
        except ARCHIVING_FAILURES as failure:
            # The record keeps its key and its parts (ADR-104 §2), and the
            # continuation point stays exactly where the package itself put it:
            # it moves on what KSeF confirmed, never on whether this run managed
            # to write the files (ADR-103 §3).
            return state, self._stalled(export=export, failure=failure, reached=reached)
        stored = archivist.reported
        return self.store.load(), SubjectRoleReport(
            subject_role=export.subject_role,
            outcome=settled,
            message=message,
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
    ) -> SubjectRoleReport:
        """The window is still owed and the record on disk is what says so.

        The message names the failure, never the invoice — these exceptions
        carry no package content (D-011) and no KSeF number in full (D-038).

        The journal entry is what makes the pass reconstructible without asking
        KSeF for the package a second time out of twenty exports an hour
        (GH-116). It is the technical journal and not `AuditTrail`: that one
        records data access and would misreport a failure as a disclosure.
        """
        technical_log().warning(
            "Export %s for subject role %s stayed on disk with its key: %s",
            export.reference,
            export.subject_role,
            failure,
        )
        return SubjectRoleReport(
            subject_role=export.subject_role,
            outcome=SyncOutcome.NOT_ARCHIVED,
            message=(
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
        message: str,
        reached: datetime | None,
    ) -> tuple[SyncState, SubjectRoleReport]:
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
            return self._abandon(
                state=state,
                export=export,
                reason=self._expired(export=export, answer=str(refusal)),
            )
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
            return self._abandon(
                state=state,
                export=export,
                reason=self._expired(export=export, answer="nie ma już jej części"),
            )
        renewed = export.relinked(parts=status.parts)
        return self._archive(
            session=session,
            budget=budget,
            retriever=retriever,
            state=state.with_pending(renewed),
            export=renewed,
            settled=settled,
            message=message,
            reached=reached,
            renewed=True,
        )

    def _expired(self, *, export: PendingExport, answer: str) -> str:
        """What `_abandon` is told when the links died and KSeF confirmed the loss."""
        return f"Odnośniki do paczki {export.reference} wygasły, a KSeF odpowiedział, że {answer}."

    def _abandon(
        self,
        *,
        state: SyncState,
        export: PendingExport,
        reason: str,
    ) -> tuple[SyncState, SubjectRoleReport]:
        """Take an export nothing can finish off the record and put the point back.

        This is the only place the continuation point moves backwards, and it
        moves for the two endings a later pass cannot mend: an export KSeF no
        longer serves (GH-93), and one still unbuilt long past the ceiling
        anybody would wait (GH-190). Leaving either record in place holds the
        subject type against a door that will not open, with the point already
        past the window behind it.

        `reason` is the sentence the caller has and this method does not — which
        of those two happened, and what KSeF said if it was asked — so the
        operator reading the report never has to go back to the registry to
        find out.
        """
        stored = state.subject_roles.get(export.subject_role)
        returned = rolled_back_to(export, stored=stored)
        return state.without_export(reference=export.reference).with_subject_role(
            export.subject_role,
            SubjectRoleState(
                reached=returned,
                attempted_at=None if stored is None else stored.attempted_at,
            ),
        ), SubjectRoleReport(
            subject_role=export.subject_role,
            outcome=SyncOutcome.RECOVERED,
            message=(
                f"{reason} Wpis zdjęty, punkt kontynuacji cofnięty na {returned.isoformat()}."
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
