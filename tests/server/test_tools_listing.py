from datetime import UTC, datetime
from decimal import Decimal

import pytest
from mcp import Client
from mcp.types import ListToolsResult

from ksef_mcp import config
from ksef_mcp.invoices.listing import (
    LISTING_THRESHOLD,
    InvoiceListing,
    Question,
    summarise,
)
from ksef_mcp.ksef_port import DateType, KsefEnvironment, MetadataPage, Period, SubjectRole
from ksef_mcp.server import context, server, tools_listing
from ksef_mcp.server.app import Journal
from ksef_mcp.server.errors import NotConfigured
from ksef_mcp.server.results import InvoiceListingResult
from ksef_mcp.server.tools_listing import describe_listing, list_invoices
from ksef_mcp.storage.audit import AuditedOperation, AuditTrail, Disclosure
from tests.server.conftest import NIP, REACHED, recorded_reads
from tests.support.synthetic import synthetic_metadata

LISTING_PERIOD = Period(
    date_from=datetime(2026, 8, 15, 7, tzinfo=UTC),
    date_to=datetime(2026, 9, 14, 7, tzinfo=UTC),
    date_type=DateType.ISSUE,
)


def listed_question(subject_role: SubjectRole) -> Question:
    return Question(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        subject_role=subject_role,
        period=LISTING_PERIOD,
    )


class StubLister:
    """Stands in for the pass over the subject types: this module's job is the surface."""

    def __init__(self, *, port: object, cache: object, allowance: object) -> None:
        self.port = port
        self.cache = cache
        self.allowance = allowance

    def run(self, *, nip: str, token: str) -> InvoiceListing:
        return InvoiceListing(
            nip=nip,
            environment=KsefEnvironment.TEST,
            threshold=LISTING_THRESHOLD,
            period=LISTING_PERIOD,
            subject_roles=(
                summarise(
                    question=listed_question(SubjectRole.BUYER),
                    page=MetadataPage(
                        invoices=(synthetic_metadata(1),),
                        has_more=False,
                        truncated=False,
                        hwm_date=None,
                    ),
                    queried_at=REACHED,
                    from_cache=True,
                ),
                summarise(
                    question=listed_question(SubjectRole.SELLER),
                    page=MetadataPage(invoices=(), has_more=False, truncated=False, hwm_date=None),
                    queried_at=REACHED,
                ),
            ),
        )


@pytest.fixture
def listing_stubbed(monkeypatch: pytest.MonkeyPatch, with_a_token: None) -> None:
    monkeypatch.setattr(tools_listing, "InvoiceLister", StubLister)
    monkeypatch.setattr(context, "PeriodCache", lambda **kwargs: kwargs)


@pytest.fixture
def listing(listing_stubbed: None) -> InvoiceListingResult:
    return list_invoices(journal=Journal(operation=AuditedOperation.LISTING))


def test_the_listing_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        list_invoices(journal=Journal(operation=AuditedOperation.LISTING))


def test_the_listing_names_the_subject_it_acted_as(listing: InvoiceListingResult) -> None:
    assert (listing.nip, listing.environment) == (NIP, "test")


def test_the_listing_states_the_window_once(listing: InvoiceListingResult) -> None:
    assert (listing.period_from, listing.period_to) == (
        LISTING_PERIOD.date_from.isoformat(),
        LISTING_PERIOD.date_to.isoformat(),
    )


def test_the_listing_states_the_threshold_it_applied(listing: InvoiceListingResult) -> None:
    assert listing.threshold == 50


def test_the_listing_sums_every_subject_role_in_one_sentence(
    listing: InvoiceListingResult,
) -> None:
    assert listing.message == (
        "Faktury w oknie: 1. Co widać dla każdej roli podmiotu z osobna — w `subject_roles`."
    )


def test_the_listing_reports_every_subject_role_it_asked_about(
    listing: InvoiceListingResult,
) -> None:
    assert [one.subject_role for one in listing.subject_roles] == ["buyer", "seller"]


def test_a_listed_subject_role_carries_the_eight_columns(listing: InvoiceListingResult) -> None:
    row = listing.subject_roles[0].invoices[0]

    assert (row.ksef_number, row.seller_invoice_number, row.currency) == (
        str(synthetic_metadata(1).ksef_number),
        "FV/2026/09/001",
        "PLN",
    )


def test_a_listed_subject_role_reports_its_gross_total(listing: InvoiceListingResult) -> None:
    assert listing.subject_roles[0].gross_totals[0].gross == Decimal("1230.00")


def test_a_listed_subject_role_says_whether_disk_answered(
    listing: InvoiceListingResult,
) -> None:
    assert listing.subject_roles[0].from_cache is True


def test_a_listed_subject_role_says_when_it_was_asked(listing: InvoiceListingResult) -> None:
    assert listing.subject_roles[0].queried_at == REACHED.isoformat()


def test_an_empty_subject_role_is_reported_as_such(listing: InvoiceListingResult) -> None:
    assert (listing.subject_roles[1].outcome, listing.subject_roles[1].invoices) == ("empty", [])


def test_an_empty_subject_role_restates_the_question(listing: InvoiceListingResult) -> None:
    assert f"NIP {NIP}" in listing.subject_roles[1].message


def test_a_described_window_names_the_end_it_asked_about() -> None:
    # Od GH-84 nie ma okna bez końca, więc opis zawsze ma co podać.
    described = describe_listing(
        InvoiceListing(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            threshold=LISTING_THRESHOLD,
            period=Period(
                date_from=datetime(2026, 9, 1, tzinfo=UTC),
                date_to=datetime(2026, 9, 14, tzinfo=UTC),
                date_type=DateType.PERMANENT_STORAGE,
            ),
            subject_roles=(),
        )
    )

    assert described.period_to == "2026-09-14T00:00:00+00:00"


@pytest.mark.anyio
async def test_the_listing_tool_takes_no_arguments_from_the_caller(
    listed_tools: ListToolsResult,
) -> None:
    # D-020 again: a window driven by an agent empties a twenty-per-hour
    # allowance in minutes.
    tool = next(tool for tool in listed_tools.tools if tool.name == "list_recent_invoices")

    assert tool.input_schema.get("properties", {}) == {}


@pytest.mark.anyio
async def test_the_listing_tool_answers_with_metadata_but_no_invoice(
    listing_stubbed: None,
) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("list_recent_invoices")

    assert "<Faktura" not in str(called.structured_content)


@pytest.mark.anyio
async def test_the_listing_tool_asks_for_no_confirmation(listing_stubbed: None) -> None:
    # Bramka zgody przy odczycie uczy klikać „tak" bez patrzenia i psuje moment,
    # w którym pytanie naprawdę coś znaczy (D-011).
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("list_recent_invoices")

    assert called.is_error is not True


def test_a_listing_records_that_the_model_was_shown_the_rows(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    # Osobne zdarzenie niż zapis na dysk (D-011): „czat zobaczył" to nie to samo
    # co „plik powstał".
    shown = recorded_reads(trail)[0]

    assert (shown.operation, shown.disclosure, shown.output_path) == (
        "list_recent_invoices",
        Disclosure.MODEL_CONTEXT,
        None,
    )


def test_a_listing_records_the_window_it_asked_about(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].criteria == (
        f"issue_date {LISTING_PERIOD.date_from.isoformat()}..{LISTING_PERIOD.date_to.isoformat()}"
    )


def test_a_listing_records_the_numbers_that_reached_the_answer(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[0].ksef_numbers == (str(synthetic_metadata(1).ksef_number),)


def test_a_listing_records_a_subject_role_that_returned_nothing(
    listing: InvoiceListingResult, trail: AuditTrail
) -> None:
    # Pytanie padło, więc zakres, o który zapytano, jest częścią śladu.
    empty = recorded_reads(trail)[1]

    assert (empty.subject_role, empty.document_count, empty.ksef_numbers) == ("seller", 0, ())
