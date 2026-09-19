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


class PackageLinkExpired(KsefRefused):
    """The presigned link to a package part is no longer honoured by storage.

    Its own type because it is the one refusal that never heals by waiting: every
    later attempt on the same URL is refused the same way, so treating it as
    "retry next pass" leaves the subject type asking for a door that will not
    open again (GH-93). The fresh links, if any exist, come from KSeF.
    """


class KsefUnreachable(KsefPortError):
    """No answer existed. DNS, TCP, TLS or a timeout before the first byte."""
