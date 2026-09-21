import pytest

from ksef_mcp import cli
from ksef_mcp.cli import parser as parser_module
from ksef_mcp.cli.parser import build_parser
from tests.cli.conftest import NIP, Recorder

# Jedna konwencja zamiast dwóch (#137). Podparser niosący swój handler nie ma
# drugiej pisowni nazwy komendy, którą można rozjechać z pierwszą — a właśnie
# to kończyło się cichym uruchomieniem serwera MCP zamiast błędem.

SUBCOMMANDS: tuple[list[str], ...] = (
    ["onboarding"],
    ["doctor"],
    ["verify"],
    ["purge"],
    ["token", "status", "--nip", NIP],
    ["skill", "install", "--scope", "user"],
)


def test_no_subcommand_runs_the_mcp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(parser_module, "run_mcp_server", lambda: started.append("run"))
    recorder = Recorder()

    code = cli.main([], console=recorder.console)

    assert (code, started) == (cli.EXIT_OK, ["run"])


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_every_subcommand_carries_a_handler_of_its_own(command: list[str]) -> None:
    serving = build_parser().parse_args([]).handler

    assert build_parser().parse_args(command).handler is not serving


def test_an_unknown_command_is_refused_rather_than_serving() -> None:
    # Literówka w nazwie komendy nie może po cichu wylądować na domyślnym
    # handlerze — integrator debugujący skrypt ma dostać błąd, nie serwer.
    recorder = Recorder()

    with pytest.raises(SystemExit):
        cli.main(["doctorr"], console=recorder.console)
