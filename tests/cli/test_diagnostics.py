from pathlib import Path

import pytest

from ksef_mcp import cli, config, keyring_preflight, ksef_port, messages
from ksef_mcp.config import Configuration
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.rendering import node_preflight
from ksef_mcp.storage import token_store
from tests.cli.conftest import NIP, TOKEN, Recorder, keyring_report, node_report
from tests.conftest import raiser
from tests.support.synthetic import synthetic_metadata


def invoice(ordinal: int) -> ksef_port.InvoiceMetadata:
    return synthetic_metadata(ordinal)


# Warunki wstępne, o które pyta i `doctor`, i onboarding.


def test_missing_node_is_described_with_an_install_command() -> None:
    described = "\n".join(messages.describe_node(node_report(version=None)))

    assert "fnm install 22.14.0" in described


def test_missing_node_warns_about_the_shell_profile() -> None:
    described = "\n".join(messages.describe_node(node_report(version=None)))

    assert "fnm env" in described


def test_current_node_is_described_as_satisfying(tmp_path: Path) -> None:
    pin_file = tmp_path / node_preflight.NODE_VERSION_FILE
    described = "\n".join(
        messages.describe_node(
            node_report(
                version=(22, 17, 0),
                required=(22, 17, 0),
                pinned=(22, 17, 0),
                pinned_by=pin_file,
            )
        )
    )

    assert f"z {pin_file}" in described


@pytest.fixture
def pin_below_the_generator(tmp_path: Path) -> str:
    return "\n".join(
        messages.describe_node(
            node_report(
                version=(20, 11, 0),
                required=node_preflight.MINIMUM_NODE_VERSION,
                pinned=(20, 11, 0),
                pinned_by=tmp_path / node_preflight.NODE_VERSION_FILE,
            )
        )
    )


def test_a_pin_below_the_generator_names_the_pinned_version(
    pin_below_the_generator: str,
) -> None:
    assert "pin 20.11.0" in pin_below_the_generator


def test_a_pin_below_the_generator_says_which_number_wins(
    pin_below_the_generator: str,
) -> None:
    assert "biorę wyższe" in pin_below_the_generator


def test_old_node_is_described_as_too_old() -> None:
    described = "\n".join(messages.describe_node(node_report(version=(20, 11, 0))))

    assert "starsze niż wymagane 22.14.0" in described


def test_absent_keyring_points_at_the_environment_variable() -> None:
    described = "\n".join(messages.describe_keyring(keyring_report()))

    assert token_store.FALLBACK_ENVIRONMENT_VARIABLE in described


def test_available_keyring_marks_the_default_backend() -> None:
    described = "\n".join(
        messages.describe_keyring(
            keyring_report(("keyring.backends.SecretService", 5), ("keyring.backends.kwallet", 4))
        )
    )

    assert "1. keyring.backends.SecretService (priorytet 5) — domyślny" in described


# `doctor` — wszystko, co da się powiedzieć bez sięgania do KSeF.


@pytest.fixture
def doctored(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    tmp_path: Path,
    configuration_file: Path,
) -> tuple[int, Recorder]:
    # The configuration path is explicit on purpose: without it `doctor` reads
    # whatever the developer running the suite happens to have configured.
    recorder = Recorder()
    code = cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=tmp_path,
        configuration_file=configuration_file,
    )
    return code, recorder


def test_doctor_succeeds(doctored: tuple[int, Recorder]) -> None:
    code, _ = doctored

    assert code == cli.EXIT_OK


def test_doctor_reports_preflight(doctored: tuple[int, Recorder]) -> None:
    _, recorder = doctored

    assert "Warunki wstępne:" in recorder.transcript


def test_doctor_names_the_path_it_runs_from(doctored: tuple[int, Recorder]) -> None:
    # With two distributions shipping a `ksef-mcp` script, the path is the
    # only answer that says which one won on PATH (#75).
    _, recorder = doctored

    assert "Ścieżka:" in recorder.transcript


def test_doctor_disowns_the_unrelated_project(doctored: tuple[int, Recorder]) -> None:
    _, recorder = doctored

    assert "To nie jest ksef-mcp.pl" in recorder.transcript


def test_doctor_says_when_there_is_no_configuration(doctored: tuple[int, Recorder]) -> None:
    _, recorder = doctored

    assert "brak konfiguracji" in recorder.transcript


def test_doctor_says_nothing_about_twins_when_there_are_none(
    doctored: tuple[int, Recorder],
) -> None:
    _, recorder = doctored

    assert "zapisane starym sposobem" not in recorder.transcript


@pytest.fixture
def stale_subject_directory(subject_data_root: Path) -> Path:
    # The shape a taxpayer who onboarded before GH-111 is left with: an archive
    # under the spelling they typed, which this version no longer looks at.
    stale = subject_data_root / "subjects" / "123-456-78-90" / "test"
    stale.mkdir(parents=True)
    (stale / "faktury").mkdir()
    return stale


def test_doctor_points_at_an_archive_left_under_the_old_spelling(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    tmp_path: Path,
    configured: Path,
    stale_subject_directory: Path,
) -> None:
    recorder = Recorder()

    cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=tmp_path,
        configuration_file=configured,
    )

    assert str(stale_subject_directory.parent) in recorder.transcript


def test_doctor_leaves_the_old_archive_exactly_where_it_is(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    tmp_path: Path,
    configured: Path,
    stale_subject_directory: Path,
) -> None:
    # Detection, never migration: those directories hold invoices carrying a
    # counterparty's personal data, and moving them unasked is the worse answer.
    cli.main(
        ["doctor"],
        console=Recorder().console,
        working_directory=tmp_path,
        configuration_file=configured,
    )

    assert (stale_subject_directory / "faktury").is_dir()


@pytest.fixture
def accounting_office(subject_data_root: Path) -> Path:
    # Trzech klientów biura onboardowanych różnymi zapisami. Konfiguracja
    # wskazuje w danej chwili najwyżej jednego z nich, a pozostali są dokładnie
    # tymi, o których nikt się nie dowie bez przejrzenia całego katalogu.
    holder = subject_data_root / "subjects"
    for name in ("9876543210", "PL9876543210", "111-222-33-44"):
        (holder / name / "test").mkdir(parents=True)
    return holder


@pytest.fixture
def office_transcript(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    tmp_path: Path,
    configured: Path,
    accounting_office: Path,
) -> str:
    recorder = Recorder()

    cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=tmp_path,
        configuration_file=configured,
    )
    return recorder.transcript


def test_doctor_names_a_client_the_configuration_does_not(
    office_transcript: str,
    accounting_office: Path,
) -> None:
    assert str(accounting_office / "111-222-33-44") in office_transcript


def test_doctor_names_the_directory_the_old_one_belongs_under(
    office_transcript: str,
    accounting_office: Path,
) -> None:
    assert f"→ {accounting_office / '9876543210'}" in office_transcript


def test_doctor_says_when_the_normalised_directory_already_exists(
    office_transcript: str,
) -> None:
    # Przeniesienie zawartości do istniejącego katalogu może nadpisać faktury,
    # więc ta różnica musi być widoczna, zanim ktoś ruszy pliki.
    assert "ten katalog już istnieje" in office_transcript


def test_doctor_does_not_crash_on_a_configuration_whose_nip_is_not_one(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    tmp_path: Path,
    working_directory: Path,
) -> None:
    # `doctor` is where a person goes when something is already wrong, so it
    # reports and keeps going rather than raising over the same bad value.
    path = tmp_path / "state" / config.CONFIGURATION_FILE
    config.save_configuration(
        Configuration(
            nip="nie-jest-nipem",
            environment=KsefEnvironment.TEST,
            keyring_backend="keyring.backends.SecretService",
            working_directory=working_directory,
        ),
        path=path,
    )
    recorder = Recorder()

    code = cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=tmp_path,
        configuration_file=path,
    )

    assert code == cli.EXIT_OK


def test_doctor_names_the_environment_without_calling_ksef(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    tmp_path: Path,
    configured: Path,
) -> None:
    # `doctor` is the only command that can answer this for free; `verify`
    # spends a KSeF call to say the same thing.
    recorder = Recorder()

    cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=tmp_path,
        configuration_file=configured,
    )

    assert "Środowisko: test" in recorder.transcript


def test_doctor_reports_a_locked_collection(
    monkeypatch: pytest.MonkeyPatch,
    usable_keyring: keyring_preflight.KeyringReport,
    healthy_node: None,
    configuration_file: Path,
) -> None:
    monkeypatch.setattr(
        keyring_preflight,
        "inspect_collection_lock",
        lambda: keyring_preflight.CollectionLock.LOCKED,
    )
    recorder = Recorder()

    cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=Path.cwd(),
        configuration_file=configuration_file,
    )

    assert "Kolekcja: zablokowana" in recorder.transcript


def test_doctor_reports_an_unlocked_collection(
    monkeypatch: pytest.MonkeyPatch,
    usable_keyring: keyring_preflight.KeyringReport,
    healthy_node: None,
    configuration_file: Path,
) -> None:
    monkeypatch.setattr(
        keyring_preflight,
        "inspect_collection_lock",
        lambda: keyring_preflight.CollectionLock.UNLOCKED,
    )
    recorder = Recorder()

    cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=Path.cwd(),
        configuration_file=configuration_file,
    )

    assert "Kolekcja: odblokowana" in recorder.transcript


def test_doctor_says_nothing_about_a_platform_without_a_collection(
    usable_keyring: keyring_preflight.KeyringReport,
    healthy_node: None,
    configuration_file: Path,
) -> None:
    # macOS and Windows have no Secret Service at all, and a line about it
    # would read like a fault where there is none.
    recorder = Recorder()

    cli.main(
        ["doctor"],
        console=recorder.console,
        working_directory=Path.cwd(),
        configuration_file=configuration_file,
    )

    assert "Kolekcja" not in recorder.transcript


# `verify` — jedyne polecenie, które wydaje godzinowy budżet.


def test_verify_refuses_without_configuration(configuration_file: Path) -> None:
    recorder = Recorder()

    code = cli.main(
        ["verify"],
        console=recorder.console,
        configuration_file=configuration_file,
    )

    assert (code, "ksef-mcp onboarding" in recorder.transcript) == (
        cli.EXIT_NOT_CONFIGURED,
        True,
    )


@pytest.fixture
def damaged_configuration(configuration_file: Path) -> Path:
    # What an interrupted write leaves behind: the file exists, so nothing
    # treats the server as unconfigured, and it is not JSON.
    configuration_file.parent.mkdir(parents=True, exist_ok=True)
    configuration_file.write_text('{"nip": "12345', encoding="utf-8")
    return configuration_file


def test_a_damaged_configuration_file_is_named_rather_than_traced(
    damaged_configuration: Path,
) -> None:
    # GH-119: this used to leave the CLI with a bare `JSONDecodeError`
    # traceback, which says nothing about deleting or rewriting one file.
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=damaged_configuration)

    assert code == cli.EXIT_UNREADABLE_CONFIGURATION
    assert str(damaged_configuration) in recorder.transcript


def test_a_damaged_configuration_file_says_how_to_get_unstuck(
    damaged_configuration: Path,
) -> None:
    recorder = Recorder()

    cli.main(["verify"], console=recorder.console, configuration_file=damaged_configuration)

    assert "ksef-mcp onboarding" in recorder.transcript


def test_verify_refuses_without_a_token(
    configured: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(token_store, "read_token", lambda **kwargs: None)
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "ksef-mcp token set" in recorder.transcript) == (cli.EXIT_NO_TOKEN, True)


def test_verify_flags_that_the_export_is_not_tied_to_the_nip(
    configured: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        lambda **kwargs: token_store.StoredToken(
            value=TOKEN,
            source=token_store.TokenSource.ENVIRONMENT,
        ),
    )
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        lambda **kwargs: ksef_port.ConnectionCheck(
            environment=KsefEnvironment.TEST,
            subject_name="Moja Firma sp. z o.o.",
            invoices=(),
        ),
    )
    recorder = Recorder()

    cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert "nie gwarantuję, że należy do" in recorder.transcript


def test_verify_greets_the_subject_by_name(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        lambda **kwargs: ksef_port.ConnectionCheck(
            environment=KsefEnvironment.TEST,
            subject_name="Moja Firma sp. z o.o.",
            invoices=(invoice(1),),
        ),
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "jesteś połączony jako Moja Firma sp. z o.o." in recorder.transcript) == (
        cli.EXIT_OK,
        True,
    )


def test_verify_lists_the_invoices_it_found(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        lambda **kwargs: ksef_port.ConnectionCheck(
            environment=KsefEnvironment.TEST,
            subject_name="Moja Firma sp. z o.o.",
            invoices=(invoice(1), invoice(2)),
        ),
    )
    recorder = Recorder()

    cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert "Dostawca sp. z o.o." in recorder.transcript


def test_verify_calls_an_empty_window_a_success(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        lambda **kwargs: ksef_port.ConnectionCheck(
            environment=KsefEnvironment.TEST,
            subject_name=None,
            invoices=(),
        ),
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "pusty wynik to nie błąd" in recorder.transcript) == (cli.EXIT_OK, True)


def test_verify_quotes_the_wait_ksef_asked_for(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        raiser(ksef_port.KsefRateLimited("limit wyczerpany", retry_after=120)),
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "Odczekaj 120 s" in recorder.transcript) == (cli.EXIT_KSEF_REFUSED, True)


def test_verify_never_retries_on_its_own(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        raiser(ksef_port.KsefRateLimited("limit wyczerpany", retry_after=None)),
    )
    recorder = Recorder()

    cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert "nie ponawiam samoczynnie" in recorder.transcript


def test_verify_reports_a_blown_local_fuse_instead_of_a_traceback(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GH-234. `check_connection` pyta przez ten sam `GuardedSession`, co reszta,
    # więc zapalony bezpiecznik tu dociera. Spod `KsefPortError` wychodził
    # złapany; nazwany osobno w tym `except` wychodzi złapany nadal, a bez tego
    # `verify` odpowiadałoby stosem wywołań zamiast momentem wznowienia.
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        raiser(
            ksef_port.RefusalBreakerEngaged(
                "Odmawiam lokalnie; nic nie wyślę przed 2026-09-01T13:00:00+00:00."
            )
        ),
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "Odmawiam lokalnie" in recorder.transcript) == (cli.EXIT_KSEF_REFUSED, True)


def test_verify_reports_a_spent_local_counter_instead_of_a_traceback(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GH-243. The first local refusal, spent hourly counter, left the port root
    # in GH-211 and `verify` was not told — so the one command whose whole job
    # is to say when asking resumes answered with a stack trace instead.
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        raiser(
            ksef_port.BudgetExhausted(
                "Refusing locally: this server's own counter is spent. "
                "Frees at 2026-09-01T13:00:00+00:00."
            )
        ),
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "Frees at 2026-09-01T13:00:00+00:00" in recorder.transcript) == (
        cli.EXIT_KSEF_REFUSED,
        True,
    )


def test_verify_reports_a_rejected_token(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        raiser(ksef_port.KsefAuthenticationFailed("KSeF odrzucił token")),
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "odrzucił token" in recorder.transcript) == (cli.EXIT_KSEF_REFUSED, True)


def test_verify_names_the_subject_and_the_environment_when_ksef_refuses(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        raiser(ksef_port.KsefAuthenticationFailed("token nie pasuje do podmiotu")),
    )
    recorder = Recorder()

    cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert f"Nie potwierdziłem połączenia dla {NIP} (test)" in recorder.transcript


def test_verify_names_the_subject_when_the_limit_refuses_too(
    configured: Path,
    stored_token: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ksef_port,
        "check_connection",
        raiser(ksef_port.KsefRateLimited("limit wyczerpany", retry_after=60)),
    )
    recorder = Recorder()

    cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert NIP in recorder.transcript


def test_verify_refuses_before_touching_a_locked_keyring(
    configured: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The token is never read and KSeF is never called: reaching the keyring
    # would open an unlock prompt and hang the transport (D-004, ST-3).
    monkeypatch.setattr(
        keyring_preflight,
        "inspect_collection_lock",
        lambda: keyring_preflight.CollectionLock.LOCKED,
    )
    monkeypatch.setattr(ksef_port, "check_connection", raiser(AssertionError("sięgnięto do KSeF")))
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "locked" in recorder.transcript) == (cli.EXIT_UNUSABLE_KEYRING, True)


def test_verify_refuses_a_configuration_whose_nip_is_not_one(
    tmp_path: Path,
    working_directory: Path,
) -> None:
    path = tmp_path / "state" / config.CONFIGURATION_FILE
    config.save_configuration(
        Configuration(
            nip="nie-jest-nipem",
            environment=KsefEnvironment.TEST,
            keyring_backend="keyring.backends.SecretService",
            working_directory=working_directory,
        ),
        path=path,
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=path)

    assert (code, "dziesięć cyfr" in recorder.transcript) == (cli.EXIT_INVALID_NIP, True)
