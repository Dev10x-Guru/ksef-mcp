import getpass
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from ksef_mcp import (
    cli,
    client,
    config,
    keyring_preflight,
    ksef_port,
    messages,
    skill,
)
from ksef_mcp.config import Configuration
from ksef_mcp.ksef_port import adapter as port_adapter
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.rendering import node_preflight
from ksef_mcp.storage import token_store
from ksef_mcp.storage.archive import InvoiceArchive
from ksef_mcp.storage.audit import AuditTrail, AuthorisationBasis, Disclosure
from tests.conftest import raiser
from tests.invoices.test_statement import RecordingPort, RecordingSession, page_of
from tests.support.synthetic import BUYER_NAME, synthetic_metadata
from tests.test_retention import a_number as a_ksef_number
from tests.test_retention import a_package as retention_package

NIP = "1234567890"

DEPARTED_NIP = "9876543210"

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


def keyring_report(*modules_with_priority: tuple[str, float]) -> keyring_preflight.KeyringReport:
    backends = tuple(
        keyring_preflight.KeyringBackendReport(module=module, priority=priority)
        for module, priority in modules_with_priority
    )
    preferred = max(backends, key=lambda item: item.priority).module if backends else None
    return keyring_preflight.KeyringReport(backends=backends, preferred=preferred)


def node_report(
    *,
    version: tuple[int, int, int] | None,
    required: tuple[int, int, int] = node_preflight.MINIMUM_NODE_VERSION,
    pinned: tuple[int, int, int] | None = None,
    pinned_by: Path | None = None,
) -> node_preflight.NodeReport:
    return node_preflight.NodeReport(
        executable=None if version is None else "/usr/bin/node",
        version=version,
        required=required,
        pinned=pinned,
        pinned_by=pinned_by,
    )


@pytest.fixture
def usable_keyring(monkeypatch: pytest.MonkeyPatch) -> keyring_preflight.KeyringReport:
    report = keyring_report(
        ("keyring.backends.SecretService", 5),
        ("keyring.backends.kwallet", 4.9),
    )
    monkeypatch.setattr(keyring_preflight, "inspect_keyring", lambda: report)
    return report


@pytest.fixture
def unusable_keyring(monkeypatch: pytest.MonkeyPatch) -> keyring_preflight.KeyringReport:
    report = keyring_report()
    monkeypatch.setattr(keyring_preflight, "inspect_keyring", lambda: report)
    return report


@pytest.fixture
def healthy_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_preflight,
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
    usable_keyring: keyring_preflight.KeyringReport,
    accepting_token_store: list[tuple[str, str]],
    configuration_file: Path,
    invoice_directory: Path,
) -> tuple[int, Recorder]:
    # Four settings, then the three closing offers from GH-74 and GH-72:
    # register with the client, install the skill, check the connection.
    recorder = Recorder(
        answers=[NIP, "", "", str(invoice_directory), "n", "n", ""],
        secrets=[TOKEN],
    )
    code = cli.main(
        ["onboarding"],
        console=recorder.console,
        working_directory=configuration_file.parent,
        configuration_file=configuration_file,
        home=configuration_file.parent,
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


@pytest.fixture
def single_backend_choice() -> tuple[str, Recorder]:
    recorder = Recorder()
    chosen = cli.choose_keyring_backend(
        recorder.console, keyring_report(("keyring.backends.SecretService", 5))
    )
    return chosen, recorder


def test_single_backend_is_chosen_without_asking(
    single_backend_choice: tuple[str, Recorder],
) -> None:
    _, recorder = single_backend_choice

    assert recorder.prompts == []


def test_single_backend_is_the_one_that_exists(
    single_backend_choice: tuple[str, Recorder],
) -> None:
    chosen, _ = single_backend_choice

    assert chosen == "keyring.backends.SecretService"


@pytest.fixture
def backend_choice_out_of_range() -> tuple[str, Recorder]:
    recorder = Recorder(answers=["7", "2"])
    report = keyring_report(("keyring.backends.SecretService", 5), ("keyring.backends.kwallet", 4))
    chosen = cli.choose_keyring_backend(recorder.console, report)
    return chosen, recorder


def test_backend_choice_rejects_a_number_out_of_range(
    backend_choice_out_of_range: tuple[str, Recorder],
) -> None:
    _, recorder = backend_choice_out_of_range

    assert "Podaj numer od 1 do 2." in recorder.transcript


def test_backend_choice_takes_the_second_answer(
    backend_choice_out_of_range: tuple[str, Recorder],
) -> None:
    chosen, _ = backend_choice_out_of_range

    assert chosen == "keyring.backends.kwallet"


def test_backend_choice_defaults_to_the_preferred_backend() -> None:
    recorder = Recorder(answers=[""])
    report = keyring_report(("keyring.backends.kwallet", 4), ("keyring.backends.SecretService", 5))

    assert cli.choose_keyring_backend(recorder.console, report) == "keyring.backends.SecretService"


def test_environment_defaults_to_the_test_registry() -> None:
    recorder = Recorder(answers=[""])

    assert cli.choose_environment(recorder.console) is KsefEnvironment.TEST


@pytest.fixture
def environment_rejected_then_named() -> tuple[KsefEnvironment, Recorder]:
    recorder = Recorder(answers=["produkcja", "production"])
    chosen = cli.choose_environment(recorder.console)
    return chosen, recorder


def test_environment_rejects_an_unknown_value(
    environment_rejected_then_named: tuple[KsefEnvironment, Recorder],
) -> None:
    _, recorder = environment_rejected_then_named

    assert "Podaj numer od 1 do 3 albo nazwę." in recorder.transcript


def test_environment_still_accepts_the_name_spelled_out(
    environment_rejected_then_named: tuple[KsefEnvironment, Recorder],
) -> None:
    # Backwards compatibility: GH-71 added numbers, it did not remove names.
    chosen, _ = environment_rejected_then_named

    assert chosen is KsefEnvironment.PRODUCTION


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("1", KsefEnvironment.TEST),
        ("2", KsefEnvironment.DEMO),
        ("3", KsefEnvironment.PRODUCTION),
    ],
)
def test_environment_is_chosen_by_number(answer: str, expected: KsefEnvironment) -> None:
    recorder = Recorder(answers=[answer])

    assert cli.choose_environment(recorder.console) is expected


def test_environment_list_explains_the_difference_between_test_and_demo() -> None:
    recorder = Recorder(answers=[""])

    cli.choose_environment(recorder.console)

    assert "piaskownica" in recorder.transcript


def test_environment_rejects_a_number_past_the_list() -> None:
    recorder = Recorder(answers=["4", "1"])

    assert cli.choose_environment(recorder.console) is KsefEnvironment.TEST


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
    unusable_keyring: keyring_preflight.KeyringReport,
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
    unusable_keyring: keyring_preflight.KeyringReport,
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


@pytest.fixture
def onboarding_with(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    accepting_token_store: list[tuple[str, str]],
    configuration_file: Path,
    invoice_directory: Path,
) -> Callable[[list[str]], Recorder]:
    """Onboarding driven to the end, with the three closing offers answered."""

    def run(closing_answers: list[str]) -> Recorder:
        recorder = Recorder(
            answers=[NIP, "", "", str(invoice_directory), *closing_answers],
            secrets=[TOKEN],
        )
        cli.main(
            ["onboarding"],
            console=recorder.console,
            working_directory=configuration_file.parent,
            configuration_file=configuration_file,
            home=configuration_file.parent,
        )
        return recorder

    return run


@pytest.fixture
def absent_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "command_available", lambda: False)


@pytest.fixture
def registering_client(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    registered: list[str] = []
    monkeypatch.setattr(client, "command_available", lambda: True)
    monkeypatch.setattr(client, "already_registered", lambda name: False)
    monkeypatch.setattr(client, "register", lambda name: registered.append(name) or True)
    return registered


@pytest.fixture
def client_already_holding_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "command_available", lambda: True)
    monkeypatch.setattr(client, "already_registered", lambda name: True)


def test_declined_registration_leaves_the_command_to_copy(
    onboarding_with: Callable[[list[str]], Recorder],
) -> None:
    recorder = onboarding_with(["n", "n", ""])

    assert f"claude mcp add {SERVER_NAME}" in recorder.transcript


def test_registration_happens_when_the_client_is_there(
    onboarding_with: Callable[[list[str]], Recorder],
    registering_client: list[str],
) -> None:
    onboarding_with(["t", "n", ""])

    assert registering_client == [SERVER_NAME]


def test_registration_is_not_repeated(
    onboarding_with: Callable[[list[str]], Recorder],
    client_already_holding_the_server: None,
) -> None:
    # Onboarding gets re-run while a setting is corrected; a second entry
    # under the same name is exactly what that must not produce.
    recorder = onboarding_with(["t", "n", ""])

    assert "jest już zarejestrowany" in recorder.transcript


def test_a_missing_client_hands_over_the_config_block(
    onboarding_with: Callable[[list[str]], Recorder],
    absent_client: None,
) -> None:
    recorder = onboarding_with(["t", "n", ""])

    assert '"mcpServers"' in recorder.transcript


def test_a_missing_client_names_the_command_verbatim(
    onboarding_with: Callable[[list[str]], Recorder],
    absent_client: None,
) -> None:
    recorder = onboarding_with(["t", "n", ""])

    assert f"claude mcp add {SERVER_NAME} -- uvx {SERVER_NAME}" in recorder.transcript


def test_declined_skill_install_names_the_command(
    onboarding_with: Callable[[list[str]], Recorder],
) -> None:
    recorder = onboarding_with(["n", "n", ""])

    assert "ksef-mcp skill install --scope user" in recorder.transcript


def test_accepted_skill_install_writes_the_skill(
    onboarding_with: Callable[[list[str]], Recorder],
) -> None:
    recorder = onboarding_with(["n", "t", ""])

    assert "Skill zainstalowany" in recorder.transcript


def test_a_run_of_enters_never_reaches_ksef(
    onboarding_with: Callable[[list[str]], Recorder],
    absent_client: None,
) -> None:
    # The whole reason the verify offer defaults to no: a corrective re-run
    # must not spend an hourly allowance the Ministry polices (D-020).
    recorder = onboarding_with(["", "", ""])

    assert "Odpytuję KSeF" not in recorder.transcript


def test_accepting_the_check_runs_verify(
    onboarding_with: Callable[[list[str]], Recorder],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "run_verify", lambda console, **kwargs: cli.EXIT_OK)

    recorder = onboarding_with(["n", "n", "t"])

    assert "Gdy zechcesz to potwierdzić" not in recorder.transcript


# The crossing GH-76 asked for, and the one neither side tested (GH-177).
# `test_accepting_the_check_runs_verify` above substitutes `run_verify` itself,
# so it proves the offer is taken and nothing about what verify then reads; the
# `test_verify_*` tests below start from a configuration a fixture wrote by
# hand. Between the two sits the seam that matters: a path, an environment or a
# NIP written by onboarding in a spelling verify does not read back would look
# exactly like a working pair on both sides.


@pytest.fixture
def token_kept_in_memory(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A keyring that keeps what it was handed, so verify can read it back.

    `accepting_token_store` records the write and answers nothing, and
    `stored_token` answers a token nobody wrote. Neither lets one command read
    what the other stored, which is half of what this crossing is about.
    """
    kept: dict[str, str] = {}

    def store(*, nip: str, token: str) -> token_store.TokenFingerprint:
        kept[nip] = token
        return token_store.fingerprint(token)

    def read(*, nip: str) -> token_store.StoredToken | None:
        held = kept.get(nip)
        if held is None:
            return None
        return token_store.StoredToken(value=held, source=token_store.TokenSource.KEYRING)

    monkeypatch.setattr(token_store, "store_token", store)
    monkeypatch.setattr(token_store, "read_token", read)
    return kept


@pytest.fixture
def onboarded_and_then_verified(
    monkeypatch: pytest.MonkeyPatch,
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    token_kept_in_memory: dict[str, str],
    configuration_file: Path,
    invoice_directory: Path,
) -> tuple[int, Recorder, RecordingSession]:
    """Onboarding to the end, then `verify` on the same disk, in one run.

    Only the port stands in. `run_verify`, `load_configuration`,
    `check_connection`, the period cache and the allowance are all the real
    ones, so the configuration under test is the file onboarding just wrote.
    """
    session = RecordingSession(page=page_of(1))
    monkeypatch.setattr(
        port_adapter,
        "Ksef2Port",
        lambda *, environment: RecordingPort(session_object=session, environment=environment),
    )
    recorder = Recorder(
        answers=[NIP, "", "", str(invoice_directory), "n", "n", "t"],
        secrets=[TOKEN],
    )
    code = cli.main(
        ["onboarding"],
        console=recorder.console,
        working_directory=configuration_file.parent,
        configuration_file=configuration_file,
        home=configuration_file.parent,
    )
    return code, recorder, session


def test_verify_reads_back_the_configuration_onboarding_just_wrote(
    onboarded_and_then_verified: tuple[int, Recorder, RecordingSession],
) -> None:
    code, recorder, _ = onboarded_and_then_verified

    assert (code, f"Odpytuję KSeF (test) jako {NIP}" in recorder.transcript) == (cli.EXIT_OK, True)


def test_verify_reaches_ksef_through_the_configuration_it_read(
    onboarded_and_then_verified: tuple[int, Recorder, RecordingSession],
) -> None:
    # The question actually left for KSeF, and as the buyer: a configuration
    # read wrongly would have stopped the command before any query was asked.
    _, _, session = onboarded_and_then_verified

    assert session.asked == [ksef_port.SubjectRole.BUYER]


def test_verify_greets_the_subject_the_onboarded_token_belongs_to(
    onboarded_and_then_verified: tuple[int, Recorder, RecordingSession],
) -> None:
    _, recorder, _ = onboarded_and_then_verified

    assert f"jesteś połączony jako {BUYER_NAME}" in recorder.transcript


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

    assert "zapisane inaczej" not in recorder.transcript


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


def test_doctor_does_not_crash_on_a_configuration_whose_nip_is_not_one(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    tmp_path: Path,
    invoice_directory: Path,
) -> None:
    # `doctor` is where a person goes when something is already wrong, so it
    # reports and keeps going rather than raising over the same bad value.
    path = tmp_path / "state" / config.CONFIGURATION_FILE
    config.save_configuration(
        Configuration(
            nip="nie-jest-nipem",
            environment=KsefEnvironment.TEST,
            keyring_backend="keyring.backends.SecretService",
            invoice_directory=invoice_directory,
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
        keyring_preflight,
        "inspect_collection_lock",
        lambda: keyring_preflight.CollectionLock.LOCKED,
    )
    monkeypatch.setattr(ksef_port, "check_connection", raiser(AssertionError("sięgnięto do KSeF")))
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=configured)

    assert (code, "locked" in recorder.transcript) == (cli.EXIT_UNUSABLE_KEYRING, True)


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


@pytest.fixture
def archive_root(subject_data_root: Path) -> Path:
    # One root for the trail and the archive both, so the purge writes its entry
    # beside the archive it emptied rather than into the data directory of
    # whoever runs the suite. The autouse fixture already substitutes it in
    # `paths`, which is the single place every store reads the layout from.
    return subject_data_root


@pytest.fixture
def archived_invoices(archive_root: Path) -> InvoiceArchive:
    stored = InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST)
    stored.store(package=retention_package(1, 2, 3))
    return stored


def purged_number(ordinal: int) -> str:
    return a_ksef_number(ordinal)


def test_purge_refuses_without_configuration(configuration_file: Path) -> None:
    recorder = Recorder()

    code = cli.main(["purge"], console=recorder.console, configuration_file=configuration_file)

    assert (code, "ksef-mcp onboarding" in recorder.transcript) == (
        cli.EXIT_NOT_CONFIGURED,
        True,
    )


@pytest.mark.parametrize("traversal", ["../../..", "../1234567890", "1234567890/../.."])
def test_purge_refuses_a_nip_that_points_outside_the_subject_tree(
    configured: Path,
    archived_invoices: InvoiceArchive,
    traversal: str,
) -> None:
    # GH-112. Everything this touches is under `tmp_path` — the archive fixture
    # writes there and the refusal has to come before any plan is built, so no
    # deletion is even considered.
    recorder = Recorder()

    code = cli.main(
        ["purge", "--nip", traversal],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, len(list(archived_invoices.invoice_directory.iterdir()))) == (
        cli.EXIT_INVALID_NIP,
        3,
    )


def test_purge_says_it_touched_nothing_when_it_refuses_the_nip(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder()

    cli.main(
        ["purge", "--nip", "../../.."], console=recorder.console, configuration_file=configured
    )

    assert "nie ruszam żadnego katalogu" in recorder.transcript


def test_purge_never_asks_for_confirmation_on_a_refused_nip(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    # The confirmation prompt is a guard against a mistake, not against bad
    # input. A refusal that reached it would be asking the person to approve a
    # plan the tool should never have drawn up.
    recorder = Recorder()

    cli.main(
        ["purge", "--nip", "../../.."], console=recorder.console, configuration_file=configured
    )

    assert recorder.prompts == []


def test_purge_accepts_a_grouped_nip_as_the_same_subject(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    code = cli.main(
        ["purge", "--nip", "123-456-78-90", "--do", "2025-12-31"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, "123-456-78-90" in recorder.transcript) == (cli.EXIT_OK, False)


def test_token_status_refuses_a_nip_that_is_not_one() -> None:
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", "../../.."], console=recorder.console)

    assert (code, "dziesięć cyfr" in recorder.transcript) == (cli.EXIT_INVALID_NIP, True)


def test_token_status_reads_a_grouped_nip_as_the_stored_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The keyring key is the NIP, so two spellings used to store two tokens for
    # one taxpayer — and the second read looked like a token that had vanished.
    asked: list[str] = []

    def read(*, nip: str) -> token_store.StoredToken | None:
        asked.append(nip)
        return token_store.StoredToken(value=TOKEN, source=token_store.TokenSource.KEYRING)

    monkeypatch.setattr(token_store, "read_token", read)
    recorder = Recorder()

    cli.main(["token", "status", "--nip", "123-456-78-90"], console=recorder.console)

    assert asked == [NIP]


def test_verify_refuses_a_configuration_whose_nip_is_not_one(
    tmp_path: Path,
    invoice_directory: Path,
) -> None:
    path = tmp_path / "state" / config.CONFIGURATION_FILE
    config.save_configuration(
        Configuration(
            nip="nie-jest-nipem",
            environment=KsefEnvironment.TEST,
            keyring_backend="keyring.backends.SecretService",
            invoice_directory=invoice_directory,
        ),
        path=path,
    )
    recorder = Recorder()

    code = cli.main(["verify"], console=recorder.console, configuration_file=path)

    assert (code, "dziesięć cyfr" in recorder.transcript) == (cli.EXIT_INVALID_NIP, True)


def test_onboarding_asks_again_when_the_nip_is_not_one(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    accepting_token_store: list[tuple[str, str]],
    configuration_file: Path,
    invoice_directory: Path,
) -> None:
    recorder = Recorder(
        answers=["nie-nip", NIP, "", "", str(invoice_directory), "n", "n", ""],
        secrets=[TOKEN],
    )

    cli.main(
        ["onboarding"],
        console=recorder.console,
        working_directory=configuration_file.parent,
        configuration_file=configuration_file,
        home=configuration_file.parent,
    )

    assert accepting_token_store == [(NIP, TOKEN)]


def test_onboarding_stores_one_spelling_however_the_nip_was_typed(
    healthy_node: None,
    usable_keyring: keyring_preflight.KeyringReport,
    accepting_token_store: list[tuple[str, str]],
    configuration_file: Path,
    invoice_directory: Path,
) -> None:
    # GH-111: the grouped spelling used to become both a second keyring entry
    # and a second archive directory, with nothing said about either.
    recorder = Recorder(
        answers=["123-456-78-90", "", "", str(invoice_directory), "n", "n", ""],
        secrets=[TOKEN],
    )

    cli.main(
        ["onboarding"],
        console=recorder.console,
        working_directory=configuration_file.parent,
        configuration_file=configuration_file,
        home=configuration_file.parent,
    )

    assert config.load_configuration(path=configuration_file).nip == NIP  # type: ignore[union-attr]


def test_purge_refuses_a_window_that_ends_before_it_begins(configured: Path) -> None:
    recorder = Recorder()

    code = cli.main(
        ["purge", "--od", "2026-01-01", "--do", "2025-01-01"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, "covers no day" in recorder.transcript) == (cli.EXIT_INVALID_WINDOW, True)


def test_purge_deletes_nothing_when_the_window_matches_nothing(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder()

    code = cli.main(
        ["purge", "--do", "2020-01-01"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, "Nic nie pasuje" in recorder.transcript) == (cli.EXIT_OK, True)


def test_purge_names_every_invoice_before_asking(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["n"])

    cli.main(
        ["purge", "--do", "2024-12-31"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert purged_number(1) in recorder.transcript


def test_purge_keeps_the_archive_when_the_answer_is_no(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["n"])

    code = cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert (code, len(list(archived_invoices.invoice_directory.iterdir()))) == (
        cli.EXIT_PURGE_DECLINED,
        3,
    )


def test_purge_deletes_the_bodies_once_confirmed(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    code = cli.main(
        ["purge", "--do", "2025-12-31"],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (code, sorted(path.stem for path in archived_invoices.invoice_directory.iterdir())) == (
        cli.EXIT_OK,
        [purged_number(3)],
    )


def test_purge_leaves_the_deduplication_index_untouched(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert archived_invoices.load_index().known == frozenset(
        purged_number(ordinal) for ordinal in (1, 2, 3)
    )


def test_purge_writes_the_deletion_to_the_audit_trail(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    recorded = AuditTrail(nip=NIP, environment=KsefEnvironment.TEST).entries()
    assert [(entry.operation, entry.disclosure, entry.document_count) for entry in recorded] == [
        ("purge_archive", Disclosure.REMOVAL, 3)
    ]


def test_purge_records_a_human_at_a_terminal_as_the_basis(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    recorded = AuditTrail(nip=NIP, environment=KsefEnvironment.TEST).entries()
    assert recorded[0].authorisation.basis is AuthorisationBasis.OPERATOR


def test_purge_reports_that_the_index_still_remembers(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    recorder = Recorder(answers=["t"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert "pamięta nadal 3 numerów KSeF" in recorder.transcript


def test_purge_cuts_another_subject_when_asked_for_one(
    configured: Path,
    archive_root: Path,
) -> None:
    departed = InvoiceArchive(nip=DEPARTED_NIP, environment=KsefEnvironment.TEST)
    departed.store(package=retention_package(1, 2, 3))
    kept = InvoiceArchive(nip=NIP, environment=KsefEnvironment.TEST)
    kept.store(package=retention_package(1, 2, 3))
    recorder = Recorder(answers=["t"])

    cli.main(
        ["purge", "--nip", DEPARTED_NIP],
        console=recorder.console,
        configuration_file=configured,
    )

    assert (
        list(departed.invoice_directory.iterdir()),
        len(list(kept.invoice_directory.iterdir())),
    ) == ([], 3)


def test_purge_warns_about_a_file_it_will_not_delete(
    configured: Path,
    archived_invoices: InvoiceArchive,
) -> None:
    (archived_invoices.invoice_directory / "notatka.xml").write_bytes(b"<x/>")
    recorder = Recorder(answers=["n"])

    cli.main(["purge"], console=recorder.console, configuration_file=configured)

    assert "notatka.xml" in recorder.transcript


@pytest.mark.parametrize(
    ("arguments", "described"),
    [
        ([], "od początku archiwum do dziś"),
        (["--od", "2025-01-01"], "od 2025-01-01 do dziś"),
        (["--do", "2025-01-01"], "od początku archiwum do 2025-01-01"),
        (["--od", "2024-01-01", "--do", "2025-01-01"], "od 2024-01-01 do 2025-01-01"),
    ],
)
def test_purge_restates_the_window_it_was_given(
    configured: Path,
    archived_invoices: InvoiceArchive,
    arguments: list[str],
    described: str,
) -> None:
    recorder = Recorder(answers=["n"])

    cli.main(["purge", *arguments], console=recorder.console, configuration_file=configured)

    assert described in recorder.transcript


def test_no_subcommand_runs_the_mcp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(cli, "run_mcp_server", lambda: started.append("run"))
    recorder = Recorder()

    code = cli.main([], console=recorder.console)

    assert (code, started) == (cli.EXIT_OK, ["run"])
