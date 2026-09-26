"""What every `ksef_live` test needs before it may reach the registry.

Shared by the render check and the contract check (GH-82, GH-178). Each helper
turns a missing precondition into a skip rather than a failure: a machine that
was never onboarded, or one onboarded for production, says nothing about the
code under test.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from ksef_mcp import config
from ksef_mcp.allowance import Allowance
from ksef_mcp.ksef_port.types import KsefEnvironment, SubjectRole
from ksef_mcp.storage import token_store
from ksef_mcp.storage.period_cache import MeteredPeriods, PeriodCache

# The window `verify` asks about: wide enough to hold a purchase on the test
# registry, well under the port's ceiling, and one query from the hourly
# twenty (D-031).
LOOKBACK = timedelta(days=30)

# The roles a subject holds on its own invoices. Third parties and authorised
# subjects are rarer on a test account, and each role asked is one more query.
OWN_ROLES = (SubjectRole.BUYER, SubjectRole.SELLER)


def live_configuration() -> config.Configuration:
    """The subject this machine was onboarded for, or a skip saying why not.

    Read from the real configuration path, not the one the suite's autouse
    fixture redirects: a live test wants the subject a person set up, and a
    missing one is a reason to skip rather than a failure.
    """
    configuration = config.load_configuration()
    if configuration is None:
        pytest.skip("brak konfiguracji — uruchom `ksef-mcp onboarding`")
    if configuration.environment is KsefEnvironment.PRODUCTION:
        # Never from a test (CLAUDE.md): production limits are a tenth of test
        # and the Ministry logs every breach against the subject.
        pytest.skip("podmiot skonfigurowany na produkcję — test na żywo idzie tylko na test/demo")
    return configuration


def live_token(*, nip: str) -> token_store.StoredToken:
    stored = token_store.read_token(nip=nip)
    if stored is None:
        # Without the NIP: the manual workflow's log is public (CLAUDE.md).
        pytest.skip(
            "brak tokenu dla skonfigurowanego podmiotu — `ksef-mcp token set` albo KSEF_TOKEN"
        )
    return stored


def readers_for(configuration: config.Configuration, *, root: Path) -> MeteredPeriods:
    """The same metered pairing `verify` uses, rooted under the test's directory.

    Rooted explicitly rather than through the autouse redirect, because the
    live fixtures are module-scoped and the redirect is not. The counter still
    counts — it just counts into a directory that disappears with the run.
    """
    return MeteredPeriods(
        cache=PeriodCache(nip=configuration.nip, environment=configuration.environment, root=root),
        allowance=Allowance(
            nip=configuration.nip,
            environment=configuration.environment,
            data_root=root,
            cache_root=root,
        ),
    )
