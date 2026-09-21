import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ksef_mcp.ksef_port.types import DateType, KsefEnvironment, Period
from ksef_mcp.storage.audit import (
    AUDIT_FILE,
    XML_FORMAT,
    AuditedOperation,
    AuditEntry,
    AuditTrail,
    AuditTrailUnreadable,
    Authorisation,
    AuthorisationBasis,
    Disclosure,
    window_criteria,
)
from ksef_mcp.storage.token_store import TokenSource
from tests.conftest import in_another_thread

NIP = "1234567890"

MOMENT = datetime(2026, 9, 14, 8, 30, tzinfo=UTC)

FIRST_NUMBER = "1234567890-20260901-0100AB12CD01-56"

SECOND_NUMBER = "1234567890-20260901-0100AB12CD02-56"


@pytest.fixture
def authorisation() -> Authorisation:
    return Authorisation(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        basis=AuthorisationBasis.KSEF_TOKEN,
        source=str(TokenSource.KEYRING),
    )


def written_entry(
    authorisation: Authorisation,
    *,
    disclosure: Disclosure = Disclosure.DISK,
    numbers: tuple[str, ...] = (FIRST_NUMBER,),
) -> AuditEntry:
    return AuditEntry(
        recorded_at=MOMENT,
        operation=AuditedOperation.SYNCHRONISATION,
        authorisation=authorisation,
        disclosure=disclosure,
        subject_role="buyer",
        criteria="export packages up to 2026-09-10T00:00:00+00:00",
        document_count=len(numbers),
        ksef_numbers=numbers,
        output_path="/dane/subjects/1234567890/test/invoices",
        formats=(XML_FORMAT,),
    )


@pytest.fixture
def trail(tmp_path: Path) -> AuditTrail:
    return AuditTrail(nip=NIP, environment=KsefEnvironment.TEST, root=tmp_path)


@pytest.fixture
def recorded(trail: AuditTrail, authorisation: Authorisation) -> Path:
    return trail.record((written_entry(authorisation),))


def first_line(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def test_the_trail_sits_beside_the_archive_it_describes(trail: AuditTrail, tmp_path: Path) -> None:
    # Per subject and per environment, exactly as the archive and the review
    # ledger are, so one office's clients never share a trail.
    assert trail.path == tmp_path / "subjects" / NIP / "test" / AUDIT_FILE


def test_the_trail_lives_in_the_data_root_by_default() -> None:
    # The cache root is a place a disk cleaner is entitled to empty (D-032), and
    # evidence that a cleaner may delete is not evidence.
    default = AuditTrail(nip=NIP, environment=KsefEnvironment.TEST)

    assert default.path.parts[-4:] == ("subjects", NIP, "test", AUDIT_FILE)


def test_a_recorded_read_names_the_moment_it_happened(recorded: Path) -> None:
    assert first_line(recorded)["recorded_at"] == MOMENT.isoformat()


def test_a_recorded_read_names_the_subject_it_acted_under(recorded: Path) -> None:
    assert (first_line(recorded)["nip"], first_line(recorded)["environment"]) == (NIP, "test")


def test_a_recorded_read_names_the_footing_and_never_the_secret(recorded: Path) -> None:
    assert first_line(recorded)["authorisation_basis"] == "ksef_token:keyring"


def test_no_token_value_reaches_the_file(recorded: Path) -> None:
    assert "tajny-token" not in recorded.read_text(encoding="utf-8")


def test_a_recorded_read_names_the_criteria_it_asked_under(recorded: Path) -> None:
    assert first_line(recorded)["criteria"].startswith("export packages up to")  # type: ignore[union-attr]


def test_a_recorded_read_counts_the_documents(recorded: Path) -> None:
    assert first_line(recorded)["document_count"] == 1


def test_a_recorded_read_names_the_numbers_it_touched(recorded: Path) -> None:
    assert first_line(recorded)["ksef_numbers"] == [FIRST_NUMBER]


def test_a_recorded_read_names_the_file_it_wrote_and_in_what_format(recorded: Path) -> None:
    assert (first_line(recorded)["output_path"], first_line(recorded)["formats"]) == (
        "/dane/subjects/1234567890/test/invoices",
        ["xml"],
    )


def test_a_recorded_read_states_the_schema_it_was_written_under(recorded: Path) -> None:
    assert first_line(recorded)["schema_version"] == 1


def test_the_file_is_readable_only_by_its_owner(recorded: Path) -> None:
    assert stat.S_IMODE(recorded.stat().st_mode) == 0o600


def test_the_directory_is_reachable_only_by_its_owner(recorded: Path) -> None:
    assert stat.S_IMODE(recorded.parent.stat().st_mode) == 0o700


def test_a_second_read_is_appended_rather_than_replacing_the_first(
    trail: AuditTrail, authorisation: Authorisation, recorded: Path
) -> None:
    # The whole point of the divergence from D-006: the trail only grows, and no
    # append ever puts the accumulated record at risk.
    trail.record((written_entry(authorisation, numbers=(SECOND_NUMBER,)),))

    assert len(recorded.read_text(encoding="utf-8").splitlines()) == 2


def test_a_pass_with_nothing_to_record_leaves_no_file(trail: AuditTrail) -> None:
    trail.record(())

    assert not trail.path.exists()


def test_an_unwritten_trail_reads_back_as_empty(trail: AuditTrail) -> None:
    assert trail.entries() == ()


def test_the_trail_reads_back_as_what_was_recorded(
    trail: AuditTrail, authorisation: Authorisation, recorded: Path
) -> None:
    assert trail.entries() == (written_entry(authorisation),)


def test_a_skip_reads_back_as_a_skip_and_not_as_a_write(
    trail: AuditTrail, authorisation: Authorisation
) -> None:
    trail.record((written_entry(authorisation, disclosure=Disclosure.DEDUPLICATION_SKIP),))

    assert trail.entries()[0].disclosure is Disclosure.DEDUPLICATION_SKIP


def test_an_entry_without_a_role_or_a_file_reads_back_as_such(
    trail: AuditTrail, authorisation: Authorisation
) -> None:
    trail.record(
        (
            AuditEntry(
                recorded_at=MOMENT,
                operation=AuditedOperation.LISTING,
                authorisation=authorisation,
                disclosure=Disclosure.MODEL_CONTEXT,
                subject_role=None,
                criteria="issue 2026-08-15..open",
                document_count=0,
                ksef_numbers=(),
                output_path=None,
                formats=(),
            ),
        )
    )

    assert (trail.entries()[0].subject_role, trail.entries()[0].output_path) == (None, None)


@pytest.mark.parametrize(
    ("basis", "source", "spelled"),
    [
        (AuthorisationBasis.KSEF_TOKEN, "keyring", "ksef_token:keyring"),
        (AuthorisationBasis.OPERATOR, None, "operator:cli"),
        (AuthorisationBasis.ARCHIVE, None, "archiwum_lokalne"),
    ],
    ids=["token-with-its-source", "operator-carrying-a-colon", "archive"],
)
def test_a_footing_reads_back_as_the_one_that_was_written(
    trail: AuditTrail,
    basis: AuthorisationBasis,
    source: str | None,
    spelled: str,
) -> None:
    """The spelling on disk is unchanged, so no `SCHEMA_VERSION` bump was owed."""
    written = Authorisation(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        basis=basis,
        source=source,
    )
    recorded_path = trail.record((written_entry(written),))
    read_back = trail.entries()[0].authorisation

    assert (
        first_line(recorded_path)["authorisation_basis"],
        read_back.basis,
        read_back.source,
    ) == (
        spelled,
        basis,
        source,
    )


def test_a_line_from_another_schema_is_refused_rather_than_guessed_at(
    trail: AuditTrail, authorisation: Authorisation, recorded: Path
) -> None:
    document = first_line(recorded) | {"schema_version": 99}
    recorded.write_text(json.dumps(document) + "\n", encoding="utf-8")

    with pytest.raises(AuditTrailUnreadable, match="schemat 99"):
        trail.entries()


def test_a_line_torn_in_half_is_named_as_a_damaged_trail(trail: AuditTrail, recorded: Path) -> None:
    """GH-104: a raw JSONDecodeError said nothing about which line, or which file."""
    whole = recorded.read_text(encoding="utf-8")
    recorded.write_text(whole + whole[: len(whole) // 2], encoding="utf-8")

    with pytest.raises(AuditTrailUnreadable, match="Line 2 of the audit trail"):
        trail.entries()


def test_a_damaged_line_is_named_without_quoting_what_it_held(
    trail: AuditTrail, recorded: Path
) -> None:
    # The line carries KSeF numbers, and an error message is the last place
    # those should surface (D-011).
    recorded.write_text('{"broken"\n', encoding="utf-8")

    with pytest.raises(AuditTrailUnreadable) as refusal:
        trail.entries()

    assert FIRST_NUMBER not in str(refusal.value)


def test_two_recorders_never_interleave_their_lines(
    trail: AuditTrail, authorisation: Authorisation
) -> None:
    """GH-104: `O_APPEND` is indivisible only up to `PIPE_BUF`."""
    crowded = written_entry(authorisation, numbers=tuple(FIRST_NUMBER for _ in range(400)))
    trail.record((crowded, crowded))
    in_another_thread(lambda: trail.record((crowded,)))

    assert len(trail.entries()) == 3


def test_the_recorded_window_states_both_ends() -> None:
    # Ślad audytowy ma pozwolić odtworzyć zakres pytania, a od GH-84 zakres
    # zawsze ma oba końce — nie ma już wpisu kończącego się na „open".
    asked = Period(
        date_from=datetime(2026, 9, 1, tzinfo=UTC),
        date_to=datetime(2026, 9, 14, tzinfo=UTC),
        date_type=DateType.PERMANENT_STORAGE,
    )

    assert window_criteria(asked).endswith("2026-09-01T00:00:00+00:00..2026-09-14T00:00:00+00:00")


def test_the_recorded_window_names_the_date_the_question_was_asked_by() -> None:
    asked = Period(
        date_from=datetime(2026, 9, 1, tzinfo=UTC),
        date_to=datetime(2026, 9, 14, tzinfo=UTC),
        date_type=DateType.ISSUE,
    )

    assert window_criteria(asked).startswith("issue_date ")
