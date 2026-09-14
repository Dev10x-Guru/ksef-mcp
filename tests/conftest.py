from collections.abc import Callable
from pathlib import Path

import pytest

from ksef_mcp import audit, preflight


def raiser(error: Exception) -> Callable[..., object]:
    """Substitute for anything whose failure is what the test is about."""

    def raise_it(*args: object, **kwargs: object) -> object:
        raise error

    return raise_it


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def audit_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    # Every read tool now appends to the trail, so without this the suite would
    # write an audit file into the data directory of whoever ran it — and a
    # trail holding synthetic accesses is worse than no trail at all.
    root = tmp_path / "dane"
    monkeypatch.setattr(audit, "user_data_path", lambda *, appname: root)
    return root


@pytest.fixture(autouse=True)
def without_a_secret_service(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite has to say the same thing on a laptop with a live D-Bus session
    # and in CI without one, so the probe is answered here rather than by
    # whichever machine happens to run it. Tests about the lock state say so.
    monkeypatch.setattr(preflight, "load_secret_service", lambda: None)
