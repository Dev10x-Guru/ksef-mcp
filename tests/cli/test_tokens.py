from collections.abc import Callable

import pytest

from ksef_mcp import cli
from ksef_mcp.storage import token_store
from tests.cli.conftest import NIP, TOKEN, Recorder
from tests.conftest import raiser


def removal(*, removed: bool, exported: bool) -> Callable[..., token_store.Removal]:
    return lambda **kwargs: token_store.Removal(
        removed_from_keyring=removed,
        still_exported=exported,
    )


def test_token_set_stores_and_confirms(
    accepting_token_store: list[tuple[str, str]],
) -> None:
    recorder = Recorder(secrets=[TOKEN])

    code = cli.main(["token", "set", "--nip", NIP], console=recorder.console)

    assert (code, accepting_token_store) == (cli.EXIT_OK, [(NIP, TOKEN)])


def test_token_delete_reports_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "delete_token", removal(removed=True, exported=False))
    recorder = Recorder()

    code = cli.main(["token", "delete", "--nip", NIP], console=recorder.console)

    assert (code, "usunięty z keyringu" in recorder.transcript) == (cli.EXIT_OK, True)


def test_token_delete_reports_nothing_to_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "delete_token", removal(removed=False, exported=False))
    recorder = Recorder()

    code = cli.main(["token", "delete", "--nip", NIP], console=recorder.console)

    assert code == cli.EXIT_NO_TOKEN


def test_token_delete_does_not_claim_revocation_while_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(token_store, "delete_token", removal(removed=True, exported=True))
    recorder = Recorder()

    cli.main(["token", "delete", "--nip", NIP], console=recorder.console)

    assert "token wciąż działa" in recorder.transcript


def test_token_status_reports_a_stored_token(stored_token: None) -> None:
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert (code, TOKEN in recorder.transcript) == (cli.EXIT_OK, False)


def test_token_status_names_the_source(stored_token: None) -> None:
    recorder = Recorder()

    cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert "keyringu systemowego" in recorder.transcript


def test_token_status_names_the_exported_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        lambda **kwargs: token_store.StoredToken(
            value=TOKEN,
            source=token_store.TokenSource.ENVIRONMENT,
        ),
    )
    recorder = Recorder()

    cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert token_store.FALLBACK_ENVIRONMENT_VARIABLE in recorder.transcript


def test_an_unusable_keyring_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        token_store,
        "read_token",
        raiser(token_store.TokenStoreUnavailable("brak magazynu")),
    )
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert (code, "brak magazynu" in recorder.transcript) == (
        cli.EXIT_UNUSABLE_KEYRING,
        True,
    )


def test_token_status_reports_a_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_store, "read_token", lambda **kwargs: None)
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", NIP], console=recorder.console)

    assert (code, "Brak tokenu" in recorder.transcript) == (cli.EXIT_NO_TOKEN, True)


def test_token_status_refuses_a_nip_that_is_not_one() -> None:
    recorder = Recorder()

    code = cli.main(["token", "status", "--nip", "../../.."], console=recorder.console)

    assert (code, "dziesięć cyfr" in recorder.transcript) == (cli.EXIT_INVALID_NIP, True)


def test_token_status_reads_a_grouped_nip_as_the_stored_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The keyring key is the NIP, so two spellings used to store two tokens for
    # one taxpayer — and the second read looked like a token that had vanished.
    asked: list[str] = []

    def read(*, nip: str) -> token_store.StoredToken | None:
        asked.append(nip)
        return token_store.StoredToken(value=TOKEN, source=token_store.TokenSource.KEYRING)

    monkeypatch.setattr(token_store, "read_token", read)
    recorder = Recorder()

    cli.main(["token", "status", "--nip", "123-456-78-90"], console=recorder.console)

    assert asked == [NIP]
