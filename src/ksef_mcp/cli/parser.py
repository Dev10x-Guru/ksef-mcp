"""What the command line says, and which command it names. Nothing about doing it.

Every subparser carries the callable that answers it, set with
`set_defaults(handler=…)`. The chain of `if arguments.command == "…"` it
replaces had two conventions ten lines apart and one silent failure mode: a
name misspelled on one side of the comparison fell through to launching the MCP
server rather than saying the command was unknown (#137). A handler attached to
the parser that defines the command cannot be misspelled into a different
command — there is no second spelling of the name to keep in step.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date

from ksef_mcp import messages
from ksef_mcp.cli.context import CommandContext, CommandHandler
from ksef_mcp.cli.diagnostics import run_doctor, run_verify
from ksef_mcp.cli.exits import EXIT_INVALID_NIP, EXIT_OK
from ksef_mcp.cli.maintenance import run_purge
from ksef_mcp.cli.onboarding import run_onboarding
from ksef_mcp.cli.skills import run_skill_install
from ksef_mcp.cli.tokens import run_token_delete, run_token_set, run_token_status
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.paths import Nip, NipRejected
from ksef_mcp.server import main as run_mcp_server
from ksef_mcp.setup.skill import SkillScope

TokenAction = Callable[..., int]


def _serve(arguments: argparse.Namespace, context: CommandContext) -> int:
    run_mcp_server()
    return EXIT_OK


def _onboarding(arguments: argparse.Namespace, context: CommandContext) -> int:
    return run_onboarding(
        context.console,
        working_directory=context.working_directory,
        configuration_file=context.configuration_file,
        home=context.home,
    )


def _doctor(arguments: argparse.Namespace, context: CommandContext) -> int:
    return run_doctor(
        context.console,
        working_directory=context.working_directory,
        configuration_file=context.configuration_file,
    )


def _verify(arguments: argparse.Namespace, context: CommandContext) -> int:
    return run_verify(context.console, configuration_file=context.configuration_file)


def _purge(arguments: argparse.Namespace, context: CommandContext) -> int:
    return run_purge(
        context.console,
        nip=arguments.nip,
        received_from=arguments.received_from,
        received_to=arguments.received_to,
        configuration_file=context.configuration_file,
    )


def _skill_install(arguments: argparse.Namespace, context: CommandContext) -> int:
    return run_skill_install(
        context.console,
        scope=arguments.scope,
        home=context.home,
        working_directory=context.working_directory,
    )


def _token(arguments: argparse.Namespace, context: CommandContext) -> int:
    # The same normalisation the archive gets, for the same reason: this value
    # is the keyring key, so two spellings would store two tokens for one
    # taxpayer and the second one would look like a token that vanished.
    try:
        subject = str(Nip.parsed(arguments.nip))
    except NipRejected:
        context.console.write(messages.describe_rejected_nip())
        return EXIT_INVALID_NIP
    action: TokenAction = arguments.token_action
    return action(context.console, nip=subject)


def _add_purge(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    purge = subcommands.add_parser(
        "purge",
        help="Kasuje faktury z archiwum, zachowując indeks deduplikacji",
    )
    purge.set_defaults(handler=_purge)
    # The configured subject by default, but the subject dimension is offered:
    # an accounting office losing a client purges that client's own directory and
    # never a neighbour's (D-032, D-034).
    purge.add_argument("--nip", help="Podmiot do wyczyszczenia (domyślnie ten z konfiguracji)")
    purge.add_argument(
        "--od",
        dest="received_from",
        type=date.fromisoformat,
        help="Najwcześniejsza data wpływu do KSeF (RRRR-MM-DD), włącznie",
    )
    purge.add_argument(
        "--do",
        dest="received_to",
        type=date.fromisoformat,
        help="Najpóźniejsza data wpływu do KSeF (RRRR-MM-DD), włącznie",
    )


def _add_token(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    token = subcommands.add_parser("token", help="Zarządza tokenem w keyringu")
    token_actions = token.add_subparsers(dest="token_command", required=True)
    for action, help_text, run in (
        ("set", "Zapisuje token odczytany ze stdin bez echa", run_token_set),
        ("delete", "Usuwa token z magazynu", run_token_delete),
        ("status", "Mówi, czy token jest, nie pokazując wartości", run_token_status),
    ):
        action_parser = token_actions.add_parser(action, help=help_text)
        action_parser.add_argument("--nip", required=True)
        action_parser.set_defaults(handler=_token, token_action=run)


def _add_skill(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    skill_command = subcommands.add_parser("skill", help="Instaluje skill dla agenta")
    skill_actions = skill_command.add_subparsers(dest="skill_command", required=True)
    install = skill_actions.add_parser("install", help="Tworzy albo aktualizuje skill")
    install.set_defaults(handler=_skill_install)
    # No default scope on purpose: uvx runs from whatever directory happens to
    # be current, so a silently assumed scope would write the skill somewhere
    # the person never meant to look for it.
    install.add_argument(
        "--scope",
        required=True,
        type=SkillScope,
        choices=tuple(SkillScope),
        help="user: ~/.claude/skills, project: ./.claude/skills",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description=(
            "Serwer MCP dla KSeF. Bez argumentów uruchamia serwer na stdio, "
            "czego oczekuje klient MCP."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{SERVER_NAME} {VERSION}")
    # No subcommand means the server, and that is a default of the root parser
    # rather than the last branch of a chain — a subcommand that forgot its own
    # handler now fails here instead of quietly starting the server.
    parser.set_defaults(handler=_serve)
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("onboarding", help="Przeprowadza przez konfigurację").set_defaults(
        handler=_onboarding
    )
    subcommands.add_parser("doctor", help="Sprawdza warunki wstępne i kończy").set_defaults(
        handler=_doctor
    )
    subcommands.add_parser("verify", help="Odpytuje KSeF i pokazuje ostatnie faktury").set_defaults(
        handler=_verify
    )
    _add_purge(subcommands)
    _add_token(subcommands)
    _add_skill(subcommands)
    return parser


def dispatch(arguments: argparse.Namespace, *, context: CommandContext) -> int:
    """Whatever the parser said answers it. There is nothing left to decide here."""
    handler: CommandHandler = arguments.handler
    return handler(arguments, context)
