from dataclasses import dataclass, field
from datetime import date

import httpx
import pytest
from ksef2 import Environment
from ksef2.core.exceptions import KSeFAuthError, KSeFRateLimitError, KSeFSessionError
from ksef2.domain.models.invoices import (
    InvoiceMetadata,
    InvoiceMetadataBuyer,
    InvoiceMetadataSeller,
)

from ksef_mcp import ksef_port
from ksef_mcp.config import KsefEnvironment

NIP = "1234567890"

TOKEN = "aaaabbbbccccdddd"


@dataclass
class FakeSeller:
    nip: str
    name: str | None


@dataclass
class FakeBuyer:
    name: str | None


@dataclass
class FakeMetadata:
    ksef_number: str
    issue_date: date
    seller: FakeSeller
    buyer: FakeBuyer
    gross_amount: float
    currency: str


def metadata(number: str, *, seller_name: str | None = "Dostawca sp. z o.o.") -> FakeMetadata:
    return FakeMetadata(
        ksef_number=number,
        issue_date=date(2026, 9, 1),
        seller=FakeSeller(nip="9876543210", name=seller_name),
        buyer=FakeBuyer(name="Moja Firma sp. z o.o."),
        gross_amount=1230.0,
        currency="PLN",
    )


@dataclass
class FakePage:
    invoices: list[FakeMetadata]


class FakeInvoices:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.calls: list[object] = []

    def query_metadata(self, *, filters: object, params: object) -> FakePage:
        self.calls.append(params)
        return self.page


class FakeAuthenticated:
    def __init__(self, invoices: FakeInvoices) -> None:
        self.invoices = invoices


class FakeAuthentication:
    def __init__(self, authenticated: FakeAuthenticated, error: Exception | None) -> None:
        self.authenticated = authenticated
        self.error = error
        self.credentials: list[tuple[str, str]] = []

    def with_token(self, *, ksef_token: str, nip: str) -> FakeAuthenticated:
        self.credentials.append((nip, ksef_token))
        if self.error is not None:
            raise self.error
        return self.authenticated


class FakeClient:
    def __init__(
        self,
        *,
        environment: Environment,
        transport_config: object,
        authentication: FakeAuthentication,
    ) -> None:
        self.environment = environment
        self.transport_config = transport_config
        self.authentication = authentication
        self.closed = False

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True


@dataclass
class Plan:
    page: list[FakeMetadata]
    error: Exception | None = None
    built: list[FakeClient] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def build(*, environment: Environment, transport_config: object) -> FakeClient:
            client = FakeClient(
                environment=environment,
                transport_config=transport_config,
                authentication=FakeAuthentication(
                    FakeAuthenticated(FakeInvoices(FakePage(invoices=list(self.page)))),
                    self.error,
                ),
            )
            self.built.append(client)
            return client

        monkeypatch.setattr(ksef_port, "Client", build)


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> Plan:
    prepared = Plan(page=[metadata(f"KSEF-{index}") for index in range(1, 8)])
    prepared.install(monkeypatch)
    return prepared


@pytest.fixture
def checked(plan: Plan) -> ksef_port.ConnectionCheck:
    return ksef_port.check_connection(
        nip=NIP,
        token=TOKEN,
        environment=KsefEnvironment.TEST,
    )


@pytest.fixture
def sent_params(checked: ksef_port.ConnectionCheck, plan: Plan) -> object:
    return plan.built[0].authentication.authenticated.invoices.calls[0]


@pytest.mark.parametrize(
    ("fake", "real"),
    [
        (FakeMetadata, InvoiceMetadata),
        (FakeSeller, InvoiceMetadataSeller),
        (FakeBuyer, InvoiceMetadataBuyer),
    ],
)
def test_the_doubles_still_match_the_sdk_models(fake: type, real: type) -> None:
    # Without this the suite stays green when ksef2 renames a field, and the
    # rename surfaces as an AttributeError on the user's machine instead.
    assert set(fake.__annotations__) <= set(real.model_fields)


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        (KsefEnvironment.TEST, Environment.TEST),
        (KsefEnvironment.DEMO, Environment.DEMO),
        (KsefEnvironment.PRODUCTION, Environment.PRODUCTION),
    ],
)
def test_environment_is_always_passed_explicitly(
    plan: Plan,
    chosen: KsefEnvironment,
    expected: Environment,
) -> None:
    ksef_port.check_connection(nip=NIP, token=TOKEN, environment=chosen)

    assert plan.built[0].environment is expected


def test_the_sdk_retry_loop_is_disabled(
    checked: ksef_port.ConnectionCheck,
    plan: Plan,
) -> None:
    assert plan.built[0].transport_config.retry.max_attempts == 1


def test_the_client_is_closed(checked: ksef_port.ConnectionCheck, plan: Plan) -> None:
    assert plan.built[0].closed is True


def test_the_token_reaches_authentication(
    checked: ksef_port.ConnectionCheck,
    plan: Plan,
) -> None:
    assert plan.built[0].authentication.credentials == [(NIP, TOKEN)]


def test_newest_invoices_are_requested_first(sent_params: object) -> None:
    assert sent_params.sort_order == "desc"


def test_the_page_size_respects_the_api_floor(sent_params: object) -> None:
    assert sent_params.page_size == ksef_port.PAGE_SIZE


def test_only_the_requested_number_of_invoices_is_returned(
    checked: ksef_port.ConnectionCheck,
) -> None:
    assert len(checked.invoices) == ksef_port.DEFAULT_LIMIT


def test_the_subject_name_comes_from_the_buyer(checked: ksef_port.ConnectionCheck) -> None:
    assert checked.subject_name == "Moja Firma sp. z o.o."


def test_an_invoice_is_reduced_to_metadata(checked: ksef_port.ConnectionCheck) -> None:
    assert checked.invoices[0] == ksef_port.InvoiceSummary(
        ksef_number="KSEF-1",
        issue_date=date(2026, 9, 1),
        seller_name="Dostawca sp. z o.o.",
        seller_nip="9876543210",
        gross_amount=1230.0,
        currency="PLN",
    )


def test_an_empty_window_yields_no_subject_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Plan(page=[]).install(monkeypatch)

    checked = ksef_port.check_connection(
        nip=NIP,
        token=TOKEN,
        environment=KsefEnvironment.TEST,
    )

    assert (checked.subject_name, checked.invoices) == (None, ())


def test_a_rate_limit_carries_the_wait_from_ksef(monkeypatch: pytest.MonkeyPatch) -> None:
    Plan(page=[], error=KSeFRateLimitError(120, "za dużo zapytań")).install(monkeypatch)

    with pytest.raises(ksef_port.KsefRateLimited) as refusal:
        ksef_port.check_connection(nip=NIP, token=TOKEN, environment=KsefEnvironment.TEST)

    assert refusal.value.retry_after == 120


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (KSeFAuthError(401, "brak uprawnień"), ksef_port.KsefAuthenticationFailed),
        (KSeFSessionError("sesja padła"), ksef_port.KsefUnreachable),
        (httpx.ConnectError("brak sieci"), ksef_port.KsefUnreachable),
    ],
)
def test_failures_are_translated_to_port_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: type[Exception],
) -> None:
    Plan(page=[], error=error).install(monkeypatch)

    with pytest.raises(expected):
        ksef_port.check_connection(nip=NIP, token=TOKEN, environment=KsefEnvironment.TEST)
