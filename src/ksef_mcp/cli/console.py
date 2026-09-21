"""The terminal, as three callables — so a test drives a command without a tty."""

from __future__ import annotations

import getpass
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

AFFIRMATIVE_ANSWERS: Final[frozenset[str]] = frozenset({"t", "tak"})


@dataclass(frozen=True)
class Console:
    write: Callable[[str], None]
    ask: Callable[[str], str]
    ask_secret: Callable[[str], str]


def default_console() -> Console:
    return Console(write=print, ask=input, ask_secret=getpass.getpass)


def ask_with_default(console: Console, *, prompt: str, default: str) -> str:
    return console.ask(f"{prompt} [{default}]: ").strip() or default


def ask_required(console: Console, *, prompt: str) -> str:
    while True:
        answer = console.ask(f"{prompt}: ").strip()
        if answer:
            return answer
        console.write("Wartość jest wymagana.")


def ask_secret_required(console: Console, *, prompt: str) -> str:
    while True:
        answer = console.ask_secret(f"{prompt}: ").strip()
        if answer:
            return answer
        console.write("Token jest wymagany.")


def affirmative(answer: str) -> bool:
    return answer.lower() in AFFIRMATIVE_ANSWERS
