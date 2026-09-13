import pytest

from ksef_mcp import preflight


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def without_a_secret_service(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite has to say the same thing on a laptop with a live D-Bus session
    # and in CI without one, so the probe is answered here rather than by
    # whichever machine happens to run it. Tests about the lock state say so.
    monkeypatch.setattr(preflight, "load_secret_service", lambda: None)
