import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import keyring.backend

# The MF invoice generator declares 22.14.0 as its floor. D-029 pins the
# repository to the version the render was verified on, but that pin ships
# with the vendored bundle — until then this floor is all we can require.
MINIMUM_NODE_VERSION: Final[tuple[int, int, int]] = (22, 14, 0)

NODE_VERSION_FILE: Final[str] = ".node-version"

PROJECT_MARKER: Final[str] = ".git"

NODE_QUERY_TIMEOUT_SECONDS: Final[float] = 10.0

# `fail` is the sentinel keyring falls back to when no OS store is reachable,
# and `chainer` only delegates to the others. Neither is something a user can
# deliberately pick, so neither belongs on the choice list (D-004).
UNSELECTABLE_BACKEND_MODULES: Final[frozenset[str]] = frozenset(
    {"keyring.backends.fail", "keyring.backends.chainer"}
)


@dataclass(frozen=True)
class NodeReport:
    executable: str | None
    version: tuple[int, int, int] | None
    required: tuple[int, int, int]
    pinned: tuple[int, int, int] | None
    pinned_by: Path | None

    @property
    def pin_is_below_the_generator(self) -> bool:
        return self.pinned is not None and self.pinned < MINIMUM_NODE_VERSION

    @property
    def satisfies_requirement(self) -> bool:
        return self.version is not None and self.version >= self.required


@dataclass(frozen=True)
class KeyringBackendReport:
    module: str
    priority: float


@dataclass(frozen=True)
class KeyringReport:
    backends: tuple[KeyringBackendReport, ...]
    preferred: str | None

    @property
    def usable(self) -> bool:
        return self.preferred is not None


def parse_version(raw: str) -> tuple[int, int, int] | None:
    parts = raw.strip().removeprefix("v").split(".")
    if len(parts) != 3:
        return None
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        return None
    return major, minor, patch


def read_pinned_node_version(
    *,
    working_directory: Path,
) -> tuple[Path, tuple[int, int, int]] | None:
    # Walked upwards, because the pin lives at a repository root and someone
    # running this from a subdirectory means the same pin. Two guards keep it
    # from picking up a stranger's file: the walk stops at the home directory,
    # and only a pin sitting next to a `.git` marker counts — fnm and nvm users
    # keep a global `~/.node-version`, which is their preference for their own
    # work, not a statement about this project.
    for candidate in (working_directory, *working_directory.parents):
        if candidate == Path.home():
            return None
        if not (candidate / PROJECT_MARKER).exists():
            continue
        pin_file = candidate / NODE_VERSION_FILE
        if not pin_file.is_file():
            return None
        pinned = parse_version(pin_file.read_text(encoding="utf-8"))
        return None if pinned is None else (pin_file, pinned)
    return None


def query_node_version(*, executable: str) -> tuple[int, int, int] | None:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=NODE_QUERY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_version(completed.stdout)


def inspect_node(*, working_directory: Path) -> NodeReport:
    pin = read_pinned_node_version(working_directory=working_directory)
    pinned_by, pinned = pin if pin is not None else (None, None)
    executable = shutil.which("node")
    return NodeReport(
        executable=executable,
        version=None if executable is None else query_node_version(executable=executable),
        # A pin may only raise the floor. Letting it lower one would turn an
        # honest "I take the MF minimum" into a confident falsehood: a project
        # pinned to 20.11.0 would report green while the MF generator, which
        # needs 22.14.0, refuses to start.
        required=MINIMUM_NODE_VERSION if pinned is None else max(pinned, MINIMUM_NODE_VERSION),
        pinned=pinned,
        pinned_by=pinned_by,
    )


def inspect_keyring() -> KeyringReport:
    # Enumeration alone: no read, no write, no prompt. That is what makes this
    # safe to call from a process that also speaks the stdio transport, where
    # an interactive password prompt would hang the protocol (D-004).
    discovered = tuple(
        KeyringBackendReport(
            module=type(backend).__module__,
            priority=float(backend.priority),
        )
        for backend in keyring.backend.get_all_keyring()
    )
    selectable = tuple(
        report for report in discovered if report.module not in UNSELECTABLE_BACKEND_MODULES
    )
    preferred = max(selectable, key=lambda report: report.priority).module if selectable else None
    return KeyringReport(backends=selectable, preferred=preferred)
