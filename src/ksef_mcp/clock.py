from datetime import UTC, datetime


def now_utc() -> datetime:
    """The one reading of the wall clock every module shares.

    A leaf on purpose: it imports nothing from `ksef_mcp`, so anything may
    depend on it. Nine identical copies lived in nine modules before, and a
    test freezing time had to patch the right one — patching the wrong copy
    still passed, because the assertion never looked at the clock it froze
    (GH-213).
    """
    return datetime.now(tz=UTC)
