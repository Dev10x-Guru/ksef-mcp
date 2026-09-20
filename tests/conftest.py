import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import pytest

from ksef_mcp import paths, preflight
from ksef_mcp.allowance import Allowance, now_utc
from ksef_mcp.diagnostics import LOG_FILE, configure_diagnostics, technical_log
from ksef_mcp.ksef_port.types import KsefEnvironment


def raiser(error: Exception) -> Callable[..., object]:
    """Substitute for anything whose failure is what the test is about."""

    def raise_it(*args: object, **kwargs: object) -> object:
        raise error

    return raise_it


def in_another_thread(work: Callable[[], object], *, timeout: float = 5.0) -> object:
    """Run `work` on a thread of its own, and re-raise here whatever it hit.

    A write lock is held per thread as much as per process, so a test about a
    second writer has to involve a genuinely second thread. Anything the thread
    raises is re-raised on this side, where `pytest.raises` can see it.
    """
    done: list[object] = []
    failures: list[BaseException] = []

    def run() -> None:
        try:
            done.append(work())
        except BaseException as failure:
            failures.append(failure)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive()
    if failures:
        raise failures[0]
    return done[0]


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
def subject_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Both platform roots under the test's own directory, substituted in one place.

    Every store reads its layout from `paths` now, so one pair of substitutions
    covers the audit trail, the spent counter and each store at once. It used to
    take a separate substitution per module, which meant a store added later
    silently wrote into the data directory of whoever ran the suite — a trail
    holding synthetic accesses, or a spent counter refusing that person's real
    calls for an hour against an allowance the suite never touched.
    """
    data = tmp_path / "dane"
    monkeypatch.setattr(paths, "user_data_path", lambda *, appname: data)
    monkeypatch.setattr(paths, "user_cache_path", lambda *, appname: tmp_path / "cache")
    return data


@pytest.fixture
def journal_restored() -> Iterator[None]:
    """Put the journal back the way it was found.

    It is a process-wide logger, so a test that points it somewhere has to
    return it — otherwise the next test reads this one's handlers and a suite
    passes or fails by the order it happened to run in.
    """
    log = technical_log()
    held = tuple(log.handlers)
    stamps = tuple(log.filters)
    propagated = log.propagate
    level = log.level
    yield
    opened = tuple(handler for handler in log.handlers if handler not in held)
    for handler in tuple(log.handlers):
        log.removeHandler(handler)
    for handler in opened:
        handler.close()
    for handler in held:
        log.addHandler(handler)
    log.filters = list(stamps)
    log.propagate = propagated
    log.setLevel(level)


@pytest.fixture
def journal(journal_restored: None, tmp_path: Path) -> Path:
    """The journal writing into the test's own directory, and the file it writes."""
    directory = tmp_path / "dziennik"
    configure_diagnostics(directory=directory)
    return directory / LOG_FILE


@pytest.fixture(autouse=True)
def without_a_secret_service(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite has to say the same thing on a laptop with a live D-Bus session
    # and in CI without one, so the probe is answered here rather than by
    # whichever machine happens to run it. Tests about the lock state say so.
    monkeypatch.setattr(preflight, "load_secret_service", lambda: None)
