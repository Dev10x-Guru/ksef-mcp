from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from ksef_mcp import allowance as allowance_module
from ksef_mcp import audit, preflight
from ksef_mcp.allowance import Allowance, now_utc
from ksef_mcp.config import KsefEnvironment


def raiser(error: Exception) -> Callable[..., object]:
    """Substitute for anything whose failure is what the test is about."""

    def raise_it(*args: object, **kwargs: object) -> object:
        raise error

    return raise_it


def an_allowance(
    *,
    nip: str,
    environment: KsefEnvironment,
    root: Path | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> Allowance:
    """The protection a service is given, with both roots under the test's own.

    One `root` rather than two, because a test never cares which convention a
    file follows — only that neither lands in the directory of whoever ran the
    suite.
    """
    return Allowance(
        nip=nip,
        environment=environment,
        data_root=root,
        cache_root=root,
        clock=clock,
    )


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
def allowance_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Same reason as the audit trail above, and a sharper one: a spent counter
    # left in the runner's data directory would refuse that person's real calls
    # for an hour, against an allowance the suite never touched.
    monkeypatch.setattr(allowance_module, "user_data_path", lambda *, appname: tmp_path / "dane")
    monkeypatch.setattr(allowance_module, "user_cache_path", lambda *, appname: tmp_path / "cache")


@pytest.fixture(autouse=True)
def without_a_secret_service(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite has to say the same thing on a laptop with a live D-Bus session
    # and in CI without one, so the probe is answered here rather than by
    # whichever machine happens to run it. Tests about the lock state say so.
    monkeypatch.setattr(preflight, "load_secret_service", lambda: None)
