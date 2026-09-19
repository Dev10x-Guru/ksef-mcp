"""The acceptance criterion of GH-33: proving the token works must stay cheap.

The hourly allowance is twenty metadata queries and the Ministry logs breaches,
so „one authentication plus one query" is a property of the code, not a promise
in the issue. A port that counts what it was asked to do makes it checkable.

Od GH-98 „tanio" znaczy też „policzone". `ksef-mcp verify` bywa wołane kilka
razy pod rząd właśnie wtedy, gdy coś już szwankuje, a szło obok licznika i obok
cache'u — dokładany ruch trafiał do tego samego budżetu, który chroni przed
blokadą.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from conftest import an_allowance
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    ConnectionCheck,
    InvoiceDirection,
    InvoiceMetadata,
    KsefLimits,
    KsefRequestRejected,
    MetadataPage,
    Operation,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
    check_connection,
)
from ksef_mcp.ksef_port.connection import DEFAULT_LIMIT, check_period
from ksef_mcp.period_cache import MeteredPeriods, PeriodCache
from synthetic import BUYER_NAME, synthetic_credential, synthetic_metadata

NIP = "1234567890"

TOKEN = synthetic_credential("aaaabbbbccccdddd")

ASKED_AT = datetime(2026, 9, 14, 7, 41, 17, tzinfo=UTC)

GENEROUS: Final[OperationLimit] = OperationLimit(per_second=None, per_minute=None, per_hour=20)


def limits(metadata: OperationLimit = GENEROUS) -> KsefLimits:
    return KsefLimits(
        rates=RateLimits(
            metadata_queries=metadata,
            exports=GENEROUS,
            export_statuses=GENEROUS,
            invoice_downloads=GENEROUS,
        ),
        ceilings=SessionCeilings(
            max_invoice_megabytes=1,
            max_invoice_with_attachment_megabytes=3,
            max_invoices_per_session=10_000,
        ),
    )


@dataclass
class CountingSession:
    invoices: list[InvoiceMetadata]
    allowance: OperationLimit = GENEROUS
    queries: list[tuple[Period, InvoiceDirection]] = field(default_factory=list)

    def read_limits(self) -> KsefLimits:
        return limits(self.allowance)

    def query_metadata(
        self,
        *,
        period: Period,
        direction: InvoiceDirection,
        page_offset: int = 0,
    ) -> MetadataPage:
        self.queries.append((period, direction))
        return MetadataPage(
            invoices=tuple(self.invoices),
            has_more=False,
            truncated=False,
            hwm_date=None,
        )


@dataclass
class CountingPort:
    environment: KsefEnvironment
    invoices: list[InvoiceMetadata] = field(default_factory=list)
    allowance: OperationLimit = GENEROUS
    sessions: list[CountingSession] = field(default_factory=list)

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[CountingSession]:
        opened = CountingSession(invoices=list(self.invoices), allowance=self.allowance)
        self.sessions.append(opened)
        yield opened

    @property
    def queries(self) -> list[tuple[Period, InvoiceDirection]]:
        return [query for session in self.sessions for query in session.queries]


@pytest.fixture
def port() -> CountingPort:
    return CountingPort(
        environment=KsefEnvironment.TEST,
        invoices=[synthetic_metadata(ordinal) for ordinal in range(1, 8)],
    )


@pytest.fixture
def readers(tmp_path: Path) -> MeteredPeriods:
    """The real pairing, not a stub: what GH-98 changed is that this is required."""
    return MeteredPeriods(
        cache=PeriodCache(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            root=tmp_path / "cache",
            clock=lambda: ASKED_AT,
        ),
        allowance=an_allowance(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            root=tmp_path,
            clock=lambda: ASKED_AT,
        ),
    )


@pytest.fixture
def checked(port: CountingPort, readers: MeteredPeriods) -> ConnectionCheck:
    return check_connection(port=port, nip=NIP, token=TOKEN, readers=readers)


def test_the_check_authenticates_exactly_once(
    checked: ConnectionCheck,
    port: CountingPort,
) -> None:
    assert len(port.sessions) == 1


def test_the_check_spends_exactly_one_metadata_query(
    checked: ConnectionCheck,
    port: CountingPort,
) -> None:
    assert len(port.queries) == 1


def test_the_check_stamps_the_environment_on_its_answer(
    port: CountingPort, readers: MeteredPeriods
) -> None:
    checked = check_connection(port=port, nip=NIP, token=TOKEN, readers=readers)

    assert checked.environment is KsefEnvironment.TEST


def test_the_subject_name_comes_from_the_buyer(port: CountingPort, readers: MeteredPeriods) -> None:
    checked = check_connection(port=port, nip=NIP, token=TOKEN, readers=readers)

    assert checked.subject_name == BUYER_NAME


def test_an_empty_window_leaves_the_subject_unnamed(readers: MeteredPeriods) -> None:
    empty = CountingPort(environment=KsefEnvironment.DEMO)

    checked = check_connection(port=empty, nip=NIP, token=TOKEN, readers=readers)

    assert (checked.subject_name, checked.invoices) == (None, ())


def test_the_answer_is_trimmed_to_the_limit(port: CountingPort, readers: MeteredPeriods) -> None:
    # The query is paid for whole; what the command prints is a separate
    # question, and a wall of invoices is not a proof of connection.
    checked = check_connection(port=port, nip=NIP, token=TOKEN, readers=readers, limit=2)

    assert len(checked.invoices) == 2


def test_the_default_answer_stays_short(checked: ConnectionCheck, port: CountingPort) -> None:
    assert len(checked.invoices) == DEFAULT_LIMIT


def test_the_query_asks_about_the_subject_as_a_buyer(
    checked: ConnectionCheck,
    port: CountingPort,
) -> None:
    # `buyer.name` is where the company name lives, and the direction decides
    # whose name comes back — asking as a seller would greet the counterparty.
    _, direction = port.queries[0]
    assert direction is InvoiceDirection.BUYER


def test_the_window_opens_no_earlier_than_the_one_asked_for(
    port: CountingPort, readers: MeteredPeriods
) -> None:
    before = datetime.now(tz=UTC)

    check_connection(
        port=port,
        nip=NIP,
        token=TOKEN,
        readers=readers,
        window=timedelta(days=1),
    )

    period, _ = port.queries[0]
    # Span rather than start: rounding the end down to the hour moves both ends
    # together, so what a caller's `window` still buys is a window exactly that
    # wide — which is the half the query ceiling is measured against (GH-84).
    assert (period.date_to - period.date_from, period.date_to <= before) == (
        timedelta(days=1),
        True,
    )


def test_the_query_is_charged_to_the_counter_that_guards_the_allowance(
    checked: ConnectionCheck, readers: MeteredPeriods
) -> None:
    # GH-98. `verify` used to reach `query_metadata` straight through, so the
    # diagnostic tool added uncounted traffic to the very budget meant to keep
    # the subject out of a block.
    assert readers.allowance.ledger.load() == {Operation.METADATA_QUERY: (ASKED_AT,)}


def test_a_second_verify_in_the_same_hour_asks_ksef_nothing(
    port: CountingPort, readers: MeteredPeriods
) -> None:
    # Somebody diagnosing a connection runs `verify` several times in a row.
    # That used to cost one of twenty metadata queries an hour every time, and
    # the command exists to be run when the allowance is already in question.
    check_connection(port=port, nip=NIP, token=TOKEN, readers=readers)
    check_connection(port=port, nip=NIP, token=TOKEN, readers=readers)

    assert len(port.queries) == 1


def test_the_window_ends_on_the_hour_so_the_cache_has_an_identity_to_match() -> None:
    # An end taken raw from the wall clock differs by microseconds between two
    # runs, so no repeat would ever match the entry the first one wrote.
    assert check_period(moment=ASKED_AT).date_to == datetime(2026, 9, 14, 7, tzinfo=UTC)


def test_a_spent_allowance_refuses_the_check_rather_than_letting_ksef_refuse_it(
    readers: MeteredPeriods,
) -> None:
    spent = CountingPort(
        environment=KsefEnvironment.TEST,
        allowance=OperationLimit(per_second=None, per_minute=None, per_hour=0),
    )

    with pytest.raises(KsefRequestRejected):
        check_connection(port=spent, nip=NIP, token=TOKEN, readers=readers)
