class KsefMcpError(RuntimeError):
    """Every failure of this application whose message was written for a reader.

    Membership is a promise about the text, not about where the raise sits: the
    message names something the person in front of the client can act on, and it
    carries no NIP, no token and no line of invoice XML (D-011). The server
    reports anything under this root verbatim, so a subclass whose message would
    not survive being shown to a taxpayer does not belong here.

    The root exists because the alternative — naming each refusal in a tuple in
    the server — was tried and proved incomplete for three of five tools: a
    keyring collection that locked itself while the laptop slept answered
    `Error executing tool …` although the sentence explaining it was already
    written (GH-167). A promise kept at the class definition is checked by
    whoever writes the class; a promise kept in a distant tuple is checked by
    nobody.
    """


class KsefMcpInputRejected(KsefMcpError, ValueError):
    """Data handed in was refused before anything was attempted with it.

    Kept a `ValueError` as well, because that is what a caller validating an
    argument expects to catch and what the callers written before this root
    already catch. The `KsefMcpError` side is what carries the message to the
    client; the `ValueError` side is what keeps a local `except` honest about
    meaning "this input is wrong" rather than "something failed" (GH-170).
    """
