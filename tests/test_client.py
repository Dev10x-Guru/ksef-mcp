from subprocess import CompletedProcess, TimeoutExpired

import pytest

from conftest import raiser
from ksef_mcp import client
from ksef_mcp.metadata import SERVER_NAME


def completed(returncode: int) -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")


@pytest.fixture
def recorded_arguments(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append(arguments)
        return completed(0)

    monkeypatch.setattr(client.subprocess, "run", run)
    return calls


@pytest.fixture
def refusing_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.subprocess, "run", lambda *args, **kwargs: completed(1))


def test_an_absent_binary_is_reported_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.shutil, "which", lambda name: None)

    assert client.command_available() is False


def test_a_present_binary_is_reported_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.shutil, "which", lambda name: "/usr/bin/claude")

    assert client.command_available() is True


def test_registration_asks_the_client_to_run_the_server_through_uvx(
    recorded_arguments: list[list[str]],
) -> None:
    client.register(SERVER_NAME)

    assert recorded_arguments == [
        [client.CLIENT_EXECUTABLE, "mcp", "add", SERVER_NAME, "--", "uvx", SERVER_NAME]
    ]


def test_a_successful_registration_is_reported_as_such(
    recorded_arguments: list[list[str]],
) -> None:
    assert client.register(SERVER_NAME) is True


def test_a_known_server_is_recognised(recorded_arguments: list[list[str]]) -> None:
    assert client.already_registered(SERVER_NAME) is True


def test_an_unknown_server_is_not_recognised(refusing_client: None) -> None:
    assert client.already_registered(SERVER_NAME) is False


def test_a_refused_registration_is_reported_as_failed(refusing_client: None) -> None:
    assert client.register(SERVER_NAME) is False


@pytest.mark.parametrize(
    "failure",
    [
        OSError("brak uprawnień"),
        TimeoutExpired(cmd="claude", timeout=client.CLIENT_TIMEOUT_SECONDS),
    ],
)
def test_a_client_that_will_not_run_is_not_an_onboarding_failure(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    # Onboarding offers this as a convenience. A hung or missing binary leaves
    # the person with a command to copy, never with a half-finished setup.
    monkeypatch.setattr(client.subprocess, "run", raiser(failure))

    assert client.already_registered(SERVER_NAME) is False
