import pytest
from mcp import Client
from mcp.types import ListToolsResult

from ksef_mcp import config
from ksef_mcp.config import Configuration
from ksef_mcp.diagnostics import UNCORRELATED
from ksef_mcp.invoices.synchronisation import SynchronisationReport
from ksef_mcp.ksef_port import KsefEnvironment
from ksef_mcp.server import context, server, tools_synchronisation
from ksef_mcp.server.app import Journal
from ksef_mcp.server.errors import NotConfigured
from ksef_mcp.server.results import SynchronisationResult
from ksef_mcp.server.tools_synchronisation import describe, synchronise
from ksef_mcp.storage import token_store
from ksef_mcp.storage.audit import AuditedOperation, AuditTrail, Disclosure
from tests.server.conftest import (
    ARCHIVE_DIRECTORY,
    ARCHIVED_NUMBER,
    ASSUMED_CEILINGS,
    GRANTED_CEILINGS,
    HELD_NUMBER,
    NIP,
    REACHED,
    StubSynchroniser,
    recorded_reads,
)


@pytest.fixture
def synchronising(monkeypatch: pytest.MonkeyPatch, with_a_token: None) -> None:
    monkeypatch.setattr(tools_synchronisation, "Synchroniser", StubSynchroniser)
    monkeypatch.setattr(context, "SyncStore", lambda **kwargs: kwargs)


@pytest.fixture
def synchronised(synchronising: None) -> SynchronisationResult:
    return synchronise(journal=Journal(operation=AuditedOperation.SYNCHRONISATION))


@pytest.mark.anyio
async def test_synchronisation_takes_no_arguments_from_the_caller(
    listed_tools: ListToolsResult,
) -> None:
    # D-020 is a hard criterion: an agent driving the window or the page size
    # spends a twenty-per-hour allowance in two minutes.
    tool = next(tool for tool in listed_tools.tools if tool.name == "synchronise_invoices")

    assert tool.input_schema.get("properties", {}) == {}


def test_synchronisation_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        synchronise(journal=Journal(operation=AuditedOperation.SYNCHRONISATION))


def test_synchronisation_refuses_without_a_token(
    monkeypatch: pytest.MonkeyPatch, configured: Configuration
) -> None:
    monkeypatch.setattr(token_store, "read_token", lambda *, nip: None)

    with pytest.raises(NotConfigured, match="token set"):
        synchronise(journal=Journal(operation=AuditedOperation.SYNCHRONISATION))


def test_the_answer_names_the_environment_it_spoke_to(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.environment == "test"


def test_the_answer_reports_every_subject_role(synchronised: SynchronisationResult) -> None:
    assert [one.subject_role for one in synchronised.subject_roles] == [
        "buyer",
        "third_subject",
    ]


def test_the_answer_says_how_far_each_subject_role_reached(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_roles[0].synchronised_up_to == REACHED.isoformat()


def test_a_subject_role_that_never_ran_reports_no_position(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_roles[1].synchronised_up_to is None


def test_the_answer_counts_what_the_package_carries(
    synchronised: SynchronisationResult,
) -> None:
    assert (
        synchronised.subject_roles[0].invoice_count,
        synchronised.subject_roles[0].part_count,
    ) == (7, 1)


def test_the_answer_says_where_the_invoices_landed(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_roles[0].archive_directory == ARCHIVE_DIRECTORY


def test_the_answer_names_the_numbers_it_stored(synchronised: SynchronisationResult) -> None:
    assert synchronised.subject_roles[0].archived == [ARCHIVED_NUMBER]


def test_the_answer_separates_what_was_already_held(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_roles[0].already_held == [HELD_NUMBER]


def test_a_subject_role_that_stored_nothing_reports_no_directory(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_roles[1].archive_directory is None


@pytest.mark.anyio
async def test_the_tool_answers_with_paths_and_numbers_but_no_invoice(
    synchronising: None,
) -> None:
    # An FA(2)/FA(3) body carries the counterparty's personal data, so the tool
    # names the file and never opens it (D-011).
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("synchronise_invoices")

    assert "Faktura" not in str(called.structured_content)


def test_the_answer_points_at_the_packages_left_to_decrypt(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.pending_exports == ["EXP-1"]


def test_the_answer_points_at_the_record_on_disk(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.state_file == "/dane/synchronisation.json"


@pytest.mark.anyio
async def test_the_tool_hands_back_the_pass_it_ran(synchronising: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("synchronise_invoices")

    assert called.structured_content["pending_exports"] == ["EXP-1"]


def test_the_answer_says_a_granted_ceiling_was_granted(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.session_ceilings.assumed is False
    assert "przyznany przez KSeF" in synchronised.session_ceilings.message


def test_the_answer_says_in_as_many_words_that_a_ceiling_was_only_assumed() -> None:
    # GH-118, and the signal whose absence cost the GH-76 diagnosis: production
    # answered about limits in a shape the SDK could not read, the conservative
    # fallback took over, and nothing said so.
    described = describe(
        SynchronisationReport(
            subject_roles=(),
            pending_exports=(),
            state_path="/dane/x.json",
            ceilings=ASSUMED_CEILINGS,
        ),
        nip=NIP,
        environment=KsefEnvironment.DEMO,
    )

    assert described.session_ceilings.assumed is True
    assert "założony, nie przyznany" in described.session_ceilings.message


def test_the_answer_carries_the_figures_the_ceiling_is_made_of(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.session_ceilings.max_invoices_per_session == 10_000


@pytest.mark.anyio
async def test_the_answer_names_the_call_in_the_journal(synchronising: None) -> None:
    # GH-117: without this the only way to tie the answer to its journal lines
    # is the timestamp, which stops working as soon as two passes overlap.
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("synchronise_invoices")

    assert called.structured_content["correlation"] != UNCORRELATED


@pytest.mark.anyio
async def test_two_passes_are_named_differently(synchronising: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        first = await client.call_tool("synchronise_invoices")
        second = await client.call_tool("synchronise_invoices")

    assert first.structured_content["correlation"] != second.structured_content["correlation"]


def test_the_description_survives_an_empty_pass() -> None:
    described = describe(
        SynchronisationReport(
            subject_roles=(),
            pending_exports=(),
            state_path="/dane/x.json",
            ceilings=GRANTED_CEILINGS,
        ),
        nip=NIP,
        environment=KsefEnvironment.DEMO,
    )

    assert described.subject_roles == []


def test_the_synchronisation_answer_names_the_subject_it_acted_for(
    synchronised: SynchronisationResult,
) -> None:
    # It was the one answer of the five that left the subject to be inferred
    # from a directory path.
    assert synchronised.nip == NIP


def test_the_synchronisation_answer_sums_the_whole_pass_in_one_sentence(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.message == (
        "Zarchiwizowane faktury: 1. Już na dysku: 1. "
        "Co zrobiła każda rola podmiotu z osobna — w `subject_roles`."
    )


def test_a_pass_with_nothing_to_caution_about_still_answers_with_the_field(
    synchronised: SynchronisationResult,
) -> None:
    # An empty list is a statement; a missing field is a guess.
    assert synchronised.warnings == []


def test_one_subject_role_explains_itself_under_the_same_name_as_the_whole_pass(
    synchronised: SynchronisationResult,
) -> None:
    assert synchronised.subject_roles[0].message == "Paczka EXP-1 trafiła do archiwum."


# Audit trail (#45). In a leak dispute it is the only evidence, so every
# read tool leaves an entry — regardless of a read having no consent gate.


def test_a_synchronisation_records_what_it_stored(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    stored = recorded_reads(trail)[0]

    assert (stored.operation, stored.disclosure, stored.ksef_numbers) == (
        "synchronise_invoices",
        Disclosure.DISK,
        (ARCHIVED_NUMBER,),
    )


def test_a_synchronisation_records_a_deduplication_skip_as_a_skip(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    # A trail made only of writes would read as if this invoice was never
    # touched — yet it was seen and recognised as already held.
    skipped = recorded_reads(trail)[1]

    assert (skipped.disclosure, skipped.ksef_numbers) == (
        Disclosure.DEDUPLICATION_SKIP,
        (HELD_NUMBER,),
    )


def test_a_synchronisation_records_the_subject_and_the_footing(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    stored = recorded_reads(trail)[0]

    assert (stored.authorisation.nip, stored.authorisation.recorded) == (
        NIP,
        "ksef_token:keyring",
    )


def test_a_synchronisation_records_where_the_invoices_landed(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].output_path == ARCHIVE_DIRECTORY


def test_a_subject_role_that_touched_nothing_leaves_no_entry(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    assert len(recorded_reads(trail)) == 2


def test_no_token_value_reaches_the_trail(
    synchronised: SynchronisationResult, trail: AuditTrail
) -> None:
    assert "tajny-token" not in trail.path.read_text(encoding="utf-8")
