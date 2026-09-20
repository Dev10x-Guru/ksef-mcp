"""Czy licznik budżetu i zapamiętane limity przeżywają proces, który je zapisał.

Serwer MCP pod `uvx` ginie razem z sesją agenta, więc okno godzinowe liczone w
pamięci miało cykl życia jednego wywołania narzędzia. Trzy narzędzia w ciągu
minuty wydawały trzy pełne przydziały, a licznik ani razu nie odmówił — czyli
dokładnie przekroczenie, przed którym miał chronić (GH-97).

Rozdział korzeni jest tu sprawdzany osobno, bo to on decyduje, co przetrwa
czyszczarkę dysku: licznik w katalogu danych, zapamiętane limity w cache
(D-032).
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ksef_mcp.allowance import (
    LEDGER_FILE,
    LIMITS_FILE,
    REFUSAL_COOLDOWN,
    REFUSAL_LIMIT,
    REFUSALS_FILE,
    SCHEMA_VERSION,
    Allowance,
    BudgetLedger,
    LimitsCache,
    RefusalRun,
)
from ksef_mcp.config import KsefEnvironment
from ksef_mcp.ksef_port import (
    NO_AUTOMATIC_RETRY,
    ExportHandle,
    ExportPart,
    ExportStatus,
    KsefLimits,
    KsefNumber,
    KsefRequestRejected,
    MetadataPage,
    Operation,
    OperationLimit,
    Period,
    RateLimits,
    SessionCeilings,
    SubjectRole,
)

NIP = "1234567890"

NOON = datetime(2026, 9, 1, 12, tzinfo=UTC)


def granted(*, exports_per_hour: int = 20, degraded: bool = False) -> KsefLimits:
    """A grant whose hourly ceiling is the tightest of the three.

    The narrow windows are stated — `remaining` answers with the tightest of
    them, so leaving them at KSeF's real per-second figures would make every
    assertion below about the burst ceiling rather than about the hour these
    tests are named for. The burst ceiling has its own tests in
    `test_port_budget.py`.
    """
    unhurried = OperationLimit(per_second=1_000, per_minute=1_000, per_hour=None)
    return KsefLimits(
        rates=RateLimits(
            metadata_queries=OperationLimit(per_second=1_000, per_minute=1_000, per_hour=20),
            exports=OperationLimit(
                per_second=1_000,
                per_minute=1_000,
                per_hour=exports_per_hour,
            ),
            export_statuses=unhurried,
            invoice_downloads=unhurried,
        ),
        ceilings=SessionCeilings(
            max_invoice_megabytes=1,
            max_invoice_with_attachment_megabytes=3,
            max_invoices_per_session=10_000,
        ),
        degraded=degraded,
    )


class CountingSession:
    """A session that says how many times its limits were actually asked for."""

    def __init__(self, *, limits: KsefLimits | None = None) -> None:
        self.limits = granted() if limits is None else limits
        self.reads = 0

    def read_limits(self) -> KsefLimits:
        self.reads += 1
        return self.limits

    def query_metadata(
        self,
        *,
        period: Period,
        subject_role: SubjectRole,
        page_offset: int = 0,
    ) -> MetadataPage:
        raise AssertionError("sięgnięto do KSeF")

    def start_export(self, *, period: Period, subject_role: SubjectRole) -> ExportHandle:
        raise AssertionError("sięgnięto do KSeF")

    def check_export(self, *, handle: ExportHandle) -> ExportStatus:
        raise AssertionError("sięgnięto do KSeF")

    def fetch_part(self, *, handle: ExportHandle, part: ExportPart) -> bytes:
        raise AssertionError("sięgnięto do KSeF")

    def download_invoice(self, *, ksef_number: KsefNumber) -> bytes:
        raise AssertionError("sięgnięto do KSeF")


class MovableClock:
    def __init__(self, *, moment: datetime = NOON) -> None:
        self.reading = moment

    def __call__(self) -> datetime:
        return self.reading

    def advance(self, by: timedelta) -> None:
        self.reading += by


@pytest.fixture
def clock() -> MovableClock:
    return MovableClock()


@pytest.fixture
def session() -> CountingSession:
    return CountingSession()


@pytest.fixture
def protection(tmp_path: Path, clock: MovableClock) -> Allowance:
    return Allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        data_root=tmp_path / "dane",
        cache_root=tmp_path / "cache",
        clock=clock,
    )


@pytest.fixture
def ledger(protection: Allowance) -> BudgetLedger:
    return protection.ledger


@pytest.fixture
def limits_cache(protection: Allowance) -> LimitsCache:
    return protection.limits


def test_an_empty_ledger_reports_nothing_spent(ledger: BudgetLedger) -> None:
    assert ledger.load() == {}


def test_what_is_recorded_comes_back(ledger: BudgetLedger) -> None:
    ledger.record({Operation.EXPORT: (NOON,)})

    assert ledger.load() == {Operation.EXPORT: (NOON,)}


def test_a_moment_older_than_the_hour_is_not_carried_forward(
    ledger: BudgetLedger, clock: MovableClock
) -> None:
    ledger.record({Operation.EXPORT: (NOON,)})

    clock.advance(timedelta(hours=1, seconds=1))

    assert ledger.load() == {}


def test_an_operation_whose_moments_have_all_expired_leaves_no_entry(
    ledger: BudgetLedger, clock: MovableClock
) -> None:
    ledger.record({Operation.EXPORT: (NOON,), Operation.METADATA_QUERY: (NOON,)})

    clock.advance(timedelta(hours=2))

    assert ledger.load() == {}


def test_the_ledger_lives_in_the_data_root_where_a_disk_cleaner_will_not_reach_it(
    ledger: BudgetLedger, tmp_path: Path
) -> None:
    # D-032. A counter that guards against a block is not reconstructible by
    # asking again — deleting it hands back an allowance already spent.
    assert ledger.path == tmp_path / "dane" / "subjects" / NIP / "test" / LEDGER_FILE


def test_the_ledger_is_written_where_only_its_owner_can_read_it(ledger: BudgetLedger) -> None:
    ledger.record({Operation.EXPORT: (NOON,)})

    assert ledger.path.stat().st_mode & 0o777 == 0o600


def test_a_document_from_another_schema_is_a_miss_rather_than_a_crash(
    ledger: BudgetLedger,
) -> None:
    ledger.record({Operation.EXPORT: (NOON,)})
    ledger.path.write_text('{"schema_version": 99, "spent": {}}', encoding="utf-8")

    assert ledger.load() == {}


@pytest.mark.parametrize(
    "damage",
    ["nie-jest-json", '{"schema_version": 1}', '{"schema_version": 1, "spent": {"x": ["y"]}}'],
    ids=["truncated", "missing-key", "unreadable-moment"],
)
def test_a_damaged_ledger_shrugs_instead_of_taking_the_server_down(
    ledger: BudgetLedger, damage: str
) -> None:
    # The cost of the shrug is bounded — one hour of undercounting. The cost of
    # the alternative is a server that will not start until somebody finds and
    # deletes a file by hand.
    ledger.record({Operation.EXPORT: (NOON,)})
    ledger.path.write_text(damage, encoding="utf-8")

    assert ledger.load() == {}


def test_a_counter_built_from_the_allowance_starts_where_the_last_process_stopped(
    protection: Allowance, session: CountingSession
) -> None:
    protection.ledger.record({Operation.EXPORT: (NOON,) * 3})

    assert protection.budget(session=session).remaining(Operation.EXPORT) == 17


def test_spending_through_the_counter_reaches_the_ledger(
    protection: Allowance, session: CountingSession
) -> None:
    protection.budget(session=session).spend(Operation.EXPORT)

    assert protection.ledger.load() == {Operation.EXPORT: (NOON,)}


def test_three_tool_calls_in_a_minute_cannot_each_spend_a_full_allowance(
    protection: Allowance, session: CountingSession
) -> None:
    # The whole of GH-97 in one test. Each `budget(...)` stands for a separate
    # process, because that is what it was: `uvx` starts and kills the server
    # per agent session.
    for _ in range(4):
        protection.budget(session=CountingSession(limits=granted(exports_per_hour=4))).spend(
            Operation.EXPORT
        )

    with pytest.raises(KsefRequestRejected):
        protection.budget(session=session).spend(Operation.EXPORT)


def test_two_subjects_do_not_spend_each_other_s_allowance(
    tmp_path: Path, clock: MovableClock, session: CountingSession
) -> None:
    # D-034. An accounting office runs one server against many clients, and a
    # shared counter would refuse one taxpayer's call for another's spending.
    mine = Allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        data_root=tmp_path,
        cache_root=tmp_path,
        clock=clock,
    )
    theirs = Allowance(
        nip="9876543210",
        environment=KsefEnvironment.TEST,
        data_root=tmp_path,
        cache_root=tmp_path,
        clock=clock,
    )
    mine.budget(session=session).spend(Operation.EXPORT)

    assert theirs.budget(session=session).remaining(Operation.EXPORT) == 20


def test_the_same_subject_in_two_environments_keeps_two_counters(
    tmp_path: Path, clock: MovableClock, session: CountingSession
) -> None:
    on_test = Allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        data_root=tmp_path,
        cache_root=tmp_path,
        clock=clock,
    )
    on_demo = Allowance(
        nip=NIP,
        environment=KsefEnvironment.DEMO,
        data_root=tmp_path,
        cache_root=tmp_path,
        clock=clock,
    )
    on_test.budget(session=session).spend(Operation.EXPORT)

    assert on_demo.budget(session=session).remaining(Operation.EXPORT) == 20


def test_nothing_remembered_means_ksef_is_asked(
    protection: Allowance, session: CountingSession
) -> None:
    protection.read_limits(session=session)

    assert session.reads == 1


def test_the_second_session_within_the_hour_asks_nobody(
    protection: Allowance, session: CountingSession
) -> None:
    # Two requests on every session open, counted by no `Operation` — the reads
    # meant to protect the allowance were themselves outside it (GH-98).
    protection.read_limits(session=session)
    protection.read_limits(session=session)

    assert session.reads == 1


def test_a_remembered_answer_is_the_one_ksef_gave(
    protection: Allowance, session: CountingSession
) -> None:
    protection.read_limits(session=session)

    assert protection.read_limits(session=session) == session.limits


def test_an_hour_later_ksef_is_asked_again(
    protection: Allowance, session: CountingSession, clock: MovableClock
) -> None:
    # They change in the scale of weeks and MF raises one on request, so an hour
    # is short enough to notice either.
    protection.read_limits(session=session)

    clock.advance(timedelta(hours=1, seconds=1))
    protection.read_limits(session=session)

    assert session.reads == 2


def test_the_remembered_limits_live_in_the_cache_root(
    limits_cache: LimitsCache, tmp_path: Path
) -> None:
    # The opposite call to the ledger's, by the same rule: losing these costs
    # one re-read, which is exactly what the cache convention promises (D-032).
    assert limits_cache.path == tmp_path / "cache" / "subjects" / NIP / "test" / LIMITS_FILE


def test_an_answer_ksef_could_not_be_parsed_from_is_not_remembered(
    protection: Allowance,
) -> None:
    # `degraded` means the conservative fallback was substituted for a payload
    # the SDK could not read (GH-76). Caching that turns one unlucky response
    # into an hour of counting against numbers KSeF never gave.
    degraded = CountingSession(limits=granted(degraded=True))

    protection.read_limits(session=degraded)
    protection.read_limits(session=degraded)

    assert degraded.reads == 2


def test_a_ceiling_ksef_granted_is_reported_as_granted(
    protection: Allowance, session: CountingSession
) -> None:
    assert protection.reading(session=session).ceilings.assumed is False


def test_a_ceiling_the_fallback_supplied_is_reported_as_assumed(
    protection: Allowance,
) -> None:
    # GH-118: the same `degraded` flag that keeps the answer out of the cache
    # now also reaches the person, instead of lying idle where GH-76 needed it.
    fallen_back = CountingSession(limits=granted(degraded=True))

    assert protection.reading(session=fallen_back).ceilings.assumed is True


def test_the_ceilings_and_the_counter_come_from_one_read(
    protection: Allowance, session: CountingSession
) -> None:
    # Two reads would reach KSeF twice in exactly the situation where a
    # degraded answer is never cached — that is, where it already answers badly.
    read = protection.reading(session=session)

    assert read.ceilings.assumed is False
    assert read.budget is not None
    assert session.reads == 1


@pytest.mark.parametrize(
    "damage",
    [
        "nie-jest-json",
        '{"schema_version": 99}',
        '{"schema_version": 1, "read_at": "2026-09-01T12:00:00+00:00"}',
    ],
    ids=["truncated", "other-schema", "missing-limits"],
)
def test_a_damaged_limits_entry_costs_one_re_read_and_nothing_else(
    protection: Allowance, session: CountingSession, damage: str
) -> None:
    protection.read_limits(session=session)
    protection.limits.path.write_text(damage, encoding="utf-8")

    protection.read_limits(session=session)

    assert session.reads == 2


def test_the_remembered_document_says_which_schema_wrote_it(
    protection: Allowance, session: CountingSession
) -> None:
    protection.read_limits(session=session)

    assert f'"schema_version": {SCHEMA_VERSION}' in protection.limits.path.read_text(
        encoding="utf-8"
    )


def test_the_remembered_limits_are_written_for_their_owner_alone(
    protection: Allowance, session: CountingSession
) -> None:
    protection.read_limits(session=session)

    assert protection.limits.path.stat().st_mode & 0o777 == 0o600


def test_the_default_roots_are_the_platform_ones(clock: MovableClock) -> None:
    # `None` means "wherever platformdirs says", never a hand-assembled path, or
    # Windows and macOS inherit Linux's layout (D-032).
    everyday = Allowance(nip=NIP, environment=KsefEnvironment.TEST, clock=clock)

    assert (everyday.ledger.root, everyday.limits.root) == (None, None)


def test_a_fresh_fuse_lets_the_call_through(protection: Allowance) -> None:
    protection.breaker.refuse_early()


def test_a_few_refusals_are_not_yet_a_pattern(protection: Allowance) -> None:
    # Jeden pechowy telefon albo wygasły token, zauważony i poprawiony, nie może
    # zamykać podmiotu na godzinę.
    for _ in range(REFUSAL_LIMIT - 1):
        protection.breaker.note_refusal(retry_after=None)

    protection.breaker.refuse_early()


def test_a_run_of_refusals_stops_the_next_call_locally(protection: Allowance) -> None:
    for _ in range(REFUSAL_LIMIT):
        protection.breaker.note_refusal(retry_after=None)

    with pytest.raises(KsefRequestRejected):
        protection.breaker.refuse_early()


def test_the_local_refusal_says_from_when_asking_is_allowed_again(
    protection: Allowance,
) -> None:
    # „Spróbuj później" bez momentu zamienia bezpiecznik w zagadkę.
    for _ in range(REFUSAL_LIMIT):
        protection.breaker.note_refusal(retry_after=None)

    with pytest.raises(
        KsefRequestRejected,
        match=re.escape((NOON + REFUSAL_COOLDOWN).isoformat()),
    ):
        protection.breaker.refuse_early()


def test_one_answer_ends_the_run(protection: Allowance) -> None:
    for _ in range(REFUSAL_LIMIT - 1):
        protection.breaker.note_refusal(retry_after=None)

    protection.breaker.note_success()
    protection.breaker.note_refusal(retry_after=None)

    protection.breaker.refuse_early()


def test_a_healthy_pass_writes_no_fuse_at_all(protection: Allowance) -> None:
    # Każde udane wywołanie zapisujące plik byłoby zapisem na dysk na każde
    # zapytanie do KSeF-u, żeby skasować zero.
    protection.breaker.note_success()

    assert protection.breaker.path.exists() is False


def test_the_block_lifts_when_the_moment_it_named_has_passed(
    protection: Allowance, clock: MovableClock
) -> None:
    for _ in range(REFUSAL_LIMIT):
        protection.breaker.note_refusal(retry_after=None)

    clock.advance(REFUSAL_COOLDOWN + timedelta(seconds=1))

    protection.breaker.refuse_early()


def test_a_longer_wait_named_by_ksef_is_obeyed(protection: Allowance) -> None:
    # Honorowanie `Retry-After` to wymaganie, nie detal (D-017).
    for _ in range(REFUSAL_LIMIT):
        protection.breaker.note_refusal(retry_after=int(REFUSAL_COOLDOWN.total_seconds()) * 3)

    assert protection.breaker.load().blocked_until == NOON + REFUSAL_COOLDOWN * 3


def test_a_shorter_wait_named_by_ksef_does_not_cut_the_fuse_short(
    protection: Allowance,
) -> None:
    # Bezpiecznik jest zatrzymaniem, nie ponowieniem. Wrócić po trzydziestu
    # sekundach, które bywają w 429, to wznowić dokładnie ten wzorzec.
    for _ in range(REFUSAL_LIMIT):
        protection.breaker.note_refusal(retry_after=30)

    assert protection.breaker.load().blocked_until == NOON + REFUSAL_COOLDOWN


def test_the_fuse_lives_in_the_data_root(protection: Allowance, tmp_path: Path) -> None:
    assert protection.breaker.path == tmp_path / "dane" / "subjects" / NIP / "test" / REFUSALS_FILE


def test_the_fuse_outlives_the_process_that_blew_it(
    protection: Allowance, tmp_path: Path, clock: MovableClock
) -> None:
    # Sedno GH-99. Licznik zerowany przez `uvx` pozwalałby wznawiać serię przy
    # każdym uruchomieniu, a szkodę robi właśnie wytrwałość klienta.
    for _ in range(REFUSAL_LIMIT):
        protection.breaker.note_refusal(retry_after=None)

    next_process = Allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        data_root=tmp_path / "dane",
        cache_root=tmp_path / "cache",
        clock=clock,
    )

    with pytest.raises(KsefRequestRejected):
        next_process.breaker.refuse_early()


@pytest.mark.parametrize(
    "damage",
    ["nie-jest-json", '{"schema_version": 99}', '{"schema_version": 1, "consecutive": 3}'],
    ids=["truncated", "other-schema", "missing-key"],
)
def test_a_damaged_fuse_reads_as_an_intact_one(protection: Allowance, damage: str) -> None:
    protection.breaker.note_refusal(retry_after=None)
    protection.breaker.path.write_text(damage, encoding="utf-8")

    assert protection.breaker.load() == RefusalRun()


def test_two_subjects_do_not_blow_each_other_s_fuse(tmp_path: Path, clock: MovableClock) -> None:
    mine = Allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        data_root=tmp_path,
        cache_root=tmp_path,
        clock=clock,
    )
    theirs = Allowance(
        nip="9876543210",
        environment=KsefEnvironment.TEST,
        data_root=tmp_path,
        cache_root=tmp_path,
        clock=clock,
    )
    for _ in range(REFUSAL_LIMIT):
        mine.breaker.note_refusal(retry_after=None)

    theirs.breaker.refuse_early()


def test_the_guarded_session_carries_this_subject_s_fuse(
    protection: Allowance, session: CountingSession
) -> None:
    guarded = protection.guarded(session=session)

    assert guarded.breaker == protection.breaker


def test_the_guarded_session_keeps_the_single_attempt_default(
    protection: Allowance, session: CountingSession
) -> None:
    # GH-100: podłączona, ale nie zmieniająca ruchu do KSeF-u.
    assert protection.guarded(session=session).retry is NO_AUTOMATIC_RETRY


def test_the_default_clock_is_the_wall_clock_in_utc(tmp_path: Path) -> None:
    everyday = Allowance(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        data_root=tmp_path,
        cache_root=tmp_path,
    )

    everyday.ledger.record({Operation.EXPORT: (datetime.now(tz=UTC),)})

    assert list(everyday.ledger.load()) == [Operation.EXPORT]
