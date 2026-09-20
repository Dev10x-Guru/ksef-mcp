"""The synchronisation pass: one subject type at a time, never past the budget.

Every test drives a scripted port rather than KSeF. The suite must say the same
thing on a machine with no network as in CI, and a live export here would spend
one of twenty an hour against a real allowance.

The package the scripted port serves is built here — a synthetic ZIP with a
manifest, encrypted with a key this file invents — so the assertions can follow
one call all the way to `<NumerKSeF>.xml` on disk. No invoice body in this file
belongs to a real taxpayer (D-011).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ksef_mcp.allowance import LEDGER_FILE
from ksef_mcp.archive import INDEX_FILE, InvoiceArchive
from ksef_mcp.ksef_port import (
    ContinuationPoint,
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    ExportStatus,
    KsefAuthenticationFailed,
    KsefEnvironment,
    KsefLimits,
    KsefNumber,
    KsefRateLimited,
    KsefRefused,
    KsefUnreachable,
    MetadataPage,
    OperationLimit,
    PackageLinkExpired,
    Period,
    RateLimits,
    SessionCeilings,
    SubjectRole,
)
from ksef_mcp.ksef_port.types import MAX_QUERY_WINDOW
from ksef_mcp.sync_store import (
    MINIMUM_INTERVAL,
    ContinuationPointMissing,
    PendingExport,
    SubjectRoleState,
    SyncState,
    SyncStore,
)
from ksef_mcp.synchronisation import (
    ABANDONED_AFTER,
    INITIAL_LOOKBACK,
    SubjectRoleReport,
    SynchronisationReport,
    Synchroniser,
    SyncOutcome,
    advance,
    now_utc,
    rolled_back_to,
)
from tests.conftest import an_allowance
from tests.support.synthetic import (
    aes_encrypted,
    base64_digest,
    synthetic_number,
    synthetic_package,
)

NIP = "1234567890"

TOKEN = "tajny-token"

KEY = b"k" * 32

IV = b"i" * 16

HWM = datetime(2026, 9, 10, tzinfo=UTC)

LAST_SEEN = datetime(2026, 9, 8, tzinfo=UTC)

NOON = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

MIDNIGHT = datetime(2026, 9, 12, 2, 0, tzinfo=UTC)

# One part carrying the whole ZIP. Splitting is `test_package`'s subject; here
# the part only has to be genuine enough to survive both hash checks and unpack.
PACKAGE = synthetic_package(1, 2)

ENCRYPTED_PACKAGE = aes_encrypted(PACKAGE, key=KEY, initialisation_vector=IV)


def a_part(ordinal: int = 1) -> ExportPart:
    return ExportPart(
        ordinal=ordinal,
        name=f"package_part_{ordinal}.zip.aes",
        method="GET",
        url=f"https://storage.example/part/{ordinal}",
        size_bytes=len(PACKAGE),
        content_hash=base64_digest(PACKAGE),
        encrypted_size_bytes=len(ENCRYPTED_PACKAGE),
        encrypted_content_hash=base64_digest(ENCRYPTED_PACKAGE),
    )


def ready(
    *,
    truncated: bool = False,
    hwm_date: datetime | None = HWM,
    last_permanent_storage_date: datetime | None = LAST_SEEN,
) -> ExportStatus:
    return ExportStatus(
        state=ExportState.READY,
        parts=(a_part(),),
        truncated=truncated,
        hwm_date=hwm_date,
        last_permanent_storage_date=last_permanent_storage_date,
        invoice_count=7,
    )


def still_running() -> ExportStatus:
    return ExportStatus(
        state=ExportState.RUNNING,
        parts=(),
        truncated=False,
        hwm_date=None,
        last_permanent_storage_date=None,
        invoice_count=0,
    )


def failed() -> ExportStatus:
    return ExportStatus(
        state=ExportState.FAILED,
        parts=(),
        truncated=False,
        hwm_date=None,
        last_permanent_storage_date=None,
        invoice_count=0,
    )


def allowances(
    *,
    exports_per_hour: int | None = 20,
    exports_per_second: int | None = 8,
    statuses_per_hour: int | None = 200,
    downloads_per_hour: int | None = 64,
) -> KsefLimits:
    """What KSeF grants this scripted context. The narrow windows are stated too.

    `exports_per_second` is wide by default and named rather than buried,
    because the clock in these tests never moves: a pass over four subject types
    reads as four exports in one instant, which is an artefact of `FrozenClock`
    and not of a run. The test that is *about* the per-second ceiling narrows it
    on purpose.
    """
    return KsefLimits(
        rates=RateLimits(
            metadata_queries=OperationLimit(per_second=8, per_minute=16, per_hour=20),
            exports=OperationLimit(
                per_second=exports_per_second,
                per_minute=16,
                per_hour=exports_per_hour,
            ),
            export_statuses=OperationLimit(per_second=8, per_minute=16, per_hour=statuses_per_hour),
            invoice_downloads=OperationLimit(
                per_second=4, per_minute=16, per_hour=downloads_per_hour
            ),
        ),
        ceilings=SessionCeilings(
            max_invoice_megabytes=1,
            max_invoice_with_attachment_megabytes=3,
            max_invoices_per_session=10_000,
        ),
    )


@dataclass
class ScriptedSession:
    statuses: list[ExportStatus]
    limits: KsefLimits
    storage_failure: Exception | None = None
    # How many times each export's links refuse before they work, so a test can
    # say "dead once, then renewed" and "dead however often it is asked".
    expiries: dict[str, int] = field(default_factory=dict)
    # What each export's status query runs into, keyed by reference. Keyed
    # rather than global because one pass asks about several exports, and
    # "KSeF says it does not know this one" must not be spelled the same way
    # as "KSeF could not be reached at all".
    status_failures: dict[str, Exception] = field(default_factory=dict)
    # What ordering an export runs into, keyed by subject type. A pass touches
    # four of them in sequence, so "the third one blew up" is the only way to
    # ask what the first two left on disk.
    start_failures: dict[SubjectRole, Exception] = field(default_factory=dict)
    started: list[tuple[SubjectRole, Period]] = field(default_factory=list)
    polled: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)

    def read_limits(self) -> KsefLimits:
        return self.limits

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        raise AssertionError("Synchronizacja nie odpytuje metadanych — idzie przez eksport.")

    def start_export(self, *, period: Period, subject_role: SubjectRole) -> ExportHandle:
        refusal = self.start_failures.get(subject_role)
        if refusal is not None:
            raise refusal
        self.started.append((subject_role, period))
        return ExportHandle(
            reference=f"EXP-{len(self.started)}",
            encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
        )

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        self.polled.append(handle.reference)
        refusal = self.status_failures.get(handle.reference)
        if refusal is not None:
            raise refusal
        return self.statuses[min(len(self.polled), len(self.statuses)) - 1]

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        self.fetched.append(handle.reference)
        remaining = self.expiries.get(handle.reference, 0)
        if remaining:
            self.expiries[handle.reference] = remaining - 1
            raise PackageLinkExpired(
                f"Storage refused part {part.ordinal} of export {handle.reference} "
                f"with 403. A package link expires."
            )
        if self.storage_failure is not None:
            raise self.storage_failure
        return ENCRYPTED_PACKAGE

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        raise AssertionError("Synchronizacja nie pobiera pojedynczych faktur.")


@dataclass
class ScriptedPort:
    session_double: ScriptedSession
    environment: KsefEnvironment = KsefEnvironment.TEST

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[ScriptedSession]:
        yield self.session_double


@dataclass
class FrozenClock:
    moment: datetime = NOON

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture
def store(tmp_path: Path) -> SyncStore:
    return SyncStore(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)


@pytest.fixture
def archive(tmp_path: Path) -> InvoiceArchive:
    # The same root the store gets: the pass derives the archive from the store
    # precisely so the two cannot point at different subjects (ADR-105 §1).
    return InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)


@pytest.fixture
def naps() -> list[float]:
    return []


@pytest.fixture
def session() -> ScriptedSession:
    return ScriptedSession(statuses=[ready()], limits=allowances())


def a_synchroniser(
    *,
    session: ScriptedSession,
    store: SyncStore,
    naps: list[float],
    moment: datetime = NOON,
) -> Synchroniser:
    return Synchroniser(
        port=ScriptedPort(session_double=session),
        store=store,
        allowance=an_allowance(
            nip=store.nip,
            environment=store.environment,
            root=store.root,
            clock=FrozenClock(moment=moment),
        ),
        clock=FrozenClock(moment=moment),
        sleep=naps.append,
    )


@pytest.fixture
def first_pass(
    session: ScriptedSession, store: SyncStore, naps: list[float]
) -> SynchronisationReport:
    return a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)


@pytest.fixture
def after_a_failed_download(store: SyncStore, naps: list[float]) -> SynchronisationReport:
    session = ScriptedSession(
        statuses=[ready()],
        limits=allowances(),
        storage_failure=KsefUnreachable("Magazyn paczek nie odpowiada."),
    )
    return a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)


def outcome(report: SynchronisationReport, subject_role: SubjectRole) -> SyncOutcome:
    return next(one.outcome for one in report.subject_roles if one.subject_role == subject_role)


def reported(report: SynchronisationReport, subject_role: SubjectRole) -> SubjectRoleReport:
    return next(one for one in report.subject_roles if one.subject_role == subject_role)


def test_every_subject_role_is_reported(first_pass: SynchronisationReport) -> None:
    assert [str(one.subject_role) for one in first_pass.subject_roles] == [
        "seller",
        "buyer",
        "third_subject",
        "authorized_subject",
    ]


def test_the_frequent_subject_roles_are_archived_at_noon(
    first_pass: SynchronisationReport,
) -> None:
    assert (
        outcome(first_pass, SubjectRole.SELLER),
        outcome(first_pass, SubjectRole.BUYER),
    ) == (SyncOutcome.ARCHIVED, SyncOutcome.ARCHIVED)


def test_one_pass_puts_the_invoices_on_disk_under_their_ksef_numbers(
    first_pass: SynchronisationReport, archive: InvoiceArchive
) -> None:
    # The whole point of #57: a single call ends with files, not with the
    # knowledge that KSeF built a package.
    assert sorted(path.name for path in archive.invoice_directory.iterdir()) == [
        f"{synthetic_number(1)}.xml",
        f"{synthetic_number(2)}.xml",
    ]


def test_one_pass_records_what_it_holds_in_the_deduplication_index(
    first_pass: SynchronisationReport, archive: InvoiceArchive
) -> None:
    assert sorted(entry.ksef_number for entry in archive.load_index().entries) == [
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    ]


def test_the_index_lives_beside_the_invoices_rather_than_inside_them(
    first_pass: SynchronisationReport, archive: InvoiceArchive
) -> None:
    # Retention deletes bodies without costing idempotence only while the index
    # is a separate file (D-005, ADR-105 §4).
    assert archive.index_path == archive.directory / INDEX_FILE


def test_the_report_names_the_directory_the_invoices_landed_in(
    first_pass: SynchronisationReport, archive: InvoiceArchive
) -> None:
    assert reported(first_pass, SubjectRole.SELLER).archive_directory == str(
        archive.invoice_directory
    )


def test_the_report_names_the_numbers_it_archived(first_pass: SynchronisationReport) -> None:
    assert reported(first_pass, SubjectRole.SELLER).archived == (
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    )


def test_the_second_subject_role_recognises_invoices_the_first_already_stored(
    first_pass: SynchronisationReport,
) -> None:
    # The same invoice reaches a company as seller and as buyer; deduplication
    # by KSeF number is what stops it being written twice (D-005).
    assert reported(first_pass, SubjectRole.BUYER).already_held == (
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    )


def test_a_second_call_on_the_same_window_writes_nothing_new(
    first_pass: SynchronisationReport,
    session: ScriptedSession,
    store: SyncStore,
    naps: list[float],
) -> None:
    later = a_synchroniser(
        session=session, store=store, naps=naps, moment=NOON + MINIMUM_INTERVAL
    ).run(nip=NIP, token=TOKEN)

    assert reported(later, SubjectRole.SELLER).archived == ()


def test_a_second_call_reports_the_numbers_as_already_held(
    first_pass: SynchronisationReport,
    session: ScriptedSession,
    store: SyncStore,
    naps: list[float],
) -> None:
    later = a_synchroniser(
        session=session, store=store, naps=naps, moment=NOON + MINIMUM_INTERVAL
    ).run(nip=NIP, token=TOKEN)

    assert reported(later, SubjectRole.SELLER).already_held == (
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    )


def test_the_invoice_body_never_reaches_the_report(first_pass: SynchronisationReport) -> None:
    # FA(2)/FA(3) XML carries the counterparty's personal data, so the pass
    # answers with paths and numbers and nothing else (D-011).
    assert "Faktura" not in "".join(one.message for one in first_pass.subject_roles)


@pytest.mark.parametrize(
    "subject_role",
    [SubjectRole.THIRD_SUBJECT, SubjectRole.AUTHORIZED_SUBJECT],
)
def test_the_rare_subject_roles_wait_for_the_night_window(
    first_pass: SynchronisationReport, subject_role: SubjectRole
) -> None:
    # Keeping them off the daytime rotation is what leaves the frequent two
    # their share of twenty exports an hour (D-031 §5).
    assert outcome(first_pass, subject_role) is SyncOutcome.NOT_DUE


def test_the_rare_subject_roles_are_exported_in_the_night_window(
    session: ScriptedSession, store: SyncStore, naps: list[float]
) -> None:
    report = a_synchroniser(session=session, store=store, naps=naps, moment=MIDNIGHT).run(
        nip=NIP, token=TOKEN
    )

    assert outcome(report, SubjectRole.THIRD_SUBJECT) is SyncOutcome.ARCHIVED


def test_a_burst_of_exports_is_refused_by_the_per_second_ceiling_ksef_stated(
    store: SyncStore, naps: list[float]
) -> None:
    # The narrow windows used to be read from KSeF, stored, and never able to
    # refuse anything: only `per_hour` was consulted (GH-97). Four subject types
    # asked for inside one second is exactly the burst the Ministry logs.
    session = ScriptedSession(statuses=[ready()], limits=allowances(exports_per_second=2))

    report = a_synchroniser(session=session, store=store, naps=naps, moment=MIDNIGHT).run(
        nip=NIP, token=TOKEN
    )

    assert [one.outcome for one in report.subject_roles].count(SyncOutcome.BUDGET_SPENT) == 2


def test_a_pass_records_what_it_spent_where_the_next_process_will_find_it(
    store: SyncStore, naps: list[float]
) -> None:
    # The counter used to live and die with one tool call (GH-97). What makes
    # the next process inherit it is the ledger beside the synchronisation
    # record; that it counts is `test_allowance.py`'s subject, that a pass
    # writes it at all is this one's.
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps, moment=MIDNIGHT).run(
        nip=NIP, token=TOKEN
    )

    assert (store.directory / LEDGER_FILE).is_file()


def test_the_export_window_is_pinned_to_permanent_storage(
    first_pass: SynchronisationReport, session: ScriptedSession
) -> None:
    # Any other date type makes incremental fetching unpredictable (D-031 §2).
    assert {period.date_type for _, period in session.started} == {"permanent_storage"}


def test_the_export_window_ends_at_the_moment_the_pass_began(
    first_pass: SynchronisationReport, session: ScriptedSession
) -> None:
    # Niegdyś okno nie miało górnego końca, a ten test tego pilnował — i właśnie
    # dlatego pułap go nie widział. Brak końca nigdy nie oznaczał okna bez
    # ograniczenia: adapter wysyłał w jego miejsce „teraz" (GH-84).
    assert {period.date_to for _, period in session.started} == {NOON}


def test_every_window_of_a_pass_fits_what_ksef_answers(
    first_pass: SynchronisationReport, session: ScriptedSession
) -> None:
    spans = {period.date_to - period.date_from for _, period in session.started}

    assert max(spans) <= MAX_QUERY_WINDOW


def test_a_first_run_starts_one_lookback_back(
    first_pass: SynchronisationReport, session: ScriptedSession
) -> None:
    assert session.started[0][1].date_from == NOON - INITIAL_LOOKBACK


def test_a_completed_package_moves_the_point_to_the_high_water_mark(
    first_pass: SynchronisationReport,
) -> None:
    assert reported(first_pass, SubjectRole.BUYER).reached == HWM


def test_a_completed_package_reports_what_it_carries(
    first_pass: SynchronisationReport,
) -> None:
    assert (
        reported(first_pass, SubjectRole.BUYER).invoice_count,
        reported(first_pass, SubjectRole.BUYER).part_count,
    ) == (7, 1)


def test_an_archived_package_leaves_no_record_behind(
    first_pass: SynchronisationReport, store: SyncStore
) -> None:
    # The record is the one trace that the window was fetched, so it goes only
    # once the invoices are durable — and then it must go, because the key it
    # carries opens nothing any more (D-033, ADR-104 §2).
    assert store.load().pending == ()


def test_the_report_names_the_state_file(
    first_pass: SynchronisationReport, store: SyncStore
) -> None:
    assert first_pass.state_path == str(store.path)


def test_a_pass_that_archived_everything_leaves_nothing_queued(
    first_pass: SynchronisationReport,
) -> None:
    assert first_pass.pending_exports == ()


def test_a_second_pass_within_the_interval_asks_for_nothing(
    first_pass: SynchronisationReport,
    session: ScriptedSession,
    store: SyncStore,
    naps: list[float],
) -> None:
    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert len(session.started) == 2


def test_a_pass_after_the_interval_asks_again(
    first_pass: SynchronisationReport,
    session: ScriptedSession,
    store: SyncStore,
    naps: list[float],
) -> None:
    later = a_synchroniser(
        session=session, store=store, naps=naps, moment=NOON + MINIMUM_INTERVAL
    ).run(nip=NIP, token=TOKEN)

    assert outcome(later, SubjectRole.BUYER) is SyncOutcome.ARCHIVED


def test_the_next_window_starts_where_the_last_one_reached(
    first_pass: SynchronisationReport,
    session: ScriptedSession,
    store: SyncStore,
    naps: list[float],
) -> None:
    # Adjoining windows, never overlapping (D-031 §4).
    a_synchroniser(session=session, store=store, naps=naps, moment=NOON + MINIMUM_INTERVAL).run(
        nip=NIP, token=TOKEN
    )

    assert session.started[-1][1].date_from == HWM


def test_a_truncated_package_continues_from_the_last_invoice_it_carried(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready(truncated=True)], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert reported(report, SubjectRole.BUYER).reached == LAST_SEEN


def test_a_package_still_being_built_is_left_for_the_next_pass(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[still_running()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.STILL_RUNNING


def test_a_package_still_being_built_keeps_its_key_on_disk(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[still_running()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert store.load().pending[0].encryption.initialisation_vector == IV


def test_polling_stops_at_its_bound_rather_than_waiting_the_package_out(
    store: SyncStore, naps: list[float]
) -> None:
    # A tool call that blocks for minutes looks hung; the record on disk is what
    # makes stopping early free.
    session = ScriptedSession(statuses=[still_running()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert session.polled.count("EXP-1") == 3


def test_the_last_look_is_not_followed_by_a_wait(store: SyncStore, naps: list[float]) -> None:
    session = ScriptedSession(statuses=[still_running()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert naps == [10.0, 10.0, 10.0, 10.0]


def test_a_package_that_finishes_while_polling_is_completed(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[still_running(), ready()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.SELLER) is SyncOutcome.ARCHIVED


def left_on_disk(store: SyncStore, *, state: ExportState) -> None:
    """Put a package from an earlier pass in the record, the way a crash would."""
    store.save(
        SyncState(
            subject_roles={SubjectRole.BUYER: SubjectRoleState(reached=LAST_SEEN)},
            pending=(
                PendingExport(
                    reference="EXP-OLD",
                    subject_role=SubjectRole.BUYER,
                    started_at=NOON - timedelta(hours=2),
                    encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
                    state=state,
                    parts=(a_part(),),
                    invoice_count=2,
                ),
            ),
        )
    )


def test_a_queued_export_without_a_continuation_point_is_refused_by_name(
    store: SyncStore, naps: list[float]
) -> None:
    """A raw `KeyError` from the middle of a pass said nothing about what was wrong."""
    store.save(
        SyncState(
            pending=(
                PendingExport(
                    reference="EXP-ORPHAN",
                    subject_role=SubjectRole.SELLER,
                    started_at=NOON - timedelta(hours=2),
                    encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
                    state=ExportState.RUNNING,
                ),
            ),
        )
    )
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    with pytest.raises(ContinuationPointMissing, match="punktu kontynuacji"):
        a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)


# Where the subject type stood before the export that then died — the value a
# rollback has to find its way back to (GH-93).
STUCK_SINCE = datetime(2026, 6, 20, tzinfo=UTC)

STARTED_AT = NOON - timedelta(hours=2)


def a_dead_package(store: SyncStore, *, covering_from: datetime | None = STUCK_SINCE) -> None:
    """The state GH-93 was reported from: a ready package, and a point already past it.

    `covering_from` is `None` in a record written before the fix — the fixture
    says so explicitly rather than leaving the older shape untested.
    """
    store.save(
        SyncState(
            subject_roles={
                SubjectRole.BUYER: SubjectRoleState(reached=HWM, attempted_at=STARTED_AT)
            },
            pending=(
                PendingExport(
                    reference="EXP-DEAD",
                    subject_role=SubjectRole.BUYER,
                    started_at=STARTED_AT,
                    encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
                    state=ExportState.READY,
                    parts=(a_part(),),
                    invoice_count=2,
                    covering_from=covering_from,
                ),
            ),
        )
    )


def a_pass_over_a_dead_package(
    store: SyncStore,
    naps: list[float],
    *,
    statuses: list[ExportStatus],
    covering_from: datetime | None = STUCK_SINCE,
    expiries: int = 1,
    status_failure: Exception | None = None,
    limits: KsefLimits | None = None,
) -> tuple[ScriptedSession, SynchronisationReport]:
    a_dead_package(store, covering_from=covering_from)
    session = ScriptedSession(
        statuses=statuses,
        limits=allowances() if limits is None else limits,
        expiries={"EXP-DEAD": expiries},
        status_failures={} if status_failure is None else {"EXP-DEAD": status_failure},
    )
    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)
    return session, report


def buyer_windows(session: ScriptedSession) -> list[Period]:
    return [period for subject_role, period in session.started if subject_role is SubjectRole.BUYER]


def test_a_queued_package_is_resumed_instead_of_being_asked_for_again(
    store: SyncStore, naps: list[float]
) -> None:
    left_on_disk(store, state=ExportState.RUNNING)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert (outcome(report, SubjectRole.BUYER), "EXP-OLD" in session.polled) == (
        SyncOutcome.ARCHIVED,
        True,
    )


def test_a_queued_package_costs_no_second_export(store: SyncStore, naps: list[float]) -> None:
    left_on_disk(store, state=ExportState.RUNNING)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert [subject_role for subject_role, _ in session.started] == [SubjectRole.SELLER]


def test_a_package_fetched_but_never_stored_is_archived_on_the_next_pass(
    store: SyncStore, naps: list[float]
) -> None:
    left_on_disk(store, state=ExportState.READY)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.ARCHIVED


def test_a_package_fetched_but_never_stored_puts_its_invoices_on_disk(
    store: SyncStore, naps: list[float], archive: InvoiceArchive
) -> None:
    left_on_disk(store, state=ExportState.READY)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert (archive.invoice_directory / f"{synthetic_number(1)}.xml").is_file()


def test_a_package_fetched_but_never_stored_costs_neither_export_nor_status_query(
    store: SyncStore, naps: list[float]
) -> None:
    # The window is already paid for; asking KSeF anything about it again would
    # spend an allowance for an answer the record on disk already holds.
    left_on_disk(store, state=ExportState.READY)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert (
        [subject_role for subject_role, _ in session.started],
        "EXP-OLD" in session.polled,
    ) == ([SubjectRole.SELLER], False)


def test_a_refused_export_is_recorded_as_the_failure_it_was(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.FAILED


def test_a_refused_export_leaves_the_point_where_it_was(
    store: SyncStore, naps: list[float]
) -> None:
    # The same window is asked for again next time — one export spent, nothing
    # skipped.
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert store.load().subject_roles[SubjectRole.BUYER].reached == NOON - INITIAL_LOOKBACK


def test_a_refused_export_is_kept_under_its_reference(store: SyncStore, naps: list[float]) -> None:
    # Kept, so the reference can still be explained later — in the journal,
    # where a record nothing can continue belongs.
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert [export.state for export in store.load().settled] == [
        ExportState.FAILED,
        ExportState.FAILED,
    ]


def test_a_refused_export_leaves_the_queue_empty(store: SyncStore, naps: list[float]) -> None:
    # This is GH-94. A refusal used to sit at the head of the queue for good,
    # and `pending_for` reads the queue — so the subject type ordered a fresh
    # package every fifteen minutes and collected none of them, which is the
    # pattern the Ministry answers with a lengthening block.
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert store.load().pending == ()


def test_a_second_pass_after_a_refusal_polls_the_new_export(
    store: SyncStore, naps: list[float]
) -> None:
    # The proof that the queue is unblocked: the pass that follows a refusal
    # reaches its own export rather than tripping over the dead one again.
    session = ScriptedSession(statuses=[failed()], limits=allowances())
    later = NOON + MINIMUM_INTERVAL

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)
    a_synchroniser(session=session, store=store, naps=naps, moment=later).run(nip=NIP, token=TOKEN)

    assert session.polled == ["EXP-1", "EXP-2", "EXP-3", "EXP-4"]


def test_a_refused_export_does_not_keep_its_key(store: SyncStore, naps: list[float]) -> None:
    # The export is over, so the key opens nothing. Leaving it on disk would be
    # exactly the entry nobody ever cleans up that D-033 refuses.
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert [export.encryption for export in store.load().settled] == [None, None]


@pytest.fixture
def after_a_later_subject_role_blew_up(store: SyncStore, naps: list[float]) -> ScriptedSession:
    """The seller's package is queued and still building; the buyer's ask explodes.

    The explosion is deliberately something no `except` in the pass names, so it
    leaves `run` the way a bug does — which is the case GH-96 is about.
    """
    session = ScriptedSession(
        statuses=[still_running()],
        limits=allowances(),
        start_failures={SubjectRole.BUYER: MemoryError("Zabrakło pamięci.")},
    )
    with pytest.raises(MemoryError):
        a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)
    return session


def test_a_package_ksef_accepted_keeps_its_key_when_a_later_type_blows_up(
    store: SyncStore, after_a_later_subject_role_blew_up: ScriptedSession
) -> None:
    # The whole of GH-96. The state used to be written once, after the loop, so
    # an exception in any subject type took the AES key of every package the
    # earlier ones had queued — and KSeF had already accepted those. Without the
    # key nothing decrypts them, ever (D-033).
    assert [export.encryption for export in store.load().pending] == [
        ExportEncryption(key=KEY, initialisation_vector=IV)
    ]


def test_a_package_ksef_accepted_keeps_its_reference_when_a_later_type_blows_up(
    store: SyncStore, after_a_later_subject_role_blew_up: ScriptedSession
) -> None:
    assert [export.reference for export in store.load().pending] == ["EXP-1"]


def test_an_attempt_already_made_is_held_when_a_later_type_blows_up(
    store: SyncStore, after_a_later_subject_role_blew_up: ScriptedSession
) -> None:
    # `attempted_at` is what holds the fifteen-minute floor open across a
    # restart. Losing it means the next pass asks again immediately, building
    # the retry pattern the Ministry records (D-031 §5).
    assert store.load().subject_roles[SubjectRole.SELLER].attempted_at == NOON


def test_a_ready_package_without_a_continuation_marker_does_not_move_the_point(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready(hwm_date=None)], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.INCONCLUSIVE


def test_a_package_without_a_marker_is_archived_all_the_same(
    store: SyncStore, naps: list[float], archive: InvoiceArchive
) -> None:
    # A missing continuation marker says nothing about the invoices in the
    # package; refusing to store them would lose an export already spent.
    session = ScriptedSession(statuses=[ready(hwm_date=None)], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert (archive.invoice_directory / f"{synthetic_number(1)}.xml").is_file()


def test_a_failed_download_keeps_the_package_and_its_key_on_disk(
    after_a_failed_download: SynchronisationReport, store: SyncStore
) -> None:
    # Nothing D-033 forbids is triggered by a mid-pipeline failure: the export
    # is not over, so its key is still the only way to read the window.
    assert [export.encryption.key for export in store.load().pending] == [KEY, KEY]


def test_a_failed_download_keeps_the_parts_to_retry_with(
    after_a_failed_download: SynchronisationReport, store: SyncStore
) -> None:
    assert store.load().pending[0].parts == (a_part(),)


def test_a_failed_download_is_reported_as_the_gap_it_leaves(
    after_a_failed_download: SynchronisationReport,
) -> None:
    assert outcome(after_a_failed_download, SubjectRole.BUYER) is SyncOutcome.NOT_ARCHIVED


def test_a_failed_download_does_not_roll_the_continuation_point_back(
    after_a_failed_download: SynchronisationReport, store: SyncStore
) -> None:
    # The point moves on what KSeF confirmed, never on whether this run managed
    # to write the files (ADR-103 §3) — the record left on disk is what says the
    # window is still owed.
    assert store.load().subject_roles[SubjectRole.BUYER].reached == HWM


def test_a_failed_download_writes_no_invoice_at_all(
    after_a_failed_download: SynchronisationReport, archive: InvoiceArchive
) -> None:
    assert archive.invoice_directory.exists() is False


def test_a_failed_download_leaves_the_window_to_the_next_pass(
    after_a_failed_download: SynchronisationReport,
) -> None:
    assert after_a_failed_download.pending_exports == ("EXP-1", "EXP-2")


def test_a_dead_package_stops_blocking_its_subject_role(
    store: SyncStore, naps: list[float]
) -> None:
    # GH-93: a package whose presigned links expired used to hold its subject
    # type forever — the record could not be fetched and the point had already
    # moved past the window it covered, so no later pass asked for it again.
    _, report = a_pass_over_a_dead_package(store, naps, statuses=[ready(), failed(), ready()])

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.ARCHIVED


def test_an_expired_link_asks_ksef_about_the_export_before_giving_up_on_it(
    store: SyncStore, naps: list[float]
) -> None:
    # The link and the export expire on different clocks: the parts on record
    # can be unusable while the export behind them is alive.
    session, _ = a_pass_over_a_dead_package(store, naps, statuses=[ready(), ready()])

    assert "EXP-DEAD" in session.polled


def test_an_export_ksef_still_serves_is_read_from_the_links_it_hands_back(
    store: SyncStore, naps: list[float], archive: InvoiceArchive
) -> None:
    a_pass_over_a_dead_package(store, naps, statuses=[ready(), ready()])

    assert (archive.invoice_directory / f"{synthetic_number(1)}.xml").is_file()


def test_renewed_links_cost_a_status_query_and_not_an_export(
    store: SyncStore, naps: list[float]
) -> None:
    # An export is one of twenty an hour shared between four subject types; a
    # status is one of two hundred. Asking the cheap question first is the point.
    session, _ = a_pass_over_a_dead_package(store, naps, statuses=[ready(), ready()])

    assert [subject_role for subject_role, _ in session.started] == [SubjectRole.SELLER]


def test_links_that_stay_dead_after_renewal_leave_the_record_where_it_was(
    store: SyncStore, naps: list[float]
) -> None:
    # KSeF still serves the export, so it is not lost — and a record that is not
    # lost keeps its key and its parts for the next pass (ADR-104 §2).
    _, report = a_pass_over_a_dead_package(store, naps, statuses=[ready(), ready()], expiries=2)

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.NOT_ARCHIVED


def test_links_that_stay_dead_after_renewal_keep_the_export_queued(
    store: SyncStore, naps: list[float]
) -> None:
    _, report = a_pass_over_a_dead_package(store, naps, statuses=[ready(), ready()], expiries=2)

    assert "EXP-DEAD" in report.pending_exports


def test_an_export_ksef_has_forgotten_is_taken_off_the_record(
    store: SyncStore, naps: list[float]
) -> None:
    _, report = a_pass_over_a_dead_package(
        store,
        naps,
        statuses=[ready(), ready()],
        status_failure=KsefRefused("KSeF nie zna eksportu EXP-DEAD."),
    )

    assert "EXP-DEAD" not in report.pending_exports


@pytest.mark.parametrize(
    "failure",
    [
        KsefUnreachable("Brak odpowiedzi od KSeF."),
        KsefRateLimited("Przekroczony limit zapytań.", retry_after=60),
        KsefAuthenticationFailed("Sesja wygasła."),
    ],
)
def test_a_status_query_that_never_reached_ksef_keeps_the_export_on_the_record(
    store: SyncStore, naps: list[float], failure: Exception
) -> None:
    # Cofnięcie punktu należy się wyłącznie eksportowi, którego KSeF sam już
    # nie podaje. Brak odpowiedzi, limit zapytań i wygasła sesja nie mówią nic
    # o istnieniu eksportu — potraktowanie ich jak potwierdzenia utraty
    # kasowałoby żywą paczkę wraz z kluczem.
    _, report = a_pass_over_a_dead_package(
        store, naps, statuses=[ready(), ready()], status_failure=failure
    )

    assert "EXP-DEAD" in report.pending_exports


@pytest.mark.parametrize(
    "failure",
    [
        KsefUnreachable("Brak odpowiedzi od KSeF."),
        KsefRateLimited("Przekroczony limit zapytań.", retry_after=60),
        KsefAuthenticationFailed("Sesja wygasła."),
    ],
)
def test_a_status_query_that_never_reached_ksef_leaves_the_point_alone(
    store: SyncStore, naps: list[float], failure: Exception
) -> None:
    a_pass_over_a_dead_package(store, naps, statuses=[ready(), ready()], status_failure=failure)

    assert store.load().subject_roles[SubjectRole.BUYER].reached == HWM


def test_a_rate_limited_renewal_spends_no_export_on_a_retry(
    store: SyncStore, naps: list[float]
) -> None:
    # Po odpowiedzi „zwolnij" nowe żądanie eksportu w tym samym przebiegu jest
    # dokładnie tym wzorcem, który Ministerstwo Finansów rejestruje jako próbę
    # obchodzenia limitów — a czas blokady rośnie przy powtórzeniach.
    session, _ = a_pass_over_a_dead_package(
        store,
        naps,
        statuses=[ready(), ready()],
        status_failure=KsefRateLimited("Przekroczony limit zapytań.", retry_after=60),
    )

    assert [subject_role for subject_role, _ in session.started] == [SubjectRole.SELLER]


def test_an_export_answered_as_no_longer_ready_is_taken_off_the_record(
    store: SyncStore, naps: list[float]
) -> None:
    # KSeF answers, and the answer is that there is nothing to fetch. Same
    # conclusion as silence, reached from the other subject_role.
    _, report = a_pass_over_a_dead_package(store, naps, statuses=[ready(), failed(), ready()])

    assert "EXP-DEAD" not in report.pending_exports


def test_a_lost_export_puts_the_point_back_at_the_window_it_covered(
    store: SyncStore, naps: list[float]
) -> None:
    # The whole reason the rollback exists: without it the next window starts
    # after the invoices the lost package was carrying.
    session, _ = a_pass_over_a_dead_package(store, naps, statuses=[ready(), failed(), ready()])

    assert [window.date_from for window in buyer_windows(session)] == [STUCK_SINCE]


def test_a_lost_export_is_reported_as_what_was_undone_and_what_followed(
    store: SyncStore, naps: list[float]
) -> None:
    _, report = a_pass_over_a_dead_package(store, naps, statuses=[ready(), failed(), ready()])

    assert "EXP-DEAD" in reported(report, SubjectRole.BUYER).message


def test_a_lost_export_is_reported_alongside_what_replaced_it(
    store: SyncStore, naps: list[float]
) -> None:
    _, report = a_pass_over_a_dead_package(store, naps, statuses=[ready(), failed(), ready()])

    assert "archiwum" in reported(report, SubjectRole.BUYER).message


def test_a_record_from_before_the_window_start_was_kept_reaches_a_whole_window_back(
    store: SyncStore, naps: list[float]
) -> None:
    # Too far back costs one export and deduplication drops the duplicates
    # (D-005); too short loses invoices nothing asks for again.
    session, _ = a_pass_over_a_dead_package(
        store, naps, statuses=[ready(), failed(), ready()], covering_from=None
    )

    assert [window.date_from for window in buyer_windows(session)] == [
        STARTED_AT - MAX_QUERY_WINDOW
    ]


def test_a_renewal_with_no_status_allowance_left_keeps_the_package_and_its_key(
    store: SyncStore, naps: list[float]
) -> None:
    # Nothing left to ask with, so nothing is concluded — the record stands.
    _, report = a_pass_over_a_dead_package(
        store,
        naps,
        statuses=[ready(), ready()],
        limits=allowances(statuses_per_hour=1),
    )

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.NOT_ARCHIVED


def test_a_renewal_with_no_status_allowance_left_keeps_the_export_queued(
    store: SyncStore, naps: list[float]
) -> None:
    _, report = a_pass_over_a_dead_package(
        store,
        naps,
        statuses=[ready(), ready()],
        limits=allowances(statuses_per_hour=1),
    )

    assert "EXP-DEAD" in report.pending_exports


def test_a_rollback_stands_on_the_export_alone_when_no_point_was_recorded(
    store: SyncStore,
) -> None:
    export = PendingExport(
        reference="EXP-DEAD",
        subject_role=SubjectRole.BUYER,
        started_at=STARTED_AT,
        encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
        state=ExportState.READY,
        covering_from=STUCK_SINCE,
    )

    assert rolled_back_to(export, stored=None) == STUCK_SINCE


def test_a_rollback_never_moves_a_point_that_stands_further_back_forward(
    store: SyncStore,
) -> None:
    # A subject type dormant longer than the ceiling stands further back than one
    # window reaches; pulling it up to meet the guess would skip exactly the
    # invoices the rollback exists to recover.
    dormant = STARTED_AT - MAX_QUERY_WINDOW - timedelta(days=200)
    export = PendingExport(
        reference="EXP-DEAD",
        subject_role=SubjectRole.BUYER,
        started_at=STARTED_AT,
        encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
        state=ExportState.READY,
    )

    assert rolled_back_to(export, stored=SubjectRoleState(reached=dormant)) == dormant


# An export that never closes, the shape #190 was reported from: the same
# reference sitting in `running` for hours, carrying no invoice, while the
# exports asked for in the same hour were built, fetched and archived.
HUNG_REFERENCE = "EXP-HUNG"

# Far enough back that one window cannot reach the moment the export was asked
# for, so the clipping `Period.for_synchronisation` does is visible.
LONG_DORMANT = datetime(2026, 1, 1, tzinfo=UTC)

HALF_AN_HOUR = timedelta(minutes=30)

# Six looks that answer „still building" — three for the seller's own export and
# three for the hung one — and then the package the re-asked window comes back
# with. Spelled out because the scripted session serves one list to every poll.
HUNG_THEN_REASKED: list[ExportStatus] = [*(still_running() for _ in range(6)), ready()]


def emptied() -> ExportStatus:
    """KSeF closed the export and the window held nothing to build a package from."""
    return ExportStatus(
        state=ExportState.EMPTY,
        parts=(),
        truncated=False,
        hwm_date=None,
        last_permanent_storage_date=None,
        invoice_count=0,
    )


def a_hung_export(
    store: SyncStore,
    *,
    started_at: datetime,
    reached: datetime = LAST_SEEN,
) -> None:
    store.save(
        SyncState(
            subject_roles={
                SubjectRole.BUYER: SubjectRoleState(reached=reached, attempted_at=started_at)
            },
            pending=(
                PendingExport(
                    reference=HUNG_REFERENCE,
                    subject_role=SubjectRole.BUYER,
                    started_at=started_at,
                    encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
                    state=ExportState.RUNNING,
                    covering_from=reached,
                ),
            ),
        )
    )


def a_pass_over_a_hung_export(
    store: SyncStore,
    naps: list[float],
    *,
    statuses: list[ExportStatus],
    started_at: datetime,
    reached: datetime = LAST_SEEN,
) -> tuple[ScriptedSession, SynchronisationReport]:
    a_hung_export(store, started_at=started_at, reached=reached)
    session = ScriptedSession(statuses=statuses, limits=allowances())
    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)
    return session, report


def test_an_export_still_being_built_says_since_when_rather_than_merely_that_it_is(
    store: SyncStore, naps: list[float]
) -> None:
    # „Nadal buduje" read the same after one minute and after thirteen hours, so
    # a stuck subject type looked healthy in the report and only the state file
    # on disk said otherwise.
    _, report = a_pass_over_a_hung_export(
        store, naps, statuses=[still_running()], started_at=NOON - timedelta(hours=13)
    )

    assert (NOON - timedelta(hours=13)).isoformat() in reported(report, SubjectRole.BUYER).message


def test_an_export_still_inside_the_ceiling_is_left_for_the_next_pass(
    store: SyncStore, naps: list[float]
) -> None:
    _, report = a_pass_over_a_hung_export(
        store, naps, statuses=[still_running()], started_at=NOON - ABANDONED_AFTER + HALF_AN_HOUR
    )

    assert (outcome(report, SubjectRole.BUYER), HUNG_REFERENCE in report.pending_exports) == (
        SyncOutcome.STILL_RUNNING,
        True,
    )


def test_an_export_older_than_the_ceiling_stops_blocking_its_subject_role(
    store: SyncStore, naps: list[float]
) -> None:
    # The whole of #190: the continuation point cannot move until the package
    # closes, so a package that never closes blocks the subject type for good.
    _, report = a_pass_over_a_hung_export(
        store,
        naps,
        statuses=HUNG_THEN_REASKED,
        started_at=NOON - ABANDONED_AFTER,
    )

    assert HUNG_REFERENCE not in report.pending_exports


def test_an_abandoned_export_asks_for_the_very_same_window_again(
    store: SyncStore, naps: list[float]
) -> None:
    # Nothing is skipped and nothing is asked for twice over: the point was
    # never moved past this window, so re-asking costs one export and no gap.
    session, _ = a_pass_over_a_hung_export(
        store,
        naps,
        statuses=HUNG_THEN_REASKED,
        started_at=NOON - ABANDONED_AFTER,
    )

    assert [window.date_from for window in buyer_windows(session)] == [LAST_SEEN]


def test_an_abandoned_export_says_how_long_it_had_been_building(
    store: SyncStore, naps: list[float]
) -> None:
    _, report = a_pass_over_a_hung_export(
        store,
        naps,
        statuses=HUNG_THEN_REASKED,
        started_at=NOON - ABANDONED_AFTER,
    )

    assert "porzucony" in reported(report, SubjectRole.BUYER).message


def test_an_empty_window_ksef_has_closed_stops_being_waited_on(
    store: SyncStore, naps: list[float]
) -> None:
    # Hypothesis 2 of #190: the export is over, there is simply no package,
    # and reading that as „still building" is what left the direction stuck.
    _, report = a_pass_over_a_hung_export(store, naps, statuses=[emptied()], started_at=STARTED_AT)

    assert (outcome(report, SubjectRole.BUYER), HUNG_REFERENCE in report.pending_exports) == (
        SyncOutcome.EMPTY_WINDOW,
        False,
    )


def test_an_empty_window_moves_the_point_to_the_end_of_the_window_it_covered(
    store: SyncStore, naps: list[float]
) -> None:
    # A closed export answers for the whole window it was given, so the point
    # may pass it although KSeF named no continuation marker.
    _, report = a_pass_over_a_hung_export(store, naps, statuses=[emptied()], started_at=STARTED_AT)

    assert reported(report, SubjectRole.BUYER).reached == STARTED_AT


def test_an_empty_window_that_was_clipped_moves_the_point_only_as_far_as_it_reached(
    store: SyncStore, naps: list[float]
) -> None:
    # A subject type far enough behind is handed a clipped window (D-031 §4);
    # moving it to the moment of the request would skip everything between.
    _, report = a_pass_over_a_hung_export(
        store, naps, statuses=[emptied()], started_at=STARTED_AT, reached=LONG_DORMANT
    )

    assert reported(report, SubjectRole.BUYER).reached == LONG_DORMANT + MAX_QUERY_WINDOW


def test_an_empty_window_is_kept_under_its_reference_rather_than_dropped(
    store: SyncStore, naps: list[float]
) -> None:
    a_pass_over_a_hung_export(store, naps, statuses=[emptied()], started_at=STARTED_AT)

    assert HUNG_REFERENCE in [export.reference for export in store.load().settled]


def test_an_empty_window_does_not_keep_its_key(store: SyncStore, naps: list[float]) -> None:
    a_pass_over_a_hung_export(store, naps, statuses=[emptied()], started_at=STARTED_AT)

    assert [export.encryption for export in store.load().settled] == [None, None]


def test_an_exhausted_export_allowance_stops_the_pass_rather_than_the_server(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready()], limits=allowances(exports_per_hour=1))

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert (
        outcome(report, SubjectRole.SELLER),
        outcome(report, SubjectRole.BUYER),
    ) == (SyncOutcome.ARCHIVED, SyncOutcome.BUDGET_SPENT)


def test_an_exhausted_export_allowance_spends_no_further_export(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready()], limits=allowances(exports_per_hour=1))

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert len(session.started) == 1


def test_an_exhausted_status_allowance_leaves_the_package_recorded(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready()], limits=allowances(statuses_per_hour=0))

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.SELLER) is SyncOutcome.BUDGET_SPENT


def test_an_exhausted_status_allowance_still_keeps_the_key(
    store: SyncStore, naps: list[float]
) -> None:
    # The export was spent; losing the key here would waste it outright (D-033).
    session = ScriptedSession(statuses=[ready()], limits=allowances(statuses_per_hour=0))

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert store.load().pending[0].encryption.key == KEY


def test_an_allowance_kseF_does_not_report_is_not_invented(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready()], limits=allowances(exports_per_hour=None))

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.BUYER) is SyncOutcome.ARCHIVED


def test_downloading_the_parts_spends_no_hourly_allowance(
    store: SyncStore, naps: list[float]
) -> None:
    # The part URLs are presigned links to external storage, reached without a
    # KSeF credential; the `invoice_download` family is the sixty-four-per-hour
    # ceiling on fetching one invoice by its number (D-031 §1, ADR-104). An
    # allowance of zero there must not stop a package from being archived.
    session = ScriptedSession(statuses=[ready()], limits=allowances(downloads_per_hour=0))

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, SubjectRole.SELLER) is SyncOutcome.ARCHIVED


def test_a_truncated_package_without_its_marker_advances_nowhere() -> None:
    point = ContinuationPoint(subject_role=SubjectRole.BUYER, reached=LAST_SEEN)

    assert advance(point, status=ready(truncated=True, last_permanent_storage_date=None)) is None


def test_a_truncated_package_ignores_a_missing_high_water_mark() -> None:
    point = ContinuationPoint(subject_role=SubjectRole.BUYER, reached=LAST_SEEN)

    moved = advance(point, status=ready(truncated=True, hwm_date=None))

    assert moved is not None and moved.reached == LAST_SEEN


def test_a_complete_package_ignores_a_missing_last_invoice_timestamp() -> None:
    point = ContinuationPoint(subject_role=SubjectRole.BUYER, reached=LAST_SEEN)

    moved = advance(point, status=ready(last_permanent_storage_date=None))

    assert moved is not None and moved.reached == HWM


def test_the_default_clock_answers_in_utc() -> None:
    assert now_utc().tzinfo is UTC
