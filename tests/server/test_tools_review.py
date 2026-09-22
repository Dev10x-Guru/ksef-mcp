from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from mcp import Client
from mcp.types import ListToolsResult

from ksef_mcp import config
from ksef_mcp.invoices.listing import LISTING_THRESHOLD, Question
from ksef_mcp.invoices.review import REVIEW_WINDOW, InvoiceReview, assess
from ksef_mcp.ksef_port import DateType, KsefEnvironment, KsefNumber, Period, SubjectRole
from ksef_mcp.server import context, server, tools_review
from ksef_mcp.server.app import Journal
from ksef_mcp.server.errors import NotConfigured
from ksef_mcp.server.results import InvoiceReviewResult
from ksef_mcp.server.tools_review import review_invoices
from ksef_mcp.storage.audit import AuditedOperation, AuditTrail, Disclosure
from tests.server.conftest import NIP, REACHED, recorded_reads
from tests.support.synthetic import synthetic_metadata

REVIEW_ENDS = datetime(2026, 9, 14, 7, tzinfo=UTC)

# Start derived from REVIEW_WINDOW, not rewritten as a literal number (GH-84).
REVIEW_PERIOD = Period(
    date_from=REVIEW_ENDS - REVIEW_WINDOW,
    date_to=REVIEW_ENDS,
    date_type=DateType.INVOICING,
)

LEDGER_PATH = "/dane/subjects/1234567890/test/review.json"

# A number issued in July, detected in September — the case from [D-025].
LATE_NUMBER = "1234567890-20260707-0100AB12CD77-56"


def reviewed_question(subject_role: SubjectRole) -> Question:
    return Question(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        subject_role=subject_role,
        period=REVIEW_PERIOD,
    )


class StubReviewer:
    """Stands in for the comparison itself: this module's job is the tool surface."""

    def __init__(
        self,
        *,
        port: object,
        cache: object,
        store: object,
        allowance: object,
    ) -> None:
        self.port = port
        self.cache = cache
        self.store = store
        self.allowance = allowance

    def run(self, *, nip: str, token: str) -> InvoiceReview:
        late = replace(synthetic_metadata(1), ksef_number=KsefNumber(LATE_NUMBER))
        return InvoiceReview(
            nip=nip,
            environment=KsefEnvironment.TEST,
            threshold=LISTING_THRESHOLD,
            period=REVIEW_PERIOD,
            ledger_path=LEDGER_PATH,
            subject_roles=(
                assess(
                    question=reviewed_question(SubjectRole.BUYER),
                    invoices=(late,),
                    reviewed=frozenset(),
                    complete=True,
                    budget_bound=False,
                    moment=REVIEW_PERIOD.date_to,
                    queried_at=REACHED,
                    from_cache=True,
                ),
                assess(
                    question=reviewed_question(SubjectRole.SELLER),
                    invoices=(),
                    reviewed=frozenset(),
                    complete=True,
                    budget_bound=False,
                    moment=REVIEW_PERIOD.date_to,
                    queried_at=REACHED,
                    from_cache=False,
                ),
            ),
        )


@pytest.fixture
def reviewing(monkeypatch: pytest.MonkeyPatch, with_a_token: None) -> None:
    monkeypatch.setattr(tools_review, "InvoiceReviewer", StubReviewer)
    monkeypatch.setattr(context, "PeriodCache", lambda **kwargs: kwargs)
    monkeypatch.setattr(context, "ReviewStore", lambda **kwargs: kwargs)


@pytest.fixture
def review(reviewing: None) -> InvoiceReviewResult:
    return review_invoices(journal=Journal(operation=AuditedOperation.REVIEW))


def test_the_review_refuses_before_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_configuration", lambda: None)

    with pytest.raises(NotConfigured, match="ksef-mcp onboarding"):
        review_invoices(journal=Journal(operation=AuditedOperation.REVIEW))


def test_the_review_names_the_subject_it_acted_as(review: InvoiceReviewResult) -> None:
    assert (review.nip, review.environment, review.threshold) == (NIP, "test", 50)


def test_the_review_states_the_window_once(review: InvoiceReviewResult) -> None:
    assert (review.period_from, review.period_to) == (
        REVIEW_PERIOD.date_from.isoformat(),
        REVIEW_PERIOD.date_to.isoformat(),
    )


def test_the_review_points_at_the_ledger_that_survives_the_session(
    review: InvoiceReviewResult,
) -> None:
    assert review.ledger_file == LEDGER_PATH


def test_the_review_sums_every_subject_role_in_one_sentence(review: InvoiceReviewResult) -> None:
    assert review.message == (
        "Nowe faktury od ostatniego przeglądu: 1. "
        "W tym z numerem sprzed bieżącego miesiąca: 1. "
        "Co widać dla każdej roli podmiotu z osobna — w `subject_roles`."
    )


def test_a_new_invoice_carries_the_day_ksef_gave_it_its_number(
    review: InvoiceReviewResult,
) -> None:
    # Data otrzymania, nie data pobrania i nie data wystawienia sprzedawcy.
    row = review.subject_roles[0].new_invoices[0]

    assert (row.received_on, row.issue_date) == ("2026-07-07", "2026-09-01")


def test_an_invoice_from_an_earlier_month_is_counted_as_a_signal(
    review: InvoiceReviewResult,
) -> None:
    assert review.subject_roles[0].earlier_month_count == 1


def test_a_reported_invoice_is_recorded_as_shown(review: InvoiceReviewResult) -> None:
    assert review.subject_roles[0].marked_as_reviewed is True


def test_a_subject_role_with_nothing_new_says_so(review: InvoiceReviewResult) -> None:
    assert (review.subject_roles[1].outcome, review.subject_roles[1].new_count) == (
        "nothing_new",
        0,
    )


def test_the_review_says_whether_disk_answered(review: InvoiceReviewResult) -> None:
    assert [one.from_cache for one in review.subject_roles] == [True, False]


def test_the_review_says_when_it_was_asked(review: InvoiceReviewResult) -> None:
    assert review.subject_roles[0].queried_at == REACHED.isoformat()


def test_the_review_carries_the_gross_total_of_what_is_new(
    review: InvoiceReviewResult,
) -> None:
    assert review.subject_roles[0].gross_totals[0].gross == Decimal("1230.00")


@pytest.mark.anyio
async def test_the_review_tool_takes_no_arguments_from_the_caller(
    listed_tools: ListToolsResult,
) -> None:
    tool = next(tool for tool in listed_tools.tools if tool.name == "review_new_invoices")

    assert tool.input_schema.get("properties", {}) == {}


@pytest.mark.anyio
async def test_the_review_tool_answers_with_metadata_but_no_invoice(reviewing: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("review_new_invoices")

    assert "<Faktura" not in str(called.structured_content)


@pytest.mark.anyio
async def test_the_review_tool_hands_back_what_is_new(reviewing: None) -> None:
    async with Client(server, raise_exceptions=True) as client:
        called = await client.call_tool("review_new_invoices")

    assert called.structured_content["subject_roles"][0]["new_count"] == 1


def test_a_review_records_what_the_reader_was_shown(
    review: InvoiceReviewResult, trail: AuditTrail
) -> None:
    shown = recorded_reads(trail)[0]

    assert (shown.operation, shown.disclosure, shown.ksef_numbers) == (
        "review_new_invoices",
        Disclosure.MODEL_CONTEXT,
        (LATE_NUMBER,),
    )


def test_a_review_records_the_window_and_the_count(
    review: InvoiceReviewResult, trail: AuditTrail
) -> None:
    assert (recorded_reads(trail)[0].criteria, recorded_reads(trail)[0].document_count) == (
        f"invoicing_date {REVIEW_PERIOD.date_from.isoformat()}"
        f"..{REVIEW_PERIOD.date_to.isoformat()}",
        1,
    )


def test_a_review_records_a_subject_role_with_nothing_new(
    review: InvoiceReviewResult, trail: AuditTrail
) -> None:
    assert recorded_reads(trail)[1].document_count == 0
