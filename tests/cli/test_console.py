import getpass

from ksef_mcp import cli
from tests.cli.conftest import NIP, TOKEN, Recorder


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
