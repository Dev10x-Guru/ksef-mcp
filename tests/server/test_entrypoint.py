import pytest

from ksef_mcp.diagnostics import technical_log
from ksef_mcp.server import main, server


@pytest.fixture
def recorded_run_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(server, "run", lambda: calls.append("run"))
    main()
    return calls


def test_main_starts_the_server(recorded_run_calls: list[str]) -> None:
    assert recorded_run_calls == ["run"]


def test_the_journal_is_open_before_the_first_frame_is_served(
    recorded_run_calls: list[str],
) -> None:
    # A refusal during the very first tool call has to land somewhere (GH-116),
    # and the journal must not hand records up to a root the host may have
    # pointed at stdout.
    journal = technical_log()

    assert journal.handlers != []
    assert journal.propagate is False
