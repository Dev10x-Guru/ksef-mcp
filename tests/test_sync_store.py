"""What has to survive a restart, and what it costs when it does not.

Losing this record costs a full resynchronisation against twenty exports an
hour, so the assertions here are about durability and placement — the data
directory rather than the cache one, one subject per directory, and a write
that a crash cannot half-finish.
"""

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    ContinuationPoint,
    ExportEncryption,
    ExportPart,
    ExportState,
    InvoiceDirection,
)
from ksef_mcp.sync_store import (
    SCHEMA_VERSION,
    DirectionState,
    ExportKeyDiscarded,
    PendingExport,
    SyncState,
    SyncStateUnreadable,
    SyncStore,
)

NIP = "1234567890"

REACHED = datetime(2026, 9, 10, tzinfo=UTC)

STARTED = datetime(2026, 9, 11, 8, 30, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SyncStore:
    return SyncStore(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)


@pytest.fixture
def part() -> ExportPart:
    return ExportPart(
        ordinal=1,
        name="package_part_1.zip.aes",
        method="GET",
        url="https://storage.example/part/1",
        size_bytes=1024,
        content_hash="c3BsaXQ=",
        encrypted_size_bytes=1040,
        encrypted_content_hash="ZW5jcnlwdGVk",
    )


@pytest.fixture
def queued(part: ExportPart) -> PendingExport:
    return PendingExport(
        reference="EXP-1",
        direction=InvoiceDirection.BUYER,
        started_at=STARTED,
        encryption=ExportEncryption(key=b"k" * 32, initialisation_vector=b"i" * 16),
        state=ExportState.READY,
        parts=(part,),
        invoice_count=12,
    )


@pytest.fixture
def populated(queued: PendingExport) -> SyncState:
    return SyncState(
        directions={
            InvoiceDirection.BUYER: DirectionState(reached=REACHED, attempted_at=STARTED),
            InvoiceDirection.SELLER: DirectionState(reached=REACHED),
        },
        pending=(queued,),
    )


@pytest.fixture
def reloaded(store: SyncStore, populated: SyncState) -> SyncState:
    store.save(populated)
    return store.load()


@pytest.fixture
def written_document(store: SyncStore, populated: SyncState) -> dict[str, object]:
    path = store.save(populated)
    return json.loads(path.read_text(encoding="utf-8"))


def test_an_absent_record_reads_as_a_first_run(store: SyncStore) -> None:
    assert store.load() == SyncState()


def test_the_record_lands_in_its_own_subject_directory(store: SyncStore, tmp_path: Path) -> None:
    assert store.path == tmp_path / "subjects" / NIP / "test" / "synchronisation.json"


def test_the_default_root_is_the_data_directory_not_the_cache_one() -> None:
    # The cache convention is "safe to delete at any moment" and a continuation
    # point is not (D-032); the tail of the path is what says which root it is.
    located = SyncStore(nip=NIP, environment=KsefEnvironment.TEST).path

    assert located.parts[-4:] == ("subjects", NIP, "test", "synchronisation.json")


def test_the_environment_separates_two_records_of_one_subject(tmp_path: Path) -> None:
    production = SyncStore(nip=NIP, environment=KsefEnvironment.PRODUCTION, root=tmp_path)
    test = SyncStore(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)

    assert production.path != test.path


def test_continuation_points_survive_the_round_trip(reloaded: SyncState) -> None:
    assert reloaded.directions[InvoiceDirection.BUYER] == DirectionState(
        reached=REACHED, attempted_at=STARTED
    )


def test_a_never_attempted_direction_reads_back_without_an_attempt(reloaded: SyncState) -> None:
    assert reloaded.directions[InvoiceDirection.SELLER].attempted_at is None


def test_the_queued_export_survives_the_round_trip(
    reloaded: SyncState, queued: PendingExport
) -> None:
    assert reloaded.pending == (queued,)


def test_the_export_key_survives_the_round_trip(reloaded: SyncState) -> None:
    # Without the key the package is unreadable and the export is spent for
    # nothing — one of twenty an hour (D-033).
    assert reloaded.pending[0].encryption.key == b"k" * 32


def test_the_initialisation_vector_survives_the_round_trip(reloaded: SyncState) -> None:
    assert reloaded.pending[0].encryption.initialisation_vector == b"i" * 16


def test_the_part_urls_survive_the_round_trip(reloaded: SyncState, part: ExportPart) -> None:
    assert reloaded.pending[0].parts == (part,)


def test_the_document_names_its_schema(written_document: dict[str, object]) -> None:
    assert written_document["schema_version"] == SCHEMA_VERSION


def test_the_document_names_the_subject_it_belongs_to(
    written_document: dict[str, object],
) -> None:
    assert (written_document["nip"], written_document["environment"]) == (NIP, "test")


def test_a_newer_schema_is_refused_rather_than_guessed_at(store: SyncStore) -> None:
    store.directory.mkdir(parents=True)
    store.path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION + 1,
                "continuation_points": {},
                "pending_exports": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SyncStateUnreadable, match="skips invoices"):
        store.load()


def test_the_record_is_readable_only_by_its_owner(store: SyncStore, populated: SyncState) -> None:
    path = store.save(populated)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_subject_directory_is_readable_only_by_its_owner(
    store: SyncStore, populated: SyncState
) -> None:
    store.save(populated)

    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700


def test_no_staging_file_outlives_the_write(store: SyncStore, populated: SyncState) -> None:
    store.save(populated)

    assert sorted(path.name for path in store.directory.iterdir()) == ["synchronisation.json"]


def test_a_second_write_replaces_the_first(store: SyncStore, populated: SyncState) -> None:
    store.save(populated)
    store.save(SyncState(directions={InvoiceDirection.BUYER: DirectionState(reached=STARTED)}))

    assert store.load().pending == ()


def test_a_queued_export_is_found_by_its_subject_type(
    populated: SyncState, queued: PendingExport
) -> None:
    assert populated.pending_for(InvoiceDirection.BUYER) == queued


def test_a_subject_type_without_a_queued_export_finds_none(populated: SyncState) -> None:
    assert populated.pending_for(InvoiceDirection.SELLER) is None


def test_re_recording_an_export_replaces_it_rather_than_duplicating(
    populated: SyncState, queued: PendingExport
) -> None:
    advanced = populated.with_pending(
        PendingExport(
            reference=queued.reference,
            direction=queued.direction,
            started_at=queued.started_at,
            encryption=queued.encryption,
            state=ExportState.FAILED,
        )
    )

    assert [export.state for export in advanced.pending] == [ExportState.FAILED]


def test_recording_a_direction_leaves_the_others_alone(populated: SyncState) -> None:
    advanced = populated.with_direction(InvoiceDirection.SELLER, DirectionState(reached=STARTED))

    assert advanced.directions[InvoiceDirection.BUYER].reached == REACHED


def test_a_direction_state_hands_out_the_ports_continuation_point() -> None:
    stored = DirectionState(reached=REACHED, attempted_at=STARTED)

    assert stored.continuation_point(direction=InvoiceDirection.BUYER) == ContinuationPoint(
        direction=InvoiceDirection.BUYER, reached=REACHED
    )


def test_a_queued_export_hands_back_the_handle_the_port_needs(queued: PendingExport) -> None:
    # This is the whole point of persisting it: the next run, or the next
    # process, calls `fetch_part(handle=…, part=…)` with exactly this.
    assert queued.handle.reference == "EXP-1"


def test_the_handle_carries_the_key_the_package_was_minted_with(queued: PendingExport) -> None:
    assert queued.handle.encryption.key == b"k" * 32


def test_dropping_an_export_leaves_the_continuation_points_alone(
    populated: SyncState,
) -> None:
    assert populated.without_export(reference="EXP-1").directions == populated.directions


def test_a_dropped_export_is_gone_from_the_record(populated: SyncState) -> None:
    # Archiving drops the record, and the key goes with it (D-033).
    assert populated.without_export(reference="EXP-1").pending == ()


def test_dropping_an_export_nobody_recorded_changes_nothing(populated: SyncState) -> None:
    assert populated.without_export(reference="EXP-innego") == populated


def spent(queued: PendingExport) -> PendingExport:
    return PendingExport(
        reference=queued.reference,
        direction=queued.direction,
        started_at=queued.started_at,
        encryption=None,
        state=ExportState.FAILED,
    )


def test_an_export_without_a_key_survives_the_round_trip(
    store: SyncStore, queued: PendingExport
) -> None:
    store.save(SyncState(pending=(spent(queued),)))

    assert store.load().pending[0].encryption is None


def test_an_export_without_a_key_still_writes_the_field(
    store: SyncStore, queued: PendingExport
) -> None:
    # `null` rather than an absent field: a record missing the key reads as
    # deliberate, not as a document somebody truncated.
    store.save(SyncState(pending=(spent(queued),)))

    assert (
        json.loads(store.path.read_text(encoding="utf-8"))["pending_exports"][0]
        | {
            "encryption_key": None,
            "initialisation_vector": None,
        }
        == json.loads(store.path.read_text(encoding="utf-8"))["pending_exports"][0]
    )


def test_asking_a_finished_export_for_its_handle_says_the_key_is_gone(
    queued: PendingExport,
) -> None:
    with pytest.raises(ExportKeyDiscarded, match="carries no key"):
        spent(queued).handle  # noqa: B018


def test_an_attempt_is_recorded_with_its_timestamp() -> None:
    stored = DirectionState(reached=REACHED, attempted_at=REACHED + timedelta(minutes=15))

    assert stored.attempted_at == datetime(2026, 9, 10, 0, 15, tzinfo=UTC)
