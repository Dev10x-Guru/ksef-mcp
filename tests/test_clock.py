from datetime import UTC

from ksef_mcp.clock import now_utc


def test_the_shared_clock_reads_utc() -> None:
    assert now_utc().tzinfo is UTC
