import getpass
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from ksef_mcp import cli, config, ksef_port, preflight, skill, token_store
from ksef_mcp.config import Configuration, KsefEnvironment
from synthetic import synthetic_metadata

NIP = "1234567890"

TOKEN = "aaaabbbbccccdddd"


class Recorder:
    def __init__(
        self,
        *,
        answers: Sequence[str] = (),
        secrets: Sequence[str] = (),
    ) -> None:
        self.lines: list[str] = []
        self.prompts: list[str] = []
        self._answers = iter(answers)
        self._secrets = iter(secrets)

    def write(self, line: str) -> None:
        self.lines.append(line)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self._answers)

    def ask_secret(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self._secrets)

    @property
    def console(self) -> cli.Console:
        return cli.Console(write=self.write, ask=self.ask, ask_secret=self.ask_secret)

    @property
    def transcript(self) -> str:
        return "\n".join(self.lines)


def raiser(error: Exception) -> Callable[..., object]:
    def raise_it(*args: object, **kwargs: object) -> object:
        raise error

    return raise_it


def keyring_report(*modules_with_priority: tuple[str, float]) -> preflight.KeyringReport:
    backends = tuple(
        preflight.KeyringBackendReport(module=module, priority=priority)
        for module, priority in modules_with_priority
    )
    preferred = max(backends, key=lambda item: item.priority).module if backends else None
    return preflight.KeyringReport(backends=backends, preferred=preferred)


def node_report(
    *,
    version: tuple[int, int, int] | None,
    required: tuple[int, int, int] = preflight.MINIMUM_NODE_VERSION,
    pinned: tuple[int, int, int] | None = None,
    pinned_by: Path | None = None,
) -> preflight.NodeReport:
    return preflight.NodeReport(
        executable=None if version is None else "/usr/bin/node",
        version=version,
        required=required,
        pinned=pinned,
        pinned_by=pinned_by,
    )


@pytest.fixture
def usable_keyring(monkeypatch: pytest.MonkeyPatch) -> preflight.KeyringReport:
    report = keyring_report(
        ("keyring.backends.SecretService", 5),
        ("keyring.backends.kwallet", 4.9),
    )
    monkeypatch.setattr(preflight, "inspect_keyring", lambda: report)
    return report


@pytest.fixture
def unusable_keyring(monkeypatch: pytest.MonkeyPatch) -> preflight.KeyringReport:
    report = keyring_report()
    monkeypatch.setattr(preflight, "inspect_keyring", lambda: report)
    return report


@pytest.fixture
def healthy_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        preflight,
        "inspect_node",
        lambda **kwargs: node_report(version=(22, 17, 0)),
    )


@pytest.fixture
def accepting_token_store(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    written: list[tuple[str, str]] = []

    def store(*, nip: str, token: str) -> token_store.TokenFingerprint:
        written.append((nip, token))
        return token_store.fingerprint(token)

    monkeypatch.setattr(token_store, "store_token", store)
    return written


@pytest.fixture
def configuration_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / config.CONFIGURATION_FILE


@pytest.fixture
def invoice_directory(tmp_path: Path) -> Path:
    return tmp_path / "faktury"


@pytest.fixture
def completed_onboarding(
    healthy_node: None,
    usable_keyring: preflight.KeyringReport,
    accepting_token_store: list[tuple[str, str]],
    configuration_file: Path,
    invoice_directory: Path,
) -> tuple[int, Recorder]:
    recorder = Recorder(
        answers=[NIP, "", "", str(invoice_directory)],
        secrets=[TOKEN],
    )
    code = cli.main(
        ["onboarding"],
        console=recorder.console,
        working_directory=configuration_file.parent,
        configuration_file=configuration_file,
    )
    return code, recorder


def test_default_console_is_wired_to_the_terminal() -> None:
    console = cli.default_console()

    assert (console.write, console.ask) == (print, input)


def test_the_token_prompt_never_echoes() -> None:
    assert cli.default_console().ask_secret is getpass.getpass


def test_default_answer_is_used_when_nothing_is_typed() -> None:
    recorder = Recorder(answers=["  "])

    assert cli.ask_with_default(recorder.console, prompt="Pytanie", default="test") == "test"


def test_typed_answer_wins_over_the_default() -> None:
    recorder = Recorder(answers=[" demo "])

    assert cli.ask_with_default(recorder.console, prompt="Pytanie", default="test") == "demo"


def test_required_answer_is_asked_again_when_blank() -> None:
    recorder = Recorder(answers=["", NIP])

    assert cli.ask_required(recorder.console, prompt="NIP") == NIP


def test_required_secret_is_asked_again_when_blank() -> None:
    recorder = Recorder(secrets=["", TOKEN])

    assert cli.ask_secret_required(recorder.console, prompt="Token") == TOKEN


def test_missing_node_is_described_with_an_install_command() -> None:
    described = "\n".join(cli.describe_node(node_report(version=None)))

    assert "fnm install 22.14.0" in described


def test_missing_node_warns_about_the_shell_profile() -> None:
    described = "\n".join(cli.describe_node(node_report(version=None)))

    assert "fnm env" in described


def test_current_node_is_described_as_satisfying(tmp_path: Path) -> None:
    pin_file = tmp_path / preflight.NODE_VERSION_FILE
    described = "\n".join(
        cli.describe_node(
            node_report(
                version=(22, 17, 0),
                required=(22, 17, 0),
                pinned=(22, 17, 0),
                pinned_by=pin_file,
            )
        )
    )

    assert f"z {pin_file}" in described


def test_a_pin_below_the_generator_minimum_names_both_numbers(tmp_path: Path) -> None:
    described = "\n".join(
        cli.describe_node(
            node_report(
                version=(20, 11, 0),
                required=preflight.MINIMUM_NODE_VERSION,
                pinned=(20, 11, 0),
                pinned_by=tmp_path / preflight.NODE_VERSION_FILE,
            )
        )
    )

    assert "pin 20.11.0" in described and "biorę wyższe" in described


def test_old_node_is_described_as_too_old() -> None:
    described = "\n".join(cli.describe_node(node_report(version=(20, 11, 0))))

    assert "starsze niż wymagane 22.14.0" in described


def test_absent_keyring_points_at_the_environment_variable() -> None:
    described = "\n".join(cli.describe_keyring(keyring_report()))

    assert token_store.FALLBACK_ENVIRONMENT_VARIABLE in described


def test_available_keyring_marks_the_default_backend() -> None:
    described = "\n".join(
        cli.describe_keyring(
            keyring_report(("keyring.backends.SecretService", 5), ("keyring.backends.kwallet", 4))
        )
    )

    assert "1. keyring.backends.SecretService (priorytet 5) — domyślny" in described


def test_single_backend_is_chosen_without_asking() -> None:
    recorder = Recorder()

    chosen = cli.choose_keyring_backend(
        recorder.console, keyring_report(("keyring.backends.SecretService", 5))
    )

    assert (chosen, recorder.prompts) == ("keyring.backends.SecretService", [])


def test_backend_choice_rejects_a_number_out_of_range() -> None:
    recorder = Recorder(answers=["7", "2"])
    report = keyring_report(("keyring.backends.SecretService", 5), ("keyring.backends.kwallet", 4))

    chosen = cli.choose_keyring_backend(recorder.console, report)

    assert (chosen, "Podaj numer od 1 do 2." in recorder.transcript) == (
        "keyring.backends.kwallet",
        True,
    )


def test_backend_choice_defaults_to_the_preferred_backend() -> None:
    recorder = Recorder(answers=[""])
    report = keyring_report(("keyring.backends.kwallet", 4), ("keyring.backends.SecretService", 5))

    assert cli.choose_keyring_backend(recorder.console, report) == "keyring.backends.SecretService"


def test_environment_defaults_to_the_test_registry() -> None:
    recorder = Recorder(answers=[""])

    assert cli.choose_environment(recorder.console) is KsefEnvironment.TEST


def test_environment_rejects_an_unknown_value() -> None:
    recorder = Recorder(answers=["produkcja", "production"])

    chosen = cli.choose_environment(recorder.console)

    assert (chosen, "Dozwolone wartości" in recorder.transcript) == (
        KsefEnvironment.PRODUCTION,
        True,
    )


def test_invoice_directory_is_created_and_reported(invoice_directory: Path) -> None:
    recorder = Recorder(answers=[str(invoice_directory)])

    chosen = cli.choose_invoice_directory(recorder.console, nip=NIP)

    assert (chosen, chosen.is_dir()) == (invoice_directory, True)


def test_an_existing_invoice_directory_is_left_as_it_was(tmp_path: Path) -> None:
    shared = tmp_path / "wspolny"
    shared.mkdir(mode=0o755)
    recorder = Recorder(answers=[str(shared)])

    cli.choose_invoice_directory(recorder.console, nip=NIP)

    assert "zostawiam uprawnienia 0755" in recorder.transcript


def test_invoice_directory_in_a_synced_folder_is_flagged(tmp_path: Path) -> None:
    recorder = Recorder(answers=[str(tmp_path / "Dropbox" / "faktury")])

    cli.choose_invoice_directory(recorder.console, nip=NIP)

    assert "dropbox" in recorder.transcript


def test_onboarding_stops_when_no_keyring_is_available(
    healthy_node: None,
    unusable_keyring: preflight.KeyringReport,
    configuration_file: Path,
) -> None:
    recorder = Recorder()

    code = cli.main(
        ["onboarding"],
        console=recorder.console,
        working_directory=configuration_file.parent,
        configuration_file=configuration_file,
    )

    assert (code, "Przerywam" in recorder.transcript) == (cli.EXIT_UNUSABLE_KEYRING, True)


def test_onboarding_never_prompts_without_a_keyring(
    healthy_node: None,
    unusable_keyring: preflight.KeyringReport,
    configuration_file: Path,
) -> None:
    recorder = Recorder()

    cli.main(
        ["onboarding"],
        console=recorder.console,
        working_directory=configuration_file.parent,
        configuration_file=configuration_file,
    )

    assert recorder.prompts == []


def test_onboarding_succeeds(completed_onboarding: tuple[int, Recorder]) -> None:
    code, _ = completed_onboarding

    assert code == cli.EXIT_OK


def test_onboarding_stores_the_token_under_the_nip(
    completed_onboarding: tuple[int, Recorder],
    accepting_token_store: list[tuple[str, str]],
) -> None:
    assert accepting_token_store == [(NIP, TOKEN)]


def test_onboarding_never_echoes_the_token(
    completed_onboarding: tuple[int, Recorder],
) -> None:
    _, recorder = completed_onboarding

    assert TOKEN not in recorder.transcript


def test_onboarding_confirms_the_token_by_fingerprint(
    completed_onboarding: tuple[int, Recorder],
) -> None:
    _, recorder = completed_onboarding

    assert "16 znaków, końcówka …dddd" in recorder.transcript


def test_onboarding_saves_the_configuration(
    completed_onboarding: tuple[int, Recorder],
    configuration_file: Path,
    invoice_directory: Path,
) -> None:
    stored = config.load_configuration(path=configuration_file)

    assert stored == config.Configuration(
        nip=NIP,
        environment=KsefEnvironment.TEST,
        keyring_backend="keyring.backends.SecretService",
        invoice_directory=invoice_directory,
    )


def test_onboarding_says_why_it_does_not_call_ksef(
    completed_onboarding: tuple[int, Recorder],
) -> None:
    _, recorder = completed_onboarding

    assert "godzinowy budżet" in recorder.transcript


def test_doctor_reports_preflight_only(
    healthy_node: None,
    usable_keyring: preflight.KeyringReport,
    tmp_path: Path,
) -> None:
    recorder = Recorder()

    code = cli.main(["doctor"], console=recorder.console, working_directory=tmp_path)

    assert (code, "Warunki wstępne:" in recorder.transcript) == (cli.EXIT_OK, True)


def test_token_set_stores_and_confirms(
    accepting_token_store: list[tuple[str, str]],
) -> None:
    recorder = Recorder(secrets=[TOKEN])

    code = cli.main(["token", "set", "--nip", NIP], console=recorder.console)

    assert (code, accepting_token_store) == (cli.EXIT_OK, [(NIP, TOKEN)])


def removal(*, removed: bool, exported: bool) -> Callable[..., token_store.Removal]:
    return lambda **kwargs: token_store.Removal(
        removed_from_keyring=removed,
        still_exported=exported,
    )


def test_token_delete_reports_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "delete_token", removal(removed=True, exported=False))
    recorder = Recorder()

    code = cli.main(["token", "delete", "--nip", NIP], console=recorder.console)

    assert (code, "usunięty z keyringu" in recorder.transcript) == (cli.EXIT_OK, True)


def test_token_delete_reports_nothing_to_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "delete_token", removal(removed=False, exported=False))
    recorder = Recorder()

    code = cli.main(["token", "delete", "--nip", NIP], console=recorder.console)

    assert code == cli.EXIT_NO_TOKEN


def test_token_delete_does_not_claim_revocation_while_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(token_store, "delete_token", removal(removed=True, exported=True))
    recorder = Recorder()

    cli.main(["token", "delete", "--nip", NIP], console=recorder.console)

    assert "token wciąż działa" in recorder.transcript


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


def test_token_status_reports_a_stored_token(
    stored_token: None,
) -> None:
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert (code, TOKEN in recorder.transcript) == (cli.EXIT_OK, False)


def test_token_status_names_the_source(stored_token: None) -> None:
    recorder = Recorder()

    cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert "keyringu systemowego" in recorder.transcript


def test_token_status_names_the_exported_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        lambda **kwargs: token_store.StoredToken(
            value=TOKEN,
            source=token_store.TokenSource.ENVIRONMENT,
        ),
    )
    recorder = Recorder()

    cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert token_store.FALLBACK_ENVIRONMENT_VARIABLE in recorder.transcript


def test_an_unusable_keyring_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        raiser(token_store.TokenStoreUnavailable("brak magazynu")),
    )
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert (code, "brak magazynu" in recorder.transcript) == (
        cli.EXIT_UNUSABLE_KEYRING,
        True,
    )


def test_token_status_reports_a_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "read_token", lambda **kwargs: None)
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert (code, "Brak tokenu" in recorder.transcript) == (cli.EXIT_NO_TOKEN, True)


def invoice(ordinal: int) -> ksef_port.InvoiceMetadata:
    return synthetic_metadata(ordinal)


@pytest.fixture
def configured(configuration_file: Path, invoice_directory: Path) -> Path:
    config.save_configuration(
        Configuration(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            keyring_backend="keyring.backends.SecretService",
            invoice_directory=invoice_directory,
        ),
        path=configuration_file,
    )
    return configuration_file


@pytest.fixture
def stored_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        lambda **kwargs: token_store.StoredToken(
            value=TOKEN,
            source=token_store.TokenSource.KEYRING,
        ),
    )


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


def test_verify_refuses_without_a_token(
    configured: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(token_store, "read_token", lambda **kwargs: None)
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "ksef-mcp token set" in recorder.transcript) == (cli.EXIT_NO_TOKEN, True)


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
        preflight,
        "inspect_collection_lock",
        lambda: preflight.CollectionLock.LOCKED,
    )
    monkeypatch.setattr(ksef_port, "check_connection", raiser(AssertionError("sięgnięto do KSeF")))
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "locked" in recorder.transcript) == (cli.EXIT_UNUSABLE_KEYRING, True)


def test_doctor_reports_a_locked_collection(
    monkeypatch: pytest.MonkeyPatch,
    usable_keyring: preflight.KeyringReport,
    healthy_node: None,
) -> None:
    monkeypatch.setattr(
        preflight,
        "inspect_collection_lock",
        lambda: preflight.CollectionLock.LOCKED,
    )
    recorder = Recorder()

    cli.main(["doctor"], console=recorder.console, working_directory=Path.cwd())

    assert "Kolekcja: zablokowana" in recorder.transcript


def test_doctor_reports_an_unlocked_collection(
    monkeypatch: pytest.MonkeyPatch,
    usable_keyring: preflight.KeyringReport,
    healthy_node: None,
) -> None:
    monkeypatch.setattr(
        preflight,
        "inspect_collection_lock",
        lambda: preflight.CollectionLock.UNLOCKED,
    )
    recorder = Recorder()

    cli.main(["doctor"], console=recorder.console, working_directory=Path.cwd())

    assert "Kolekcja: odblokowana" in recorder.transcript


def test_doctor_says_nothing_about_a_platform_without_a_collection(
    usable_keyring: preflight.KeyringReport,
    healthy_node: None,
) -> None:
    # macOS and Windows have no Secret Service at all, and a line about it
    # would read like a fault where there is none.
    recorder = Recorder()

    cli.main(["doctor"], console=recorder.console, working_directory=Path.cwd())

    assert "Kolekcja" not in recorder.transcript


def test_onboarding_points_at_the_verify_command(
    completed_onboarding: tuple[int, Recorder],
) -> None:
    _, recorder = completed_onboarding

    assert "ksef-mcp verify" in recorder.transcript


def test_the_ksef_sdk_is_not_imported_at_startup() -> None:
    # Run out of process: this file imports ksef_port at the top, so in-process
    # the module is always loaded and the guarantee cannot be observed. Putting
    # the import back on cli.py would otherwise stay green and quietly undo it.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ksef_mcp.cli, sys; sys.exit(1 if 'ksef2' in sys.modules else 0)",
        ],
        check=False,
    )

    assert completed.returncode == 0


def install_skill(recorder: Recorder, *, scope: str, tmp_path: Path) -> int:
    return cli.main(
        ["skill", "install", "--scope", scope],
        console=recorder.console,
        working_directory=tmp_path / "project",
        home=tmp_path / "home",
    )


@pytest.fixture
def skill_in_the_project(tmp_path: Path) -> Path:
    recorder = Recorder()
    install_skill(recorder, scope="project", tmp_path=tmp_path)
    return skill.skill_path(
        skill.SkillScope.PROJECT,
        home=tmp_path / "home",
        working_directory=tmp_path / "project",
    )


@pytest.mark.parametrize(("scope", "parent"), [("user", "home"), ("project", "project")])
def test_skill_install_writes_into_the_chosen_scope(
    tmp_path: Path,
    scope: str,
    parent: str,
) -> None:
    recorder = Recorder()

    code = install_skill(recorder, scope=scope, tmp_path=tmp_path)

    written = tmp_path / parent / ".claude" / "skills" / "ksef-mcp" / "SKILL.md"
    assert (code, written.is_file()) == (cli.EXIT_OK, True)


def test_skill_install_requires_an_explicit_scope() -> None:
    recorder = Recorder()

    with pytest.raises(SystemExit):
        cli.main(["skill", "install"], console=recorder.console)


def test_skill_install_leaves_an_up_to_date_skill_alone(
    skill_in_the_project: Path,
    tmp_path: Path,
) -> None:
    recorder = Recorder()

    code = install_skill(recorder, scope="project", tmp_path=tmp_path)

    assert (code, "jest aktualny" in recorder.transcript) == (cli.EXIT_OK, True)


def test_skill_install_shows_the_difference_before_overwriting(
    skill_in_the_project: Path,
    tmp_path: Path,
) -> None:
    skill_in_the_project.write_text("Własna wersja księgowej\n", encoding="utf-8")
    recorder = Recorder(answers=["t"])

    code = install_skill(recorder, scope="project", tmp_path=tmp_path)

    assert (code, "-Własna wersja księgowej" in recorder.transcript) == (cli.EXIT_OK, True)


def test_skill_install_keeps_local_edits_when_not_confirmed(
    skill_in_the_project: Path,
    tmp_path: Path,
) -> None:
    skill_in_the_project.write_text("Własna wersja księgowej\n", encoding="utf-8")
    recorder = Recorder(answers=["n"])

    code = install_skill(recorder, scope="project", tmp_path=tmp_path)

    assert (code, skill_in_the_project.read_text(encoding="utf-8")) == (
        cli.EXIT_SKILL_KEPT,
        "Własna wersja księgowej\n",
    )


def test_no_subcommand_runs_the_mcp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(cli, "run_mcp_server", lambda: started.append("run"))
    recorder = Recorder()

    code = cli.main([], console=recorder.console)

    assert (code, started) == (cli.EXIT_OK, ["run"])
