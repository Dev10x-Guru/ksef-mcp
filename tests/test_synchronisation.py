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

from ksef_mcp.archive import INDEX_FILE, InvoiceArchive
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    ContinuationPoint,
    ExportEncryption,
    ExportHandle,
    ExportPart,
    ExportState,
    ExportStatus,
    InvoiceDirection,
    KsefLimits,
    KsefNumber,
    KsefUnreachable,
    MetadataPage,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
)
from ksef_mcp.sync_store import DirectionState, PendingExport, SyncState, SyncStore
from ksef_mcp.synchronisation import (
    INITIAL_LOOKBACK,
    MINIMUM_INTERVAL,
    DirectionReport,
    SynchronisationReport,
    Synchroniser,
    SyncOutcome,
    advance,
    in_night_window,
    is_due,
    now_utc,
)
from synthetic import (
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
    statuses_per_hour: int | None = 200,
    downloads_per_hour: int | None = 64,
) -> KsefLimits:
    return KsefLimits(
        rates=RateLimits(
            metadata_queries=OperationLimit(per_second=8, per_minute=16, per_hour=20),
            exports=OperationLimit(per_second=2, per_minute=4, per_hour=exports_per_hour),
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
    started: list[tuple[InvoiceDirection, Period]] = field(default_factory=list)
    polled: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)

    def read_limits(self) -> KsefLimits:
        return self.limits

    def query_metadata(self, *, period: Period, direction: InvoiceDirection) -> MetadataPage:
        raise AssertionError("Synchronizacja nie odpytuje metadanych — idzie przez eksport.")

    def start_export(self, *, period: Period, direction: InvoiceDirection) -> ExportHandle:
        self.started.append((direction, period))
        return ExportHandle(
            reference=f"EXP-{len(self.started)}",
            encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
        )

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        self.polled.append(handle.reference)
        return self.statuses[min(len(self.polled), len(self.statuses)) - 1]

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        self.fetched.append(handle.reference)
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


def outcome(report: SynchronisationReport, direction: InvoiceDirection) -> SyncOutcome:
    return next(one.outcome for one in report.directions if one.direction == direction)


def reported(report: SynchronisationReport, direction: InvoiceDirection) -> DirectionReport:
    return next(one for one in report.directions if one.direction == direction)


def test_every_subject_type_is_reported(first_pass: SynchronisationReport) -> None:
    assert [str(one.direction) for one in first_pass.directions] == [
        "seller",
        "buyer",
        "third_subject",
        "authorized_subject",
    ]


def test_the_frequent_subject_types_are_archived_at_noon(
    first_pass: SynchronisationReport,
) -> None:
    assert (
        outcome(first_pass, InvoiceDirection.SELLER),
        outcome(first_pass, InvoiceDirection.BUYER),
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
    assert reported(first_pass, InvoiceDirection.SELLER).archive_directory == str(
        archive.invoice_directory
    )


def test_the_report_names_the_numbers_it_archived(first_pass: SynchronisationReport) -> None:
    assert reported(first_pass, InvoiceDirection.SELLER).archived == (
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    )


def test_the_second_subject_type_recognises_invoices_the_first_already_stored(
    first_pass: SynchronisationReport,
) -> None:
    # The same invoice reaches a company as seller and as buyer; deduplication
    # by KSeF number is what stops it being written twice (D-005).
    assert reported(first_pass, InvoiceDirection.BUYER).already_held == (
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

    assert reported(later, InvoiceDirection.SELLER).archived == ()


def test_a_second_call_reports_the_numbers_as_already_held(
    first_pass: SynchronisationReport,
    session: ScriptedSession,
    store: SyncStore,
    naps: list[float],
) -> None:
    later = a_synchroniser(
        session=session, store=store, naps=naps, moment=NOON + MINIMUM_INTERVAL
    ).run(nip=NIP, token=TOKEN)

    assert reported(later, InvoiceDirection.SELLER).already_held == (
        str(synthetic_number(1)),
        str(synthetic_number(2)),
    )


def test_the_invoice_body_never_reaches_the_report(first_pass: SynchronisationReport) -> None:
    # FA(2)/FA(3) XML carries the counterparty's personal data, so the pass
    # answers with paths and numbers and nothing else (D-011).
    assert "Faktura" not in "".join(one.detail for one in first_pass.directions)


@pytest.mark.parametrize(
    "direction",
    [InvoiceDirection.THIRD_SUBJECT, InvoiceDirection.AUTHORIZED_SUBJECT],
)
def test_the_rare_subject_types_wait_for_the_night_window(
    first_pass: SynchronisationReport, direction: InvoiceDirection
) -> None:
    # Keeping them off the daytime rotation is what leaves the frequent two
    # their share of twenty exports an hour (D-031 §5).
    assert outcome(first_pass, direction) is SyncOutcome.NOT_DUE


def test_the_rare_subject_types_are_exported_in_the_night_window(
    session: ScriptedSession, store: SyncStore, naps: list[float]
) -> None:
    report = a_synchroniser(session=session, store=store, naps=naps, moment=MIDNIGHT).run(
        nip=NIP, token=TOKEN
    )

    assert outcome(report, InvoiceDirection.THIRD_SUBJECT) is SyncOutcome.ARCHIVED


def test_the_export_window_is_pinned_to_permanent_storage(
    first_pass: SynchronisationReport, session: ScriptedSession
) -> None:
    # Any other date type makes incremental fetching unpredictable (D-031 §2).
    assert {period.date_type for _, period in session.started} == {"permanent_storage"}


def test_the_export_window_has_no_upper_bound(
    first_pass: SynchronisationReport, session: ScriptedSession
) -> None:
    assert {period.date_to for _, period in session.started} == {None}


def test_a_first_run_starts_one_lookback_back(
    first_pass: SynchronisationReport, session: ScriptedSession
) -> None:
    assert session.started[0][1].date_from == NOON - INITIAL_LOOKBACK


def test_a_completed_package_moves_the_point_to_the_high_water_mark(
    first_pass: SynchronisationReport,
) -> None:
    assert reported(first_pass, InvoiceDirection.BUYER).reached == HWM


def test_a_completed_package_reports_what_it_carries(
    first_pass: SynchronisationReport,
) -> None:
    assert (
        reported(first_pass, InvoiceDirection.BUYER).invoice_count,
        reported(first_pass, InvoiceDirection.BUYER).part_count,
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

    assert outcome(later, InvoiceDirection.BUYER) is SyncOutcome.ARCHIVED


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

    assert reported(report, InvoiceDirection.BUYER).reached == LAST_SEEN


def test_a_package_still_being_built_is_left_for_the_next_pass(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[still_running()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, InvoiceDirection.BUYER) is SyncOutcome.STILL_RUNNING


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

    assert outcome(report, InvoiceDirection.SELLER) is SyncOutcome.ARCHIVED


def left_on_disk(store: SyncStore, *, state: ExportState) -> None:
    """Put a package from an earlier pass in the record, the way a crash would."""
    store.save(
        SyncState(
            directions={InvoiceDirection.BUYER: DirectionState(reached=LAST_SEEN)},
            pending=(
                PendingExport(
                    reference="EXP-OLD",
                    direction=InvoiceDirection.BUYER,
                    started_at=NOON - timedelta(hours=2),
                    encryption=ExportEncryption(key=KEY, initialisation_vector=IV),
                    state=state,
                    parts=(a_part(),),
                    invoice_count=2,
                ),
            ),
        )
    )


def test_a_queued_package_is_resumed_instead_of_being_asked_for_again(
    store: SyncStore, naps: list[float]
) -> None:
    left_on_disk(store, state=ExportState.RUNNING)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert (outcome(report, InvoiceDirection.BUYER), "EXP-OLD" in session.polled) == (
        SyncOutcome.ARCHIVED,
        True,
    )


def test_a_queued_package_costs_no_second_export(store: SyncStore, naps: list[float]) -> None:
    left_on_disk(store, state=ExportState.RUNNING)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert [direction for direction, _ in session.started] == [InvoiceDirection.SELLER]


def test_a_package_fetched_but_never_stored_is_archived_on_the_next_pass(
    store: SyncStore, naps: list[float]
) -> None:
    left_on_disk(store, state=ExportState.READY)
    session = ScriptedSession(statuses=[ready()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, InvoiceDirection.BUYER) is SyncOutcome.ARCHIVED


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
        [direction for direction, _ in session.started],
        "EXP-OLD" in session.polled,
    ) == ([InvoiceDirection.SELLER], False)


def test_a_refused_export_is_recorded_as_the_failure_it_was(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, InvoiceDirection.BUYER) is SyncOutcome.FAILED


def test_a_refused_export_leaves_the_point_where_it_was(
    store: SyncStore, naps: list[float]
) -> None:
    # The same window is asked for again next time — one export spent, nothing
    # skipped.
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert store.load().directions[InvoiceDirection.BUYER].reached == NOON - INITIAL_LOOKBACK


def test_a_refused_export_is_kept_under_its_reference(store: SyncStore, naps: list[float]) -> None:
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert [export.state for export in store.load().pending] == [
        ExportState.FAILED,
        ExportState.FAILED,
    ]


def test_a_refused_export_does_not_keep_its_key(store: SyncStore, naps: list[float]) -> None:
    # The export is over, so the key opens nothing. Leaving it on disk would be
    # exactly the entry nobody ever cleans up that D-033 refuses.
    session = ScriptedSession(statuses=[failed()], limits=allowances())

    a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert [export.encryption for export in store.load().pending] == [None, None]


def test_a_ready_package_without_a_continuation_marker_does_not_move_the_point(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready(hwm_date=None)], limits=allowances())

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, InvoiceDirection.BUYER) is SyncOutcome.INCONCLUSIVE


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
    assert outcome(after_a_failed_download, InvoiceDirection.BUYER) is SyncOutcome.NOT_ARCHIVED


def test_a_failed_download_does_not_roll_the_continuation_point_back(
    after_a_failed_download: SynchronisationReport, store: SyncStore
) -> None:
    # The point moves on what KSeF confirmed, never on whether this run managed
    # to write the files (ADR-103 §3) — the record left on disk is what says the
    # window is still owed.
    assert store.load().directions[InvoiceDirection.BUYER].reached == HWM


def test_a_failed_download_writes_no_invoice_at_all(
    after_a_failed_download: SynchronisationReport, archive: InvoiceArchive
) -> None:
    assert archive.invoice_directory.exists() is False


def test_a_failed_download_leaves_the_window_to_the_next_pass(
    after_a_failed_download: SynchronisationReport,
) -> None:
    assert after_a_failed_download.pending_exports == ("EXP-1", "EXP-2")


def test_an_exhausted_export_allowance_stops_the_pass_rather_than_the_server(
    store: SyncStore, naps: list[float]
) -> None:
    session = ScriptedSession(statuses=[ready()], limits=allowances(exports_per_hour=1))

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert (
        outcome(report, InvoiceDirection.SELLER),
        outcome(report, InvoiceDirection.BUYER),
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

    assert outcome(report, InvoiceDirection.SELLER) is SyncOutcome.BUDGET_SPENT


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

    assert outcome(report, InvoiceDirection.BUYER) is SyncOutcome.ARCHIVED


def test_downloading_the_parts_spends_no_hourly_allowance(
    store: SyncStore, naps: list[float]
) -> None:
    # The part URLs are presigned links to external storage, reached without a
    # KSeF credential; the `invoice_download` family is the sixty-four-per-hour
    # ceiling on fetching one invoice by its number (D-031 §1, ADR-104). An
    # allowance of zero there must not stop a package from being archived.
    session = ScriptedSession(statuses=[ready()], limits=allowances(downloads_per_hour=0))

    report = a_synchroniser(session=session, store=store, naps=naps).run(nip=NIP, token=TOKEN)

    assert outcome(report, InvoiceDirection.SELLER) is SyncOutcome.ARCHIVED


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(0, True), (3, True), (5, True), (6, False), (12, False), (23, False)],
)
def test_the_night_window_is_measured_in_utc(hour: int, expected: bool) -> None:
    assert in_night_window(datetime(2026, 9, 12, hour, tzinfo=UTC)) is expected


def test_a_subject_type_never_attempted_is_due() -> None:
    assert is_due(direction=InvoiceDirection.BUYER, stored=None, moment=NOON) is True


def test_a_subject_type_recorded_without_an_attempt_is_due() -> None:
    assert (
        is_due(
            direction=InvoiceDirection.BUYER,
            stored=DirectionState(reached=HWM),
            moment=NOON,
        )
        is True
    )


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(timedelta(minutes=14), False), (MINIMUM_INTERVAL, True), (timedelta(hours=1), True)],
)
def test_the_interval_floor_holds_per_subject_type(elapsed: timedelta, expected: bool) -> None:
    assert (
        is_due(
            direction=InvoiceDirection.BUYER,
            stored=DirectionState(reached=HWM, attempted_at=NOON - elapsed),
            moment=NOON,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(timedelta(hours=23), False), (timedelta(days=1), True)],
)
def test_the_rare_subject_types_are_asked_once_a_day(elapsed: timedelta, expected: bool) -> None:
    assert (
        is_due(
            direction=InvoiceDirection.THIRD_SUBJECT,
            stored=DirectionState(reached=HWM, attempted_at=MIDNIGHT - elapsed),
            moment=MIDNIGHT,
        )
        is expected
    )


def test_a_truncated_package_without_its_marker_advances_nowhere() -> None:
    point = ContinuationPoint(direction=InvoiceDirection.BUYER, reached=LAST_SEEN)

    assert advance(point, status=ready(truncated=True, last_permanent_storage_date=None)) is None


def test_a_truncated_package_ignores_a_missing_high_water_mark() -> None:
    point = ContinuationPoint(direction=InvoiceDirection.BUYER, reached=LAST_SEEN)

    moved = advance(point, status=ready(truncated=True, hwm_date=None))

    assert moved is not None and moved.reached == LAST_SEEN


def test_a_complete_package_ignores_a_missing_last_invoice_timestamp() -> None:
    point = ContinuationPoint(direction=InvoiceDirection.BUYER, reached=LAST_SEEN)

    moved = advance(point, status=ready(last_permanent_storage_date=None))

    assert moved is not None and moved.reached == HWM


def test_the_default_clock_answers_in_utc() -> None:
    assert now_utc().tzinfo is UTC
