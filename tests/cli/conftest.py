"""The terminal every command test drives, and the state it starts from."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from ksef_mcp import cli, config, keyring_preflight
from ksef_mcp.config import Configuration
from ksef_mcp.ksef_port.types import KsefEnvironment
from ksef_mcp.rendering import node_preflight
from ksef_mcp.storage import token_store

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
def working_directory(tmp_path: Path) -> Path:
    return tmp_path / "zestawienia"


@pytest.fixture
def configured(configuration_file: Path, working_directory: Path) -> Path:
    config.save_configuration(
        Configuration(
            nip=NIP,
            environment=KsefEnvironment.TEST,
            keyring_backend="keyring.backends.SecretService",
            working_directory=working_directory,
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
