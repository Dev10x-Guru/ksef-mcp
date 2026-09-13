"""The acceptance criterion of GH-33: proving the token works must stay cheap.

The hourly allowance is twenty metadata queries and the Ministry logs breaches,
so „one authentication plus one query" is a property of the code, not a promise
in the issue. A port that counts what it was asked to do makes it checkable.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    ConnectionCheck,
    InvoiceDirection,
    InvoiceMetadata,
    MetadataPage,
    Period,
    check_connection,
)
from ksef_mcp.ksef_port.connection import DEFAULT_LIMIT
from synthetic import BUYER_NAME, synthetic_metadata

NIP = "1234567890"

TOKEN = "aaaabbbbccccdddd"


@dataclass
class CountingSession:
    invoices: list[InvoiceMetadata]
    queries: list[tuple[Period, InvoiceDirection]] = field(default_factory=list)

    def query_metadata(self, *, period: Period, direction: InvoiceDirection) -> MetadataPage:
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
    sessions: list[CountingSession] = field(default_factory=list)

    @contextmanager
    def session(self, *, nip: str, token: str) -> Iterator[CountingSession]:
        opened = CountingSession(invoices=list(self.invoices))
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
def checked(port: CountingPort) -> ConnectionCheck:
    return check_connection(port=port, nip=NIP, token=TOKEN)


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


def test_the_check_stamps_the_environment_on_its_answer(port: CountingPort) -> None:
    checked = check_connection(port=port, nip=NIP, token=TOKEN)

    assert checked.environment is KsefEnvironment.TEST


def test_the_subject_name_comes_from_the_buyer(port: CountingPort) -> None:
    checked = check_connection(port=port, nip=NIP, token=TOKEN)

    assert checked.subject_name == BUYER_NAME


def test_an_empty_window_leaves_the_subject_unnamed() -> None:
    empty = CountingPort(environment=KsefEnvironment.DEMO)

    checked = check_connection(port=empty, nip=NIP, token=TOKEN)

    assert (checked.subject_name, checked.invoices) == (None, ())


def test_the_answer_is_trimmed_to_the_limit(port: CountingPort) -> None:
    # The query is paid for whole; what the command prints is a separate
    # question, and a wall of invoices is not a proof of connection.
    checked = check_connection(port=port, nip=NIP, token=TOKEN, limit=2)

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


def test_the_window_opens_no_earlier_than_the_one_asked_for(port: CountingPort) -> None:
    before = datetime.now(tz=UTC)

    check_connection(port=port, nip=NIP, token=TOKEN, window=timedelta(days=1))

    period, _ = port.queries[0]
    assert period.date_from >= before - timedelta(days=1)
