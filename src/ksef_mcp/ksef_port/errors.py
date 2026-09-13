class KsefPortError(RuntimeError):
    """Every failure the port lets out. Callers catch this and nothing else."""


class KsefRequestRejected(KsefPortError):
    """The port refused to send the call — the request was wrong before KSeF saw it."""


class KsefAuthenticationFailed(KsefPortError):
    """KSeF answered, and the answer was: not you, or not for this subject."""


class KsefRateLimited(KsefPortError):
    """KSeF answered 429. `retry_after` is the server's own wait, in seconds."""

    def __init__(self, message: str, *, retry_after: int | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class KsefRefused(KsefPortError):
    """KSeF answered, and the answer was an error — the call reached the registry."""


class KsefUnreachable(KsefPortError):
    """No answer existed. DNS, TCP, TLS or a timeout before the first byte."""
