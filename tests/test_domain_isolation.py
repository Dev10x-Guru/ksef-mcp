"""ADR-102 read in the other direction: the domain must not see the protocol.

`tests/test_port_substitutability.py` guards the domain against the `ksef2`
SDK underneath it. Nothing guarded the symmetric edge — the `mcp` SDK lives
above, reached only by `ksef_mcp.server`, and that held on the reviewer's
memory alone. The day someone imports `ToolError` into `invoices/statement.py`
because it is convenient, this goes red instead of merging unremarked.
"""

import subprocess
import sys

import pytest

DOMAIN_MODULES = [
    "ksef_mcp.invoices.listing",
    "ksef_mcp.invoices.review",
    "ksef_mcp.invoices.statement",
    "ksef_mcp.retention",
    "ksef_mcp.storage.archive",
    "ksef_mcp.storage.audit",
    "ksef_mcp.storage.period_cache",
    "ksef_mcp.storage.sync_store",
]


@pytest.mark.parametrize("module", DOMAIN_MODULES)
def test_a_domain_module_does_not_drag_in_the_mcp_protocol(module: str) -> None:
    # Out of process for the same reason as the `ksef2` startup-cost test: the
    # suite imports `ksef_mcp.server` elsewhere, so in-process `mcp` is always
    # in `sys.modules` and the guarantee cannot be observed at all.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {module}, sys; sys.exit(1 if 'mcp' in sys.modules else 0)",
        ],
        check=False,
    )

    assert completed.returncode == 0
