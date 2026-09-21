from collections.abc import Callable
from pathlib import Path

import pytest

from ksef_mcp import cli, config, keyring_preflight, ksef_port
from ksef_mcp.cli import onboarding
from ksef_mcp.ksef_port import adapter as port_adapter
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.metadata import SERVER_NAME
from ksef_mcp.setup import client
from ksef_mcp.storage import token_store
from tests.cli.conftest import NIP, TOKEN, Recorder, keyring_report
from tests.invoices.test_statement import RecordingPort, RecordingSession, page_of
from tests.support.synthetic import BUYER_NAME


@pytest.fixture
def single_backend_choice() -> tuple[str, Recorder]:
    recorder = Recorder()
    chosen = onboarding.choose_keyring_backend(
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
    chosen = onboarding.choose_keyring_backend(recorder.console, report)
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

    assert (
        onboarding.choose_keyring_backend(recorder.console, report)
        == "keyring.backends.SecretService"
    )


def test_environment_defaults_to_the_test_registry() -> None:
    recorder = Recorder(answers=[""])

    assert onboarding.choose_environment(recorder.console) is KsefEnvironment.TEST


@pytest.fixture
def environment_rejected_then_named() -> tuple[KsefEnvironment, Recorder]:
    recorder = Recorder(answers=["produkcja", "production"])
    chosen = onboarding.choose_environment(recorder.console)
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

    assert onboarding.choose_environment(recorder.console) is expected


def test_environment_list_explains_the_difference_between_test_and_demo() -> None:
    recorder = Recorder(answers=[""])

    onboarding.choose_environment(recorder.console)

    assert "piaskownica" in recorder.transcript


def test_environment_rejects_a_number_past_the_list() -> None:
    recorder = Recorder(answers=["4", "1"])

    assert onboarding.choose_environment(recorder.console) is KsefEnvironment.TEST


def test_invoice_directory_is_created_and_reported(invoice_directory: Path) -> None:
    recorder = Recorder(answers=[str(invoice_directory)])

    chosen = onboarding.choose_invoice_directory(recorder.console, nip=NIP)

    assert (chosen, chosen.is_dir()) == (invoice_directory, True)


def test_an_existing_invoice_directory_is_left_as_it_was(tmp_path: Path) -> None:
    shared = tmp_path / "wspolny"
    shared.mkdir(mode=0o755)
    recorder = Recorder(answers=[str(shared)])

    onboarding.choose_invoice_directory(recorder.console, nip=NIP)

    assert "zostawiam uprawnienia 0755" in recorder.transcript


def test_invoice_directory_in_a_synced_folder_is_flagged(tmp_path: Path) -> None:
    recorder = Recorder(answers=[str(tmp_path / "Dropbox" / "faktury")])

    onboarding.choose_invoice_directory(recorder.console, nip=NIP)

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
    monkeypatch.setattr(onboarding, "run_verify", lambda console, **kwargs: cli.EXIT_OK)

    recorder = onboarding_with(["n", "n", "t"])

    assert "Gdy zechcesz to potwierdzić" not in recorder.transcript


# The crossing GH-76 asked for, and the one neither side tested (GH-177).
# `test_accepting_the_check_runs_verify` above substitutes `run_verify` itself,
# so it proves the offer is taken and nothing about what verify then reads; the
# `test_verify_*` tests in `test_diagnostics.py` start from a configuration a
# fixture wrote by hand. Between the two sits the seam that matters: a path, an
# environment or a NIP written by onboarding in a spelling verify does not read
# back would look exactly like a working pair on both sides.


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


def test_onboarding_points_at_the_verify_command(
    completed_onboarding: tuple[int, Recorder],
) -> None:
    _, recorder = completed_onboarding

    assert "ksef-mcp verify" in recorder.transcript


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
