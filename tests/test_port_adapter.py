import inspect
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import reduce
from operator import or_
from pathlib import Path
from types import UnionType
from typing import Final, Union, get_args, get_origin

import httpx
import pytest
from ksef2 import Environment
from ksef2.clients.auth import AuthClient
from ksef2.clients.limits import LimitsClient
from ksef2.core.exceptions import (
    KSeFAuthError,
    KSeFRateLimitError,
    KSeFSessionError,
)
from ksef2.domain.models.invoices import (
    ExportHandle,
    ExportStatusInfo,
    InvoiceExportStatusResponse,
    InvoiceMetadata,
    InvoiceMetadataBuyer,
    InvoiceMetadataSeller,
    InvoicePackage,
    PackagePart,
    QueryInvoicesMetadataResponse,
)
from ksef2.domain.models.limits import (
    ApiRateLimits,
    ContextLimits,
    RateLimitValues,
    SessionLimits,
)
from ksef2.services.invoices import InvoicesService

from ksef_mcp.diagnostics import LOGGER_NAME, correlated
from ksef_mcp.ksef_port import (
    BudgetExhausted,
    DateType,
    DocumentType,
    ExportPart,
    ExportState,
    KsefAuthenticationFailed,
    KsefEnvironment,
    KsefRateLimited,
    KsefRefused,
    KsefSession,
    KsefUnreachable,
    Operation,
    PackageLinkExpired,
    Period,
    QueryBudget,
    SubjectRole,
)
from ksef_mcp.ksef_port import adapter as port_adapter
from ksef_mcp.ksef_port.adapter import (
    CONSERVATIVE_CEILINGS,
    CONSERVATIVE_RATES,
    Ksef2Port,
)
from ksef_mcp.ksef_port.types import PAGE_SIZE
from tests.support.doubles import (
    HWM,
    FakeApiRateLimits,
    FakeAuthenticated,
    FakeAuthentication,
    FakeBuyer,
    FakeContextLimits,
    FakeExportHandle,
    FakeExportStatusInfo,
    FakeExportStatusResponse,
    FakeInvoicePackage,
    FakeInvoicesService,
    FakeLimitsClient,
    FakeMetadata,
    FakeMetadataPage,
    FakePackagePart,
    FakeRateValues,
    FakeSdkClient,
    FakeSeller,
    FakeSessionLimits,
    FakeUnparsableLimitsClient,
    RefusingLimitsClient,
    sdk_metadata,
    sdk_part,
)
from tests.support.synthetic import synthetic_content_hash, synthetic_credential

NIP = "1234567890"

TOKEN_VALUE = "aaaabbbbccccdddd"

TOKEN = synthetic_credential(TOKEN_VALUE)

WINDOW = Period(
    date_from=datetime(2026, 8, 1, tzinfo=UTC),
    date_to=datetime(2026, 9, 1, tzinfo=UTC),
    date_type=DateType.ISSUE,
)


def ready_status() -> FakeExportStatusResponse:
    return FakeExportStatusResponse(
        status=FakeExportStatusInfo(code=200, description="Zakończone"),
        package=FakeInvoicePackage(
            invoice_count=2,
            parts=[sdk_part(1), sdk_part(2)],
            is_truncated=True,
            last_permanent_storage_date=HWM - timedelta(days=1),
            permanent_storage_hwm_date=HWM,
        ),
    )


class Plan:
    def __init__(
        self,
        *,
        page: list[FakeMetadata] | None = None,
        status: FakeExportStatusResponse | None = None,
        authentication_error: Exception | None = None,
        call_error: Exception | None = None,
        limits: object | None = None,
        further: dict[int, FakeMetadataPage] | None = None,
    ) -> None:
        self.limits = limits if limits is not None else FakeLimitsClient()
        self.service = FakeInvoicesService(
            page=list(page if page is not None else [sdk_metadata(1), sdk_metadata(2)]),
            status=status if status is not None else ready_status(),
            error=call_error,
            further=dict(further or {}),
        )
        self.authentication_error = authentication_error
        self.built: list[FakeSdkClient] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def build(*, environment: object, transport_config: object) -> FakeSdkClient:
            client = FakeSdkClient(
                environment=environment,
                transport_config=transport_config,
                authentication=FakeAuthentication(
                    authenticated=FakeAuthenticated(
                        invoices=self.service,
                        limits=self.limits,
                    ),
                    error=self.authentication_error,
                ),
            )
            self.built.append(client)
            return client

        monkeypatch.setattr(port_adapter, "Client", build)


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> Plan:
    prepared = Plan()
    prepared.install(monkeypatch)
    return prepared


@pytest.fixture
def port() -> Ksef2Port:
    return Ksef2Port(environment=KsefEnvironment.TEST)


@pytest.fixture
def session(plan: Plan, port: Ksef2Port) -> Iterator[KsefSession]:
    with port.session(nip=NIP, token=TOKEN) as opened:
        yield opened


# Which SDK model each double stands in for. One table rather than one per
# test: it is also what lets a double's own annotation — `list[FakeMetadata]`,
# `FakeInvoicePackage | None` — be read as the SDK type it claims to imitate.
#
# `FakeExportHandle` and `FakeExportStatusInfo` were absent from the earlier
# check altogether, so `schedule_export` and the export status code were the
# two mappings nothing guarded (GH-175).
STAND_INS: Final[dict[type, type]] = {
    FakeMetadata: InvoiceMetadata,
    FakeSeller: InvoiceMetadataSeller,
    FakeBuyer: InvoiceMetadataBuyer,
    FakeMetadataPage: QueryInvoicesMetadataResponse,
    FakeRateValues: RateLimitValues,
    FakeApiRateLimits: ApiRateLimits,
    FakeSessionLimits: SessionLimits,
    FakeContextLimits: ContextLimits,
    FakePackagePart: PackagePart,
    FakeInvoicePackage: InvoicePackage,
    FakeExportStatusResponse: InvoiceExportStatusResponse,
    FakeExportHandle: ExportHandle,
    FakeExportStatusInfo: ExportStatusInfo,
}

# The amounts the adapter hands to `as_amount`, which spells them through
# `Decimal(str(value))`. A `None` here does not read as "no amount" — it
# reaches `Decimal("None")` and raises `InvalidOperation` from inside a
# translation that has no branch for it. So the invariant this rests on is
# that KSeF states every one of them, and it is asserted rather than assumed.
REQUIRED_AMOUNTS: Final[tuple[str, ...]] = ("net_amount", "gross_amount", "vat_amount")

# Every SDK call the adapter makes, paired with the double that stands in for
# it. Field names were checked before this; the call signatures were not, so a
# renamed keyword or a newly required argument reached the taxpayer's machine
# as a TypeError with a green suite behind it (GH-175).
SDK_CALLS: Final[tuple[tuple[type, type, str], ...]] = (
    (FakeInvoicesService, InvoicesService, "query_metadata"),
    (FakeInvoicesService, InvoicesService, "schedule_export"),
    (FakeInvoicesService, InvoicesService, "get_export_status"),
    (FakeInvoicesService, InvoicesService, "download_invoice"),
    (FakeLimitsClient, LimitsClient, "get_api_rate_limits"),
    (FakeLimitsClient, LimitsClient, "get_context_limits"),
    (FakeAuthentication, AuthClient, "with_token"),
)


def as_sdk_annotation(annotation: object) -> object:
    """The double's annotation with every stand-in swapped for what it imitates."""
    arguments = get_args(annotation)
    if not arguments:
        return STAND_INS.get(annotation, annotation)  # type: ignore[arg-type]
    rebuilt = tuple(as_sdk_annotation(one) for one in arguments)
    origin = get_origin(annotation)
    if origin in (UnionType, Union):
        return reduce(or_, rebuilt)
    return origin[rebuilt]  # type: ignore[index]


def parameters_of(owner: type, name: str) -> dict[str, inspect.Parameter]:
    return {
        parameter_name: parameter
        for parameter_name, parameter in inspect.signature(getattr(owner, name)).parameters.items()
        if parameter_name != "self"
    }


def required_parameters_of(owner: type, name: str) -> set[str]:
    return {
        parameter_name
        for parameter_name, parameter in parameters_of(owner, name).items()
        if parameter.default is inspect.Parameter.empty
    }


@pytest.mark.parametrize(("double", "real"), STAND_INS.items(), ids=lambda one: one.__name__)
def test_the_doubles_still_carry_the_field_names_the_sdk_does(double: type, real: type) -> None:
    # Without this the suite stays green when ksef2 renames a field, and the
    # rename surfaces as an AttributeError on the user's machine instead.
    assert set(inspect.get_annotations(double)) <= set(real.model_fields)


@pytest.mark.parametrize(("double", "real"), STAND_INS.items(), ids=lambda one: one.__name__)
def test_the_doubles_still_carry_the_field_types_the_sdk_does(double: type, real: type) -> None:
    # The name surviving is not the same as the meaning surviving. `float`
    # turning into `Decimal`, or a required field turning optional, leaves
    # every name in place and changes what the adapter must do with the value.
    assert {
        name: as_sdk_annotation(annotation)
        for name, annotation in inspect.get_annotations(double).items()
    } == {name: real.model_fields[name].annotation for name in inspect.get_annotations(double)}


@pytest.mark.parametrize("name", REQUIRED_AMOUNTS)
def test_ksef_states_every_amount_the_adapter_spells_through_decimal(name: str) -> None:
    field = InvoiceMetadata.model_fields[name]

    assert (field.is_required(), type(None) in get_args(field.annotation)) == (True, False)


@pytest.mark.parametrize(
    ("double", "real", "name"),
    SDK_CALLS,
    ids=[f"{real.__name__}.{name}" for _, real, name in SDK_CALLS],
)
def test_the_doubles_still_answer_the_call_the_sdk_answers(
    double: type,
    real: type,
    name: str,
) -> None:
    # Both directions, and they catch different drifts. A keyword the double
    # knows and the SDK does not means the adapter is calling something that
    # no longer exists; a keyword the SDK now requires and the double does not
    # know means the adapter is about to be refused for an argument it never
    # learned to pass.
    assert set(parameters_of(double, name)) <= set(parameters_of(real, name))
    assert required_parameters_of(real, name) <= set(parameters_of(double, name))


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        (KsefEnvironment.TEST, Environment.TEST),
        (KsefEnvironment.DEMO, Environment.DEMO),
        (KsefEnvironment.PRODUCTION, Environment.PRODUCTION),
    ],
)
def test_the_environment_is_always_passed_explicitly(
    plan: Plan,
    chosen: KsefEnvironment,
    expected: Environment,
) -> None:
    with Ksef2Port(environment=chosen).session(nip=NIP, token=TOKEN):
        pass

    assert plan.built[0].environment is expected


def test_the_sdk_retry_loop_is_disabled(session: KsefSession, plan: Plan) -> None:
    assert plan.built[0].transport_config.retry.max_attempts == 1


def test_the_client_is_closed_when_the_session_ends(plan: Plan, port: Ksef2Port) -> None:
    with port.session(nip=NIP, token=TOKEN):
        pass

    assert plan.built[0].closed is True


def test_the_token_reaches_authentication(session: KsefSession, plan: Plan) -> None:
    # The SDK is handed the bare string, and only here: the value is unwrapped
    # one line before it is spent, never earlier (GH-115).
    assert plan.built[0].authentication.credentials == [(NIP, TOKEN_VALUE)]


def test_a_request_to_ksef_is_journalled_under_the_call_that_made_it(
    session: KsefSession, journal: Path
) -> None:
    # GH-117: the whole point is being able to read back which requests one
    # tool call made, without matching eight rows by timestamp.
    with correlated() as minted:
        session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert f"[{minted}]" in journal.read_text(encoding="utf-8")


def test_a_journalled_request_names_neither_the_subject_nor_the_token(
    session: KsefSession, journal: Path
) -> None:
    session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    written = journal.read_text(encoding="utf-8")
    assert NIP not in written
    assert TOKEN_VALUE not in written


def test_metadata_is_requested_newest_first(session: KsefSession, plan: Plan) -> None:
    session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert plan.service.metadata_calls[0][1].sort_order == "desc"


def test_the_page_size_is_the_ceiling_not_the_sdk_default(
    session: KsefSession,
    plan: Plan,
) -> None:
    session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert plan.service.metadata_calls[0][1].page_size == PAGE_SIZE == 250


def test_the_subject_role_reaches_the_sdk_as_a_role(session: KsefSession, plan: Plan) -> None:
    session.query_metadata(period=WINDOW, subject_role=SubjectRole.SELLER)

    assert plan.service.metadata_calls[0][0].role == "seller"


def test_a_synchronisation_window_asks_ksef_to_stop_at_the_high_water_mark(
    session: KsefSession,
    plan: Plan,
) -> None:
    session.query_metadata(
        period=Period.for_synchronisation(
            since=datetime(2026, 8, 1, tzinfo=UTC),
            now=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        subject_role=SubjectRole.BUYER,
    )
    sent = plan.service.metadata_calls[0][0]

    assert (sent.date_type, sent.restrict_to_permanent_storage_hwm_date) == (
        "permanent_storage",
        True,
    )


def test_an_invoice_arrives_as_the_eight_columns_plus_its_counterparties(
    session: KsefSession,
) -> None:
    page = session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)
    first = page.invoices[0]

    assert (
        str(first.ksef_number),
        first.seller_invoice_number,
        first.seller_nip,
        first.seller_name,
        first.buyer_name,
        first.currency,
    ) == (
        "1234567890-20260901-0100AB12CD01-56",
        "FV/2026/09/001",
        "9876543210",
        "Dostawca sp. z o.o.",
        "Moja Firma sp. z o.o.",
        "PLN",
    )


def test_amounts_arrive_as_decimals_so_a_comparison_cannot_invent_a_grosz(
    session: KsefSession,
) -> None:
    page = session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)
    first = page.invoices[0]

    assert (first.gross_amount, first.net_amount, first.vat_amount) == (
        Decimal("1230.0"),
        Decimal("1000.0"),
        Decimal("230.0"),
    )


@pytest.mark.parametrize(
    ("answered", "expected"),
    [
        # The value is the SDK's own: `ksef2` translates `Kor` to `kor` before
        # the record gets here (`infra.mappers.invoices.responses._map_invoice_type`).
        ("kor", DocumentType.KOR),
        ("vat", DocumentType.VAT),
        # The table of types has grown before and will grow again. A month
        # that trips over the thirteenth type turns a missing warning into a
        # missing statement.
        ("kor_czegos_nowego", DocumentType.UNKNOWN),
    ],
)
def test_the_document_type_arrives_so_a_correction_can_be_told_apart(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
    answered: str,
    expected: DocumentType,
) -> None:
    Plan(page=[sdk_metadata(1, invoice_type=answered)]).install(monkeypatch)

    with port.session(nip=NIP, token=TOKEN) as opened:
        page = opened.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert page.invoices[0].document_type is expected


def test_the_content_hashes_arrive_so_a_correction_can_name_its_original(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
) -> None:
    # The names are ours; the wire ones stay on the other side of the port
    # (D-017): `invoice_hash` and `hash_of_corrected_invoice` in the SDK,
    # `content_hash` and `corrected_content_hash` on ours (ADR-111).
    Plan(
        page=[sdk_metadata(2, invoice_type="kor", hash_of_corrected_invoice="cGllcndvdG5h")]
    ).install(monkeypatch)

    with port.session(nip=NIP, token=TOKEN) as opened:
        page = opened.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    first = page.invoices[0]

    assert (first.content_hash, first.corrected_content_hash) == (
        synthetic_content_hash(2),
        "cGllcndvdG5h",
    )


def test_a_document_correcting_nothing_names_no_original(
    session: KsefSession,
) -> None:
    page = session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert page.invoices[0].corrected_content_hash is None


def test_a_page_carries_the_high_water_mark_the_next_window_starts_from(
    session: KsefSession,
) -> None:
    page = session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert page.hwm_date == HWM


@pytest.fixture
def paged(monkeypatch: pytest.MonkeyPatch) -> Plan:
    """A window KSeF hands over in two pages, the way a busy month comes back."""
    prepared = Plan(
        further={
            1: FakeMetadataPage(
                invoices=[sdk_metadata(3)],
                has_more=False,
                is_truncated=False,
                permanent_storage_hwm_date=HWM,
            )
        }
    )
    prepared.install(monkeypatch)
    return prepared


@pytest.fixture
def paged_session(paged: Plan, port: Ksef2Port) -> Iterator[KsefSession]:
    with port.session(nip=NIP, token=TOKEN) as opened:
        yield opened


@pytest.fixture
def stopped(monkeypatch: pytest.MonkeyPatch) -> Plan:
    """A package MF stopped at the point of completeness, not a full window."""
    prepared = Plan(
        further={
            0: FakeMetadataPage(
                invoices=[sdk_metadata(1)],
                has_more=False,
                is_truncated=True,
                permanent_storage_hwm_date=HWM,
            )
        }
    )
    prepared.install(monkeypatch)
    return prepared


@pytest.fixture
def stopped_session(stopped: Plan, port: Ksef2Port) -> Iterator[KsefSession]:
    with port.session(nip=NIP, token=TOKEN) as opened:
        yield opened


def test_the_first_page_is_asked_for_by_the_number_zero(session: KsefSession, plan: Plan) -> None:
    session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert plan.service.metadata_calls[0][1].page_offset == 0


def test_a_first_page_reports_the_continuation_behind_it(paged_session: KsefSession) -> None:
    page = paged_session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert page.has_more is True


def test_the_page_number_asked_for_reaches_the_sdk(paged_session: KsefSession, paged: Plan) -> None:
    paged_session.query_metadata(
        period=WINDOW,
        subject_role=SubjectRole.BUYER,
        page_offset=1,
    )

    assert paged.service.metadata_calls[0][1].page_offset == 1


def test_a_page_says_which_number_it_came_back_from(paged_session: KsefSession) -> None:
    page = paged_session.query_metadata(
        period=WINDOW,
        subject_role=SubjectRole.BUYER,
        page_offset=1,
    )

    assert page.page_offset == 1


def test_the_page_behind_the_first_brings_what_the_first_left_out(
    paged_session: KsefSession,
) -> None:
    page = paged_session.query_metadata(
        period=WINDOW,
        subject_role=SubjectRole.BUYER,
        page_offset=1,
    )

    assert str(page.invoices[0].ksef_number).endswith("03-56")


def test_the_last_page_of_a_window_stops_reporting_more(paged_session: KsefSession) -> None:
    page = paged_session.query_metadata(
        period=WINDOW,
        subject_role=SubjectRole.BUYER,
        page_offset=1,
    )

    assert page.has_more is False


def test_a_further_page_is_still_asked_for_at_the_ceiling_size_and_newest_first(
    paged_session: KsefSession, paged: Plan
) -> None:
    paged_session.query_metadata(
        period=WINDOW,
        subject_role=SubjectRole.BUYER,
        page_offset=1,
    )
    sent = paged.service.metadata_calls[0][1]

    assert (sent.page_size, sent.sort_order) == (PAGE_SIZE, "desc")


def test_a_package_ksef_cut_short_arrives_marked_as_cut_short(
    stopped_session: KsefSession,
) -> None:
    page = stopped_session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert page.truncated is True


def test_the_limits_the_budget_spends_against_come_from_ksef(session: KsefSession) -> None:
    limits = session.read_limits()

    assert (
        limits.rates.metadata_queries.per_hour,
        limits.rates.exports.per_hour,
        limits.rates.export_statuses.per_hour,
        limits.rates.invoice_downloads.per_hour,
    ) == (20, 20, 200, 64)


def test_the_session_ceilings_come_from_ksef_too(session: KsefSession) -> None:
    limits = session.read_limits()

    assert (
        limits.ceilings.max_invoice_megabytes,
        limits.ceilings.max_invoice_with_attachment_megabytes,
        limits.ceilings.max_invoices_per_session,
    ) == (1, 3, 10_000)


@pytest.fixture
def unreadable_limits(monkeypatch: pytest.MonkeyPatch, port: Ksef2Port) -> Iterator[KsefSession]:
    prepared = Plan(limits=FakeUnparsableLimitsClient())
    prepared.install(monkeypatch)
    with port.session(nip=NIP, token=TOKEN) as opened:
        yield opened


@pytest.fixture
def unreadable_rates(monkeypatch: pytest.MonkeyPatch, port: Ksef2Port) -> Iterator[KsefSession]:
    prepared = Plan(limits=FakeUnparsableLimitsClient(ceilings_unparsable=False))
    prepared.install(monkeypatch)
    with port.session(nip=NIP, token=TOKEN) as opened:
        yield opened


@pytest.fixture
def unreadable_ceilings(monkeypatch: pytest.MonkeyPatch, port: Ksef2Port) -> Iterator[KsefSession]:
    prepared = Plan(limits=FakeUnparsableLimitsClient(rates_unparsable=False))
    prepared.install(monkeypatch)
    with port.session(nip=NIP, token=TOKEN) as opened:
        yield opened


def test_limits_we_cannot_parse_do_not_take_the_call_down(
    unreadable_limits: KsefSession,
) -> None:
    limits = unreadable_limits.read_limits()

    assert limits.degraded


def test_unparsable_rates_fall_back_to_the_conservative_allowance(
    unreadable_rates: KsefSession,
) -> None:
    limits = unreadable_rates.read_limits()

    assert limits.rates == CONSERVATIVE_RATES


def test_unparsable_ceilings_fall_back_to_the_conservative_session(
    unreadable_ceilings: KsefSession,
) -> None:
    limits = unreadable_ceilings.read_limits()

    assert limits.ceilings == CONSERVATIVE_CEILINGS


def test_the_rejected_ceilings_payload_is_named_in_the_journal(
    unreadable_ceilings: KsefSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The point of GH-253: `assumed=true` on every production run told the
    # operator nothing about why, and the journal is where the why belongs.
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        unreadable_ceilings.read_limits()

    assert "collectiveIdentifier: Field required" in caplog.text


def test_one_unreadable_read_does_not_discard_the_other(
    unreadable_ceilings: KsefSession,
) -> None:
    limits = unreadable_ceilings.read_limits()

    assert limits.rates.metadata_queries.per_hour == 20


def test_a_degraded_allowance_still_runs_out(unreadable_limits: KsefSession) -> None:
    # The point of the fallback. `None` would read as "no ceiling" and let the
    # counter wave every call through — the pattern the Ministry blocks for.
    # Which of the three windows stops it first is not this test's business;
    # that one of them does is.
    budget = QueryBudget(limits=unreadable_limits.read_limits().rates)

    with pytest.raises(BudgetExhausted):
        for _ in range(21):
            budget.spend(Operation.METADATA_QUERY)


def test_a_refusal_is_not_mistaken_for_an_unreadable_limit(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
) -> None:
    # The fallback is narrow on purpose: only an unparsable payload survives.
    # An error KSeF actually reported still reaches the caller.
    prepared = Plan(limits=RefusingLimitsClient())
    prepared.install(monkeypatch)

    with port.session(nip=NIP, token=TOKEN) as opened, pytest.raises(KsefRefused):
        opened.read_limits()


def test_an_export_keeps_the_key_it_was_minted_with(session: KsefSession) -> None:
    handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert (handle.reference, handle.encryption.key, handle.encryption.initialisation_vector) == (
        "EXP-1",
        b"k" * 32,
        b"i" * 16,
    )


def test_a_ready_export_reports_where_its_parts_live(session: KsefSession) -> None:
    handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)

    status = session.check_export(handle=handle)

    assert (status.state, len(status.parts), status.parts[0].url) == (
        ExportState.READY,
        2,
        "https://storage.example/part/1",
    )


def test_a_truncated_export_names_the_date_the_next_window_starts_from(
    session: KsefSession,
) -> None:
    handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)

    status = session.check_export(handle=handle)

    assert (status.truncated, status.last_permanent_storage_date, status.hwm_date) == (
        True,
        HWM - timedelta(days=1),
        HWM,
    )


EMPTY_PACKAGE = FakeInvoicePackage(
    invoice_count=0,
    parts=[],
    is_truncated=False,
    last_permanent_storage_date=None,
    permanent_storage_hwm_date=None,
)

COMPLETED = datetime(2026, 9, 12, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("code", "completed_date", "package", "expected"),
    [
        (100, None, None, ExportState.RUNNING),
        (415, None, None, ExportState.FAILED),
        # A refusal stays a refusal even once KSeF has stamped it finished.
        (415, COMPLETED, None, ExportState.FAILED),
        # The two shapes a closed empty window arrives in: no package at all,
        # and a package whose part list KSeF left empty (GH-190).
        (200, COMPLETED, None, ExportState.EMPTY),
        (200, COMPLETED, EMPTY_PACKAGE, ExportState.EMPTY),
        # The one that used to be indistinguishable from the two above, and
        # still must be: nothing built yet, and KSeF has not said otherwise.
        (200, None, EMPTY_PACKAGE, ExportState.RUNNING),
    ],
)
def test_an_export_without_parts_is_read_off_the_date_ksef_closed_it_on(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
    code: int,
    completed_date: datetime | None,
    package: FakeInvoicePackage | None,
    expected: ExportState,
) -> None:
    plan = Plan(
        status=FakeExportStatusResponse(
            status=FakeExportStatusInfo(code=code, description="—"),
            package=package,
            completed_date=completed_date,
        )
    )
    plan.install(monkeypatch)

    with port.session(nip=NIP, token=TOKEN) as session:
        handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)
        status = session.check_export(handle=handle)

    assert status.state is expected


def test_an_invoice_is_downloaded_as_the_bytes_ksef_holds(
    session: KsefSession,
    plan: Plan,
) -> None:
    page = session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    body = session.download_invoice(ksef_number=page.invoices[0].ksef_number)

    assert (body, plan.service.downloaded) == (
        b"<Faktura/>",
        ["1234567890-20260901-0100AB12CD01-56"],
    )


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (KSeFRateLimitError(120, "za dużo zapytań"), KsefRateLimited),
        (KSeFAuthError(401, "brak uprawnień"), KsefAuthenticationFailed),
        (KSeFSessionError("sesja padła"), KsefRefused),
        (httpx.ConnectError("brak sieci"), KsefUnreachable),
    ],
)
def test_every_failure_family_leaves_as_one_hierarchy(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
    raised: Exception,
    expected: type[Exception],
) -> None:
    Plan(authentication_error=raised).install(monkeypatch)

    with pytest.raises(expected), port.session(nip=NIP, token=TOKEN):
        pass


def test_a_refusal_carries_the_wait_ksef_asked_for(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
) -> None:
    Plan(authentication_error=KSeFRateLimitError(120, "za dużo")).install(monkeypatch)

    with pytest.raises(KsefRateLimited) as refusal, port.session(nip=NIP, token=TOKEN):
        pass

    assert refusal.value.retry_after == 120


def test_a_rejected_token_never_names_the_subject_in_the_message(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
) -> None:
    Plan(authentication_error=KSeFAuthError(401, "brak uprawnień")).install(monkeypatch)

    with pytest.raises(KsefAuthenticationFailed) as rejection, port.session(nip=NIP, token=TOKEN):
        pass

    assert NIP not in str(rejection.value)


def test_a_failure_mid_call_is_translated_too(
    monkeypatch: pytest.MonkeyPatch,
    port: Ksef2Port,
) -> None:
    Plan(call_error=KSeFSessionError("sesja padła")).install(monkeypatch)

    with pytest.raises(KsefRefused), port.session(nip=NIP, token=TOKEN) as session:
        session.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)


PART = ExportPart(
    ordinal=1,
    name="package_part_1.zip.aes",
    method="GET",
    url="https://storage.example/part/1",
    size_bytes=1024,
    content_hash="c3BsaXQ=",
    encrypted_size_bytes=1040,
    encrypted_content_hash="ZW5jcnlwdGVk",
)


def answering(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_package_part_arrives_encrypted_and_untouched(
    session: KsefSession,
    plan: Plan,
) -> None:
    session.transport = answering(lambda request: httpx.Response(200, content=b"\x00encrypted"))
    handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert session.fetch_part(handle=handle, part=PART) == b"\x00encrypted"


@pytest.mark.parametrize("status", [403, 410])
def test_an_expired_package_link_says_so_by_type(session: KsefSession, status: int) -> None:
    # Its own type because it is the one refusal waiting does not mend: the
    # caller has to tell it apart to know that asking KSeF again is the move
    # (GH-93).
    session.transport = answering(lambda request: httpx.Response(status))
    handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)

    with pytest.raises(PackageLinkExpired):
        session.fetch_part(handle=handle, part=PART)


def test_storage_refusing_for_another_reason_stays_an_ordinary_refusal(
    session: KsefSession,
) -> None:
    # A 500 from storage is an outage, and an outage does heal by waiting —
    # reading it as an expiry would drop a package that is still there.
    session.transport = answering(lambda request: httpx.Response(500))
    handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)

    with pytest.raises(KsefRefused) as refusal:
        session.fetch_part(handle=handle, part=PART)
    assert isinstance(refusal.value, PackageLinkExpired) is False


def test_storage_that_cannot_be_reached_says_so(session: KsefSession) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("brak sieci")

    session.transport = answering(refuse)
    handle = session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER)

    with pytest.raises(KsefUnreachable):
        session.fetch_part(handle=handle, part=PART)
