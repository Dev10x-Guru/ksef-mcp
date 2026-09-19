from ksef_mcp.errors import KsefMcpInputRejected


class KsefPortError(RuntimeError):
    """Every failure the port lets out. Callers catch this and nothing else."""


class KsefRequestRejected(KsefPortError):
    """The port refused to send the call — the request was wrong before KSeF saw it."""


# Deliberately outside `KsefPortError`, although they live beside it: nothing
# was sent and no port was involved, so calling them port failures made every
# reader of `pdf.py` and `retention.py` catch "the port refused" to mean "this
# is not a KSeF number" (GH-170). The cost was not only in the reading: the
# period cache caught the port root to mean "damaged entry", and that root also
# covers `KsefUnreachable` and `KsefAuthenticationFailed`, so a real outage was
# answered as a cache miss.
class InvalidKsefIdentifier(KsefMcpInputRejected):
    """A string offered as a KSeF number is not shaped like one."""


class InvalidPeriod(KsefMcpInputRejected):
    """A window this client will not ask about: inverted, or wider than KSeF answers."""


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
