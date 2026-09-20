"""What has to survive a restart, and what it costs when it does not.

Losing this record costs a full resynchronisation against twenty exports an
hour, so the assertions here are about durability and placement — the data
directory rather than the cache one, one subject per directory, and a write
that a crash cannot half-finish.
"""

import json
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ksef_mcp import storage
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    ContinuationPoint,
    ExportEncryption,
    ExportPart,
    ExportState,
    SubjectRole,
)
from ksef_mcp.storage import WriteExclusivityUnavailable
from ksef_mcp.sync_store import (
    MINIMUM_INTERVAL,
    SCHEMA_VERSION,
    SETTLED_JOURNAL_LIMIT,
    ExportKeyDiscarded,
    PendingExport,
    SubjectRoleState,
    SyncState,
    SyncStateUnreadable,
    SyncStore,
    in_night_window,
)
from tests.conftest import in_another_thread

NIP = "1234567890"

REACHED = datetime(2026, 9, 10, tzinfo=UTC)

NOON = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

MIDNIGHT = datetime(2026, 9, 12, 2, 0, tzinfo=UTC)

STARTED = datetime(2026, 9, 11, 8, 30, tzinfo=UTC)

# Where the subject type stood when the export was asked for — the only way back
# if the package turns out to be unreachable (GH-93).
COVERING_FROM = datetime(2026, 6, 14, tzinfo=UTC)


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
        subject_role=SubjectRole.BUYER,
        started_at=STARTED,
        encryption=ExportEncryption(key=b"k" * 32, initialisation_vector=b"i" * 16),
        state=ExportState.READY,
        parts=(part,),
        invoice_count=12,
    )


@pytest.fixture
def populated(queued: PendingExport) -> SyncState:
    return SyncState(
        subject_roles={
            SubjectRole.BUYER: SubjectRoleState(reached=REACHED, attempted_at=STARTED),
            SubjectRole.SELLER: SubjectRoleState(reached=REACHED),
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
    assert reloaded.subject_roles[SubjectRole.BUYER] == SubjectRoleState(
        reached=REACHED, attempted_at=STARTED
    )


def test_a_never_attempted_subject_role_reads_back_without_an_attempt(reloaded: SyncState) -> None:
    assert reloaded.subject_roles[SubjectRole.SELLER].attempted_at is None


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

    with pytest.raises(SyncStateUnreadable, match="pomija faktury"):
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

    assert sorted(path.name for path in store.directory.iterdir()) == [
        storage.LOCK_FILE,
        "synchronisation.json",
    ]


def test_a_second_write_replaces_the_first(store: SyncStore, populated: SyncState) -> None:
    store.save(populated)
    store.save(SyncState(subject_roles={SubjectRole.BUYER: SubjectRoleState(reached=STARTED)}))

    assert store.load().pending == ()


def test_a_queued_export_is_found_by_its_subject_role(
    populated: SyncState, queued: PendingExport
) -> None:
    assert populated.pending_for(SubjectRole.BUYER) == queued


def test_a_subject_role_without_a_queued_export_finds_none(populated: SyncState) -> None:
    assert populated.pending_for(SubjectRole.SELLER) is None


def test_re_recording_an_export_replaces_it_rather_than_duplicating(
    populated: SyncState, queued: PendingExport
) -> None:
    advanced = populated.with_pending(
        PendingExport(
            reference=queued.reference,
            subject_role=queued.subject_role,
            started_at=queued.started_at,
            encryption=queued.encryption,
            state=ExportState.FAILED,
        )
    )

    assert [export.state for export in advanced.pending] == [ExportState.FAILED]


def test_recording_a_subject_role_leaves_the_others_alone(populated: SyncState) -> None:
    advanced = populated.with_subject_role(SubjectRole.SELLER, SubjectRoleState(reached=STARTED))

    assert advanced.subject_roles[SubjectRole.BUYER].reached == REACHED


def test_a_subject_role_state_hands_out_the_ports_continuation_point() -> None:
    stored = SubjectRoleState(reached=REACHED, attempted_at=STARTED)

    assert stored.continuation_point(subject_role=SubjectRole.BUYER) == ContinuationPoint(
        subject_role=SubjectRole.BUYER, reached=REACHED
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
    assert populated.without_export(reference="EXP-1").subject_roles == populated.subject_roles


def test_a_dropped_export_is_gone_from_the_record(populated: SyncState) -> None:
    # Archiving drops the record, and the key goes with it (D-033).
    assert populated.without_export(reference="EXP-1").pending == ()


def test_dropping_an_export_nobody_recorded_changes_nothing(populated: SyncState) -> None:
    assert populated.without_export(reference="EXP-innego") == populated


def spent(queued: PendingExport) -> PendingExport:
    return PendingExport(
        reference=queued.reference,
        subject_role=queued.subject_role,
        started_at=queued.started_at,
        encryption=None,
        state=ExportState.FAILED,
    )


def test_the_window_an_export_asked_for_survives_the_round_trip(
    store: SyncStore, queued: PendingExport
) -> None:
    store.save(SyncState(pending=(replace(queued, covering_from=COVERING_FROM),)))

    assert store.load().pending[0].covering_from == COVERING_FROM


def test_a_record_written_before_the_window_start_existed_still_loads(
    store: SyncStore, populated: SyncState
) -> None:
    # The field is additive both ways: an older reader ignores the key
    # and an older document simply does not carry it, so neither needs a schema
    # bump (GH-93). Reading such a document must not fail.
    path = store.save(populated)
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["pending_exports"][0]["covering_from"]
    path.write_text(json.dumps(document), encoding="utf-8")

    assert store.load().pending[0].covering_from is None


def test_an_export_without_a_key_survives_the_round_trip(
    store: SyncStore, queued: PendingExport
) -> None:
    store.save(SyncState(settled=(spent(queued),)))

    assert store.load().settled[0].encryption is None


def test_an_empty_window_ends_the_export_without_calling_it_a_failure(
    queued: PendingExport,
) -> None:
    # Nothing went wrong — KSeF simply had no invoice to put in the package
    # (GH-190). Recording it as `FAILED` would tell the next reader the opposite.
    assert queued.emptied().state is ExportState.EMPTY


def test_an_empty_window_lets_go_of_the_key_it_will_never_open_anything_with(
    queued: PendingExport,
) -> None:
    assert queued.emptied().encryption is None


def test_an_empty_window_is_journalled_rather_than_queued(
    store: SyncStore, queued: PendingExport
) -> None:
    # The queue is what blocks a subject type. An export KSeF has closed must
    # leave it, whichever of the two endings it reached.
    store.save(SyncState(pending=(queued.emptied(),)))
    reloaded = store.load()

    assert (reloaded.pending, [export.state for export in reloaded.settled]) == (
        (),
        [ExportState.EMPTY],
    )


def test_a_finished_export_is_journalled_rather_than_queued(queued: PendingExport) -> None:
    # The queue answers "what is this subject type still waiting on". A refused
    # export can never be waited on again, so it has no business answering it.
    state = SyncState(pending=(queued,)).with_settled(spent(queued))

    assert (state.pending, [export.state for export in state.settled]) == (
        (),
        [ExportState.FAILED],
    )


def test_a_journalled_export_stops_blocking_its_subject_role(queued: PendingExport) -> None:
    # The whole of GH-94 in one assertion: with the refusal in the queue this
    # returned it forever, and every pass ordered a package nobody collected.
    state = SyncState(pending=(queued,)).with_settled(spent(queued))

    assert state.pending_for(SubjectRole.BUYER) is None


def test_the_journal_keeps_only_the_most_recent_refusals(queued: PendingExport) -> None:
    # The document is read and written whole on every pass, so the journal is
    # bounded on purpose — one refused export must not grow the file forever.
    state = SyncState()
    for ordinal in range(SETTLED_JOURNAL_LIMIT + 5):
        state = state.with_settled(replace(spent(queued), reference=f"EXP-{ordinal}"))

    assert (len(state.settled), state.settled[0].reference) == (SETTLED_JOURNAL_LIMIT, "EXP-5")


def test_the_journal_survives_the_round_trip(store: SyncStore, queued: PendingExport) -> None:
    store.save(SyncState(settled=(spent(queued),)))

    assert [export.reference for export in store.load().settled] == ["EXP-1"]


def test_a_record_written_before_the_journal_existed_still_loads(
    store: SyncStore, populated: SyncState
) -> None:
    # Additive both ways, exactly like `covering_from`: an older reader
    # ignores the key and an older document does not carry it, so neither needs
    # a schema bump.
    path = store.save(populated)
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["settled_exports"]
    path.write_text(json.dumps(document), encoding="utf-8")

    assert store.load().settled == ()


def test_a_refusal_left_in_the_queue_by_an_older_build_moves_to_the_journal(
    store: SyncStore, queued: PendingExport
) -> None:
    # The upgrade path. A file written before GH-94 holds the deadlock itself;
    # reading it has to undo that, or the fix reaches nobody who already hit it.
    store.save(SyncState(pending=(spent(queued),)))
    reloaded = store.load()

    assert (reloaded.pending, [export.reference for export in reloaded.settled]) == ((), ["EXP-1"])


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
    stored = SubjectRoleState(reached=REACHED, attempted_at=REACHED + timedelta(minutes=15))

    assert stored.attempted_at == datetime(2026, 9, 10, 0, 15, tzinfo=UTC)


def test_an_update_reads_the_record_it_is_about_to_change(
    store: SyncStore, populated: SyncState
) -> None:
    store.save(populated)

    updated = store.updating(lambda state: state.without_export(reference="EXP-1"))

    assert updated.pending == ()


def test_an_update_is_on_disk_by_the_time_it_is_returned(
    store: SyncStore, populated: SyncState
) -> None:
    store.save(populated)

    store.updating(lambda state: state.without_export(reference="EXP-1"))

    assert store.load().pending == ()


def test_a_second_writer_is_refused_rather_than_left_to_lose_its_changes(
    store: SyncStore, populated: SyncState
) -> None:
    """GH-101: two clients on one subject is an ordinary setup, not an exotic one."""
    with store.exclusively():
        with pytest.raises(WriteExclusivityUnavailable):
            in_another_thread(lambda: store.save(populated))


def test_the_record_a_refused_writer_never_wrote_stays_as_the_holder_left_it(
    store: SyncStore, populated: SyncState
) -> None:
    store.save(populated)

    with store.exclusively():
        with pytest.raises(WriteExclusivityUnavailable):
            in_another_thread(lambda: store.save(SyncState()))

    assert store.load().pending == populated.pending


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(0, True), (3, True), (5, True), (6, False), (12, False), (23, False)],
)
def test_the_night_window_is_measured_in_utc(hour: int, expected: bool) -> None:
    assert in_night_window(datetime(2026, 9, 12, hour, tzinfo=UTC)) is expected


def test_a_subject_role_recorded_without_an_attempt_is_due() -> None:
    state = SubjectRoleState(reached=REACHED)

    assert state.is_due(subject_role=SubjectRole.BUYER, moment=NOON) is True


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(timedelta(minutes=14), False), (MINIMUM_INTERVAL, True), (timedelta(hours=1), True)],
)
def test_the_state_holds_the_interval_floor_open(elapsed: timedelta, expected: bool) -> None:
    state = SubjectRoleState(reached=REACHED, attempted_at=NOON - elapsed)

    assert state.is_due(subject_role=SubjectRole.BUYER, moment=NOON) is expected


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(timedelta(hours=23), False), (timedelta(days=1), True)],
)
def test_the_state_lets_a_rare_subject_role_through_once_a_day(
    elapsed: timedelta, expected: bool
) -> None:
    state = SubjectRoleState(reached=REACHED, attempted_at=MIDNIGHT - elapsed)

    assert state.is_due(subject_role=SubjectRole.THIRD_SUBJECT, moment=MIDNIGHT) is expected


def test_a_rare_subject_role_waits_for_the_night_window_even_when_never_attempted() -> None:
    state = SubjectRoleState(reached=REACHED)

    assert state.is_due(subject_role=SubjectRole.THIRD_SUBJECT, moment=NOON) is False
