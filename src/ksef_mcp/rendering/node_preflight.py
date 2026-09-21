"""Whether a Node runtime new enough for the Ministry's renderer is reachable.

Separate from `keyring_preflight` because the two checks share nothing but the
moment they are run at. Together in one module, `pdf` — which renders invoices
and never touches a secret — imported the keyring probe, `keyring.backend` and
the D-Bus code with it (GH-125). The `doctor` command still runs both; that is
a decision of the command, not a property of either check.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# The MF invoice generator declares 22.14.0 as its floor. D-029 pins the
# repository to the version the render was verified on, but that pin ships
# with the vendored bundle — until then this floor is all we can require.
MINIMUM_NODE_VERSION: Final[tuple[int, int, int]] = (22, 14, 0)

NODE_VERSION_FILE: Final[str] = ".node-version"

PROJECT_MARKER: Final[str] = ".git"

NODE_QUERY_TIMEOUT_SECONDS: Final[float] = 10.0


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
