"""Testy `bin/ksef_live.py` — uruchamiacza testów KSeF na żywo.

Nic tu nie sięga do sieci ani nie uruchamia pytesta naprawdę: podprocess
zastępuje rejestrator, a katalog repozytorium — `tmp_path`. Sprawdzane jest
to, co skrypt obiecuje: skąd bierze poświadczenia, że środowisko jest zawsze
testowe i że NIP ani token nie wyciekają do komunikatów.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "ksef_live.py"


def load_module() -> object:
    specification = importlib.util.spec_from_file_location("ksef_live", SCRIPT)
    module = importlib.util.module_from_spec(specification)
    # Zarejestrowany przed wykonaniem: @dataclass rozwiązuje adnotacje przez
    # sys.modules[cls.__module__], pusty dla modułu ładowanego ze ścieżki.
    sys.modules["ksef_live"] = module
    specification.loader.exec_module(module)
    return module


ksef_live = load_module()

NIP = "1111111111"

TOKEN = "20260101-EC-0000000000-0000000000-00|nip-1111111111|syntetyczny"


class Recorder:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[dict[str, object]] = []

    def __call__(self, command: list[str], **options: object) -> subprocess.CompletedProcess:
        self.calls.append({"command": command, **options})
        return subprocess.CompletedProcess(args=command, returncode=self.returncode)


@pytest.fixture
def secrets_on_disk(tmp_path: Path) -> Path:
    (tmp_path / "ksef.secrets.env").write_text(
        f"# lokalne sekrety\nKSEF_LIVE_TEST_NIP={NIP}\nexport KSEF_LIVE_TEST_TOKEN='{TOKEN}'\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def recorded_run(secrets_on_disk: Path) -> tuple[int, Recorder, Path]:
    recorder = Recorder()
    exit_code = ksef_live.main(
        ["-k", "limits"], environment={}, root=secrets_on_disk, runner=recorder
    )
    return exit_code, recorder, secrets_on_disk


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A=1\nB=2\n", {"A": "1", "B": "2"}),
        ("# komentarz\n\nA=1\n", {"A": "1"}),
        ("export A=1\n", {"A": "1"}),
        ("A='x|y'\nB=\"z\"\n", {"A": "x|y", "B": "z"}),
        ("A=a=b\n", {"A": "a=b"}),
        ("bez-znaku-rownosci\n", {}),
    ],
    ids=["pary", "komentarz-i-pusta", "export", "cudzyslowy", "znak-w-wartosci", "smiec"],
)
def test_the_secrets_file_parses_like_an_env_file(text: str, expected: dict[str, str]) -> None:
    assert ksef_live.parsed_secrets(text) == expected


def test_credentials_come_from_the_untracked_file(secrets_on_disk: Path) -> None:
    credentials = ksef_live.resolved_credentials(environment={}, root=secrets_on_disk)
    assert (credentials.nip, credentials.token) == (NIP, TOKEN)


def test_environment_variables_win_over_the_file(secrets_on_disk: Path) -> None:
    credentials = ksef_live.resolved_credentials(
        environment={"KSEF_LIVE_TEST_NIP": "2222222222", "KSEF_LIVE_TEST_TOKEN": "z-sekretu"},
        root=secrets_on_disk,
    )
    assert (credentials.nip, credentials.token) == ("2222222222", "z-sekretu")


def test_credentials_work_without_a_file(tmp_path: Path) -> None:
    credentials = ksef_live.resolved_credentials(
        environment={"KSEF_LIVE_TEST_NIP": NIP, "KSEF_LIVE_TEST_TOKEN": TOKEN},
        root=tmp_path,
    )
    assert credentials.token == TOKEN


def test_missing_credentials_name_both_keys(tmp_path: Path) -> None:
    with pytest.raises(
        ksef_live.CredentialsMissing, match="KSEF_LIVE_TEST_NIP, KSEF_LIVE_TEST_TOKEN"
    ):
        ksef_live.resolved_credentials(environment={}, root=tmp_path)


def test_an_unfilled_template_counts_as_missing(tmp_path: Path) -> None:
    example = SCRIPT.parent.parent / "ksef.secrets.env.example"
    (tmp_path / "ksef.secrets.env").write_text(
        example.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(
        ksef_live.CredentialsMissing, match="KSEF_LIVE_TEST_NIP, KSEF_LIVE_TEST_TOKEN"
    ):
        ksef_live.resolved_credentials(environment={}, root=tmp_path)


def test_the_credentials_never_print_their_values() -> None:
    shown = repr(ksef_live.Credentials(nip=NIP, token=TOKEN))
    leaked = NIP in shown or TOKEN in shown
    assert not leaked


def test_the_run_writes_a_configuration_pinned_to_test(
    recorded_run: tuple[int, Recorder, Path],
) -> None:
    _, _, root = recorded_run
    configuration = root / ".tmp" / "ksef-live" / "dane" / "ksef-mcp" / "configuration.json"
    document = json.loads(configuration.read_text(encoding="utf-8"))
    assert document["environment"] == "test"


def test_the_configuration_names_the_test_subject(recorded_run: tuple[int, Recorder, Path]) -> None:
    _, _, root = recorded_run
    configuration = root / ".tmp" / "ksef-live" / "dane" / "ksef-mcp" / "configuration.json"
    assert json.loads(configuration.read_text(encoding="utf-8"))["nip"] == NIP


def test_the_configuration_is_readable_only_by_its_owner(
    recorded_run: tuple[int, Recorder, Path],
) -> None:
    _, _, root = recorded_run
    configuration = root / ".tmp" / "ksef-live" / "dane" / "ksef-mcp" / "configuration.json"
    assert configuration.stat().st_mode & 0o777 == 0o600


def test_the_run_selects_only_live_tests(recorded_run: tuple[int, Recorder, Path]) -> None:
    _, recorder, _ = recorded_run
    assert recorder.calls[0]["command"] == [
        "uv",
        "run",
        "pytest",
        "-m",
        "ksef_live",
        "--no-cov",
        "-rs",
        "-k",
        "limits",
    ]


def test_the_run_hands_the_token_through_the_fallback_variable(
    recorded_run: tuple[int, Recorder, Path],
) -> None:
    _, recorder, _ = recorded_run
    assert recorder.calls[0]["env"]["KSEF_TOKEN"] == TOKEN


def test_the_run_points_the_data_home_at_the_live_directory(
    recorded_run: tuple[int, Recorder, Path],
) -> None:
    _, recorder, root = recorded_run
    assert recorder.calls[0]["env"]["XDG_DATA_HOME"] == str(root / ".tmp" / "ksef-live" / "dane")


def test_the_run_reports_pytests_exit_code(secrets_on_disk: Path) -> None:
    exit_code = ksef_live.main(
        [], environment={}, root=secrets_on_disk, runner=Recorder(returncode=5)
    )
    assert exit_code == 5


def test_missing_credentials_stop_before_pytest(tmp_path: Path) -> None:
    recorder = Recorder()
    exit_code = ksef_live.main([], environment={}, root=tmp_path, runner=recorder)
    assert (exit_code, recorder.calls) == (2, [])
