"""Czy serwer przestaje pytać, gdy KSeF odmawia raz za razem.

Budżet chroni przed sukcesem zbyt częstym; do GH-99 nic nie chroniło przed
porażką zbyt częstą. A seria odmów to dokładnie wzorzec, który Ministerstwo
Finansów analizuje jako próbę obchodzenia limitów i odpowiada blokadą tym
dłuższą, im częściej się powtarza. Szkodę robi wytrwałość klienta, nie
pojedyncza operacja — więc bezpiecznik musi przeżyć proces.

Tu też domyka się GH-100: `RetryPolicy` miała testy i zero wywołujących w
produkcji. `GuardedSession` jest miejscem, w którym wreszcie działa — z
domyślną `NO_AUTOMATIC_RETRY`, więc ruch do KSeF-u nie zmienia się ani o jedno
żądanie.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from ksef_mcp.ksef_port import (
    DateType,
    ExportHandle,
    ExportPart,
    ExportState,
    ExportStatus,
    GuardedSession,
    KsefAuthenticationFailed,
    KsefLimits,
    KsefNumber,
    KsefRateLimited,
    KsefRefused,
    KsefSession,
    KsefUnreachable,
    MetadataPage,
    Period,
    RefusalBreakerEngaged,
    RetryPolicy,
    SubjectRole,
)

WINDOW = Period(
    date_from=datetime(2026, 9, 1, tzinfo=UTC),
    date_to=datetime(2026, 9, 30, tzinfo=UTC),
    date_type=DateType.ISSUE,
)


@dataclass
class RecordingBreaker:
    """Says what it was told, so the wrapper's bookkeeping is visible."""

    blocked: bool = False
    refusals: list[int | None] = field(default_factory=list)
    successes: int = 0

    def refuse_early(self) -> None:
        if self.blocked:
            raise RefusalBreakerEngaged("Odmawiam lokalnie.")

    def note_refusal(self, *, retry_after: int | None) -> None:
        self.refusals.append(retry_after)

    def note_success(self) -> None:
        self.successes += 1


@dataclass
class ScriptedSession:
    """A session that answers, or raises whatever the test put in front of it."""

    failure: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def _answer(self, name: str) -> None:
        self.calls.append(name)
        if self.failure is not None:
            raise self.failure

    def read_limits(self) -> KsefLimits:
        self._answer("read_limits")
        raise AssertionError("test nie potrzebuje limitów")

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        self._answer("query_metadata")
        return MetadataPage(invoices=(), has_more=False, truncated=False, hwm_date=None)

    def start_export(self, *, period: Period, subject_role: SubjectRole) -> ExportHandle:
        self._answer("start_export")
        return ExportHandle(reference="EXP-1", encryption=None)

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        self._answer("check_export")
        return ExportStatus(
            state=ExportState.READY,
            parts=(),
            truncated=False,
            hwm_date=None,
            last_permanent_storage_date=None,
            invoice_count=0,
        )

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        self._answer("fetch_part")
        return b"\x00encrypted"

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        self._answer("download_invoice")
        return b"<Faktura/>"


HANDLE = ExportHandle(reference="EXP-1", encryption=None)

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


@pytest.fixture
def breaker() -> RecordingBreaker:
    return RecordingBreaker()


@pytest.fixture
def inner() -> ScriptedSession:
    return ScriptedSession()


@pytest.fixture
def guarded(inner: ScriptedSession, breaker: RecordingBreaker) -> GuardedSession:
    return GuardedSession(inner=inner, breaker=breaker)


def test_the_wrapper_is_still_a_session(guarded: GuardedSession) -> None:
    # Podmiana musi być niewidoczna dla wołającego, inaczej opakowanie w
    # czterech serwisach byłoby zmianą ich kontraktu.
    assert isinstance(guarded, KsefSession)


def test_a_call_that_works_reaches_the_session_underneath(
    guarded: GuardedSession, inner: ScriptedSession
) -> None:
    guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert inner.calls == ["query_metadata"]


def test_a_call_that_works_says_the_run_is_over(
    guarded: GuardedSession, breaker: RecordingBreaker
) -> None:
    guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert breaker.successes == 1


@pytest.mark.parametrize(
    "refusal",
    [
        KsefRateLimited("KSeF odmówił: 429.", retry_after=None),
        KsefAuthenticationFailed("KSeF nie uznał tego tokenu."),
        KsefRefused("KSeF odrzucił wywołanie."),
    ],
    ids=["rate-limited", "rejected-credential", "refused"],
)
def test_every_way_ksef_says_no_is_counted(breaker: RecordingBreaker, refusal: Exception) -> None:
    guarded = GuardedSession(inner=ScriptedSession(failure=refusal), breaker=breaker)

    with pytest.raises(type(refusal)):
        guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert len(breaker.refusals) == 1


def test_no_answer_at_all_is_not_a_refusal(breaker: RecordingBreaker) -> None:
    # Brak odpowiedzi to nie odmowa. Liczenie własnego DNS-u jako wzorca
    # obchodzenia limitów zamykałoby podmiot za cudzą awarię.
    guarded = GuardedSession(
        inner=ScriptedSession(failure=KsefUnreachable("Brak odpowiedzi z KSeF-u.")),
        breaker=breaker,
    )

    with pytest.raises(KsefUnreachable):
        guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert breaker.refusals == []


def test_only_ksef_s_own_wait_is_passed_on(breaker: RecordingBreaker) -> None:
    guarded = GuardedSession(
        inner=ScriptedSession(failure=KsefRateLimited("429.", retry_after=90)),
        breaker=breaker,
    )

    with pytest.raises(KsefRateLimited):
        guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert breaker.refusals == [90]


def test_a_refusal_that_names_no_wait_invents_none(breaker: RecordingBreaker) -> None:
    guarded = GuardedSession(
        inner=ScriptedSession(failure=KsefRefused("KSeF odrzucił wywołanie.")),
        breaker=breaker,
    )

    with pytest.raises(KsefRefused):
        guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert breaker.refusals == [None]


def test_a_blocked_breaker_stops_the_call_before_it_is_sent(
    breaker: RecordingBreaker, inner: ScriptedSession
) -> None:
    breaker.blocked = True
    guarded = GuardedSession(inner=inner, breaker=breaker)

    with pytest.raises(RefusalBreakerEngaged):
        guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert inner.calls == []


def test_a_package_part_is_delegated_without_the_fuse(
    guarded: GuardedSession, breaker: RecordingBreaker, inner: ScriptedSession
) -> None:
    # Część paczki idzie z zewnętrznego magazynu po podpisanym URL-u, bez
    # poświadczeń KSeF-u i poza jakimkolwiek limitem. Wygasły odnośnik nic nie
    # mówi o tym, jak często ten podmiot pyta KSeF.
    guarded.fetch_part(handle=HANDLE, part=PART)

    assert (inner.calls, breaker.successes) == (["fetch_part"], 0)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            lambda session: session.start_export(period=WINDOW, subject_role=SubjectRole.BUYER),
            "start_export",
        ),
        (lambda session: session.check_export(handle=HANDLE), "check_export"),
        (
            lambda session: session.download_invoice(
                ksef_number=KsefNumber("1234567890-20260901-0100AB12CD01-56")
            ),
            "download_invoice",
        ),
    ],
    ids=["start_export", "check_export", "download_invoice"],
)
def test_every_call_ksef_answers_passes_the_fuse(
    guarded: GuardedSession,
    inner: ScriptedSession,
    breaker: RecordingBreaker,
    call: object,
    expected: str,
) -> None:
    call(guarded)  # type: ignore[operator]

    assert (inner.calls, breaker.successes) == ([expected], 1)


def test_the_limits_read_passes_the_fuse_too(breaker: RecordingBreaker) -> None:
    # Dwa żądania na każde otwarcie sesji. Gdyby szły obok bezpiecznika,
    # zablokowany podmiot i tak pukałby do KSeF-u przy każdym uruchomieniu.
    breaker.blocked = True
    inner = ScriptedSession()
    guarded = GuardedSession(inner=inner, breaker=breaker)

    with pytest.raises(RefusalBreakerEngaged):
        guarded.read_limits()

    assert inner.calls == []


def test_the_default_policy_sends_one_attempt_and_no_more(
    guarded: GuardedSession,
) -> None:
    # GH-100 bez zmiany zachowania: `RetryPolicy` jest wreszcie podłączona, a
    # domyślna to wciąż jedna próba. Żadnego wycofania wykładniczego (D-017).
    assert guarded.retry.attempts == 1


def test_a_policy_that_waits_uses_only_the_wait_ksef_named(
    breaker: RecordingBreaker,
) -> None:
    naps: list[float] = []
    guarded = GuardedSession(
        inner=ScriptedSession(failure=KsefRateLimited("429.", retry_after=30)),
        breaker=breaker,
        retry=RetryPolicy(attempts=2, max_wait=timedelta(minutes=10), sleep=naps.append),
    )

    with pytest.raises(KsefRateLimited):
        guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert naps == [30]


def test_a_policy_that_waits_still_counts_the_refusal_once(
    breaker: RecordingBreaker,
) -> None:
    # Bezpiecznik widzi odmowę, która dotarła do wołającego — nie każdą próbę
    # w środku. Inaczej jedna polityka z trzema próbami przepalałaby bezpiecznik
    # szybciej niż trzy osobne wywołania.
    guarded = GuardedSession(
        inner=ScriptedSession(failure=KsefRateLimited("429.", retry_after=30)),
        breaker=breaker,
        retry=RetryPolicy(attempts=2, max_wait=timedelta(minutes=10), sleep=lambda _: None),
    )

    with pytest.raises(KsefRateLimited):
        guarded.query_metadata(period=WINDOW, subject_role=SubjectRole.BUYER)

    assert len(breaker.refusals) == 1
