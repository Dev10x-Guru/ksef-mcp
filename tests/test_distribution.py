"""What actually ships in the distribution — checked against a built archive.

The rest of the package has tests that run code from the source tree. This
one is about something the source tree cannot show: PDF rendering (D-027)
needs two files that are not Python — a Node shim and the Ministry's
vendored generator. Until 106 their presence in the wheel depended
purely on what `.gitignore` did not list, and their absence from the
distribution broke neither the install, nor the import, nor a single test.
The failure only surfaced for the end user.

That is why this file does not check the manifest — it checks the archive.
It builds the same sequence `pypi-publish.yml` runs: `uv build` produces the
sdist, and the wheel is built from it, so a resource missing from the sdist
is missing from the wheel too. The build runs once per module, since it
costs a dozen-odd seconds, and every assertion reads the same archive.
"""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

BUILD_TIMEOUT_SECONDS = 600.0

# Paths inside the import package, without the distribution prefix — the
# wheel carries them under `ksef_mcp/`, the sdist under `src/ksef_mcp/`.
REQUIRED_RESOURCES = (
    "rendering/node/render.mjs",
    "rendering/vendor/ksef-fe-invoice-converter.1.1.39.js",
    "rendering/vendor/package.json",
    "rendering/vendor/LICENCJA-MF.md",
)

# Whether the bundle in the tree matches the licence note is guarded by
# `bin/vendor_bundle.py` (108). The question here is narrower and only
# about packaging: whether what got packed is the same file as in the tree,
# or a truncated version of it.
BUNDLE_NAME = "rendering/vendor/ksef-fe-invoice-converter.1.1.39.js"


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--out-dir", str(destination)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
        check=True,
    )
    return destination


@pytest.fixture(scope="module")
def wheel(distributions: Path) -> Iterator[zipfile.ZipFile]:
    with zipfile.ZipFile(next(iter(distributions.glob("*.whl")))) as archive:
        yield archive


@pytest.fixture(scope="module")
def wheel_entries(wheel: zipfile.ZipFile) -> frozenset[str]:
    return frozenset(wheel.namelist())


@pytest.fixture(scope="module")
def sdist_entries(distributions: Path) -> frozenset[str]:
    with tarfile.open(next(iter(distributions.glob("*.tar.gz")))) as archive:
        # The first segment is a directory named after the distribution
        # version; stripped off so the assertions do not need to know the
        # release number.
        return frozenset(name.partition("/")[2] for name in archive.getnames())


@pytest.mark.parametrize("resource", REQUIRED_RESOURCES)
def test_the_wheel_carries_every_resource_the_renderer_needs(
    resource: str, wheel_entries: frozenset[str]
) -> None:
    assert f"ksef_mcp/{resource}" in wheel_entries


@pytest.mark.parametrize("resource", REQUIRED_RESOURCES)
def test_the_sdist_carries_every_resource_the_renderer_needs(
    resource: str, sdist_entries: frozenset[str]
) -> None:
    assert f"src/ksef_mcp/{resource}" in sdist_entries


def test_the_bundle_in_the_wheel_is_not_a_truncated_transfer(wheel: zipfile.ZipFile) -> None:
    source = REPOSITORY_ROOT / "src" / "ksef_mcp" / BUNDLE_NAME

    assert wheel.getinfo(f"ksef_mcp/{BUNDLE_NAME}").file_size == source.stat().st_size
