"""Testy `bin/release.py` — strażnika nieodwracalnej publikacji.

Sprawdzane jest przede wszystkim to, co skrypt ODMAWIA zrobić. Sama zmiana
numeru wersji jest jedną komendą; wartością jest zestaw kontroli, które
biegną, zanim tag stanie się nieodwracalny, więc to one mają pokrycie.

Testy nie sięgają do sieci ani do prawdziwego repozytorium: PyPI jest
podmieniane, a `git` uruchamiany na repozytorium zakładanym w `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "release.py"


def load_module() -> object:
    specification = importlib.util.spec_from_file_location("release", SCRIPT)
    module = importlib.util.module_from_spec(specification)
    # Registered before execution: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is empty for a module loaded by path.
    sys.modules["release"] = module
    specification.loader.exec_module(module)
    return module


release = load_module()


PYPROJECT = """\
[project]
name = "ksef-mcp"
version = "0.1.0"
"""

LOCKFILE = """\
[[package]]
name = "ksef-mcp"
version = "0.1.0"
"""

CHANGELOG = """\
# Dziennik zmian

## Bez wydania

### Dodane

- Coś nowego.
"""


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(LOCKFILE, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "start")
    return tmp_path


@pytest.fixture
def unpublished(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "published_versions", lambda name: set())


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "require_synced_with_remote", lambda project: None)
    monkeypatch.setattr(release, "remote_tag_commit", lambda project, *, tag: None)
    monkeypatch.setattr(release, "github_release_exists", lambda project, *, tag: False)


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        ("0.1.0", "patch", "0.1.1"),
        ("0.1.0", "minor", "0.2.0"),
        ("0.1.0", "major", "1.0.0"),
        ("1.4.9", "minor", "1.5.0"),
        ("1.4.9", "major", "2.0.0"),
    ],
)
def test_next_version(current: str, kind: str, expected: str) -> None:
    project = release.Project(root=Path("/"), name="ksef-mcp", version=current)

    assert release.next_version(project, kind=kind) == expected


def test_the_project_is_read_from_the_manifest(repository: Path) -> None:
    project = release.read_project(repository)

    assert (project.name, project.version) == ("ksef-mcp", "0.1.0")


def test_a_dirty_tree_stops_the_release(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    (repository / "brudny.txt").write_text("x", encoding="utf-8")

    with pytest.raises(release.ReleaseRefused, match="czyste"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_releasing_off_main_stops_the_release(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    git(repository, "checkout", "-b", "gałąź-robocza")

    with pytest.raises(release.ReleaseRefused, match="wyłącznie z `main`"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_an_already_published_version_stops_the_release(
    repository: Path,
    offline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release, "published_versions", lambda name: {"0.1.1"})

    with pytest.raises(release.ReleaseRefused, match="nie da się użyć ponownie"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_an_empty_unreleased_section_stops_the_release(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    (repository / "CHANGELOG.md").write_text(
        "# Dziennik zmian\n\n## Bez wydania\n\n## 0.0.9 — 2026-01-01\n\n- Stare.\n",
        encoding="utf-8",
    )
    git(repository, "commit", "-am", "pusty dziennik")

    with pytest.raises(release.ReleaseRefused, match="pusta"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_a_missing_changelog_stops_the_release(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    (repository / "CHANGELOG.md").unlink()
    git(repository, "commit", "-am", "bez dziennika")

    with pytest.raises(release.ReleaseRefused, match="Brak CHANGELOG"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_a_dry_run_changes_nothing(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    before = (repository / "pyproject.toml").read_text(encoding="utf-8")

    release.release(root=repository, kind="features", dry_run=True)

    assert (repository / "pyproject.toml").read_text(encoding="utf-8") == before


def test_a_dry_run_names_the_next_version(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    reported = release.release(root=repository, kind="features", dry_run=True)

    assert "0.1.0 → 0.2.0" in reported and "v0.2.0" in reported


def published_tag(monkeypatch: pytest.MonkeyPatch, *, with_release: bool) -> None:
    monkeypatch.setattr(release, "require_synced_with_remote", lambda project: None)
    monkeypatch.setattr(release, "remote_tag_commit", lambda project, *, tag: "zdalny-sha")
    monkeypatch.setattr(release, "github_release_exists", lambda project, *, tag: with_release)


def test_an_interrupted_release_resumes_instead_of_starting_a_new_one(
    repository: Path,
    unpublished: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tag doszedł na zdalne repozytorium, więc workflow publikacji już się
    # wyzwolił, a wydania GitHub brak — przebieg przerwano po otagowaniu.
    published_tag(monkeypatch, with_release=False)

    reported = release.release(root=repository, kind="fixes", dry_run=True)

    assert "wznowienie" in reported and "v0.1.0" in reported


def test_a_completed_release_starts_the_next_one(
    repository: Path,
    unpublished: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_tag(monkeypatch, with_release=True)

    reported = release.release(root=repository, kind="fixes", dry_run=True)

    assert "0.1.0 → 0.1.1" in reported


def test_resuming_skips_the_pypi_novelty_check(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(name: str) -> set[str]:
        raise AssertionError("PyPI nie powinno być odpytywane przy wznawianiu")

    published_tag(monkeypatch, with_release=False)
    monkeypatch.setattr(release, "published_versions", explode)

    assert "wznowienie" in release.release(root=repository, kind="fixes", dry_run=True)


def test_a_local_only_tag_is_neither_a_resume_nor_a_fresh_release(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    # Tag, który nigdy nie wyszedł, nic nie wyzwolił. Potraktowanie go jako
    # wznowienia pomijałoby kontrolę PyPI i dziennika, a potem wypychało
    # numer bieżącej wersji — najtańsza droga do wydania przez pomyłkę.
    git(repository, "tag", "-a", "v0.1.0", "-m", "próba")

    with pytest.raises(release.ReleaseRefused, match="istnieje lokalnie"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_a_remote_tag_without_a_local_one_still_resumes(
    repository: Path,
    unpublished: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Świeży klon po awarii: tag jest na origin, lokalnie go nie ma. Bez
    # pytania o zdalny ref skrypt zacząłby nową wersję, a wydany tag
    # zostałby bez wydania GitHub na zawsze.
    published_tag(monkeypatch, with_release=False)

    assert "wznowienie" in release.release(root=repository, kind="fixes", dry_run=True)


def test_a_published_version_is_never_told_to_delete_its_tag(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The instruction a hurried person follows must not destroy history for
    # a number that cannot be reused anyway.
    monkeypatch.setattr(release, "published_versions", lambda name: {"0.1.1"})

    described = release.describe_tag_conflict(make_plan(repository), remote="stary", head="nowy")

    assert "Podnieś numer" in described and "git push --delete" not in described


def test_an_unpublished_version_may_still_be_withdrawn(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release, "published_versions", lambda name: set())

    described = release.describe_tag_conflict(make_plan(repository), remote="stary", head="nowy")

    assert "git push --delete origin v0.1.1" in described


def test_an_unusable_gh_stops_the_release_instead_of_guessing(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failure to ask must not read as "the release is absent", which would
    # turn a finished release into a resume.
    monkeypatch.setattr(release, "succeeds", lambda command, *, root: False)
    project = release.read_project(repository)

    with pytest.raises(release.ReleaseRefused, match="gh auth login"):
        release.github_release_exists(project, tag="v0.1.1")


def test_a_shipped_remote_tag_is_never_overwritten(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release, "remote_tag_commit", lambda project, *, tag: "cudzy-sha")
    project = release.read_project(repository)
    plan = release.Plan(project=project, version="0.1.1", tag="v0.1.1", resuming=False)

    with pytest.raises(release.ReleaseRefused, match="Nie nadpisuję"):
        release.ensure_tag(plan)


def test_a_local_tag_pointing_elsewhere_is_recreated(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release, "remote_tag_commit", lambda project, *, tag: None)
    git(repository, "tag", "-a", "v0.1.1", "-m", "stary")
    (repository / "nowy.txt").write_text("x", encoding="utf-8")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "ruch")
    project = release.read_project(repository)
    plan = release.Plan(project=project, version="0.1.1", tag="v0.1.1", resuming=False)

    release.ensure_tag(plan)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert release.local_tag_commit(project, tag="v0.1.1") == head


def test_an_annotated_tag_resolves_to_its_commit(repository: Path) -> None:
    git(repository, "tag", "-a", "v0.1.0", "-m", "wydanie")
    project = release.read_project(repository)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert release.local_tag_commit(project, tag="v0.1.0") == head


def test_a_missing_local_tag_reads_as_absent(repository: Path) -> None:
    project = release.read_project(repository)

    assert release.local_tag_commit(project, tag="v9.9.9") is None


def test_a_missing_pypi_answer_stops_the_release(
    repository: Path,
    offline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(name: str) -> set[str]:
        raise release.ReleaseRefused("Nie udało się zapytać PyPI")

    monkeypatch.setattr(release, "published_versions", refuse)

    with pytest.raises(release.ReleaseRefused, match="PyPI"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_an_unknown_project_on_pypi_reads_as_no_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    def missing(url: str, timeout: int) -> None:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(release.urllib.request, "urlopen", missing)

    assert release.published_versions("ksef-mcp") == set()


def test_the_heading_is_matched_as_a_line_not_a_substring(repository: Path) -> None:
    # The real CHANGELOG.md explains this very section in prose, so the
    # heading text appears inside a sentence before it appears as a heading.
    # A substring match lands mid-paragraph and releases the explanation.
    (repository / "CHANGELOG.md").write_text(
        "# Dziennik zmian\n\n"
        "Sekcję `## Bez wydania` prowadzi człowiek, a skrypt ją przenosi.\n\n"
        "## Bez wydania\n\n- Prawdziwy wpis.\n",
        encoding="utf-8",
    )

    _, unreleased, _ = release.split_unreleased(
        (repository / "CHANGELOG.md").read_text(encoding="utf-8")
    )

    assert "Prawdziwy wpis." in unreleased and "prowadzi człowiek" not in unreleased


def test_prose_mentioning_the_heading_does_not_satisfy_the_guard(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    (repository / "CHANGELOG.md").write_text(
        "# Dziennik zmian\n\nSekcję `## Bez wydania` prowadzi człowiek.\n\n## Bez wydania\n\n",
        encoding="utf-8",
    )
    git(repository, "commit", "-am", "sam akapit objaśniający")

    with pytest.raises(release.ReleaseRefused, match="pusta"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_the_real_changelog_parses_into_a_usable_section() -> None:
    # Against the repository's own file, not a fixture. The substring bug this
    # guards was invisible to synthetic cases and only showed when the
    # function met the real document — whose prose quotes the heading.
    body = (SCRIPT.parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")

    before, unreleased, _ = release.split_unreleased(body)

    assert before.rstrip().endswith(".") and unreleased.strip().startswith("###")


def test_a_changelog_without_the_heading_stops_the_release(repository: Path) -> None:
    with pytest.raises(release.ReleaseRefused, match="nie zawiera nagłówka"):
        release.split_unreleased("# Dziennik zmian\n\n## 0.0.9 — 2026-01-01\n")


def test_the_bump_config_travels_with_the_release_commit() -> None:
    # bump-my-version rewrites current_version inside its own config file.
    # Leaving it unstaged makes the release commit internally inconsistent
    # and blocks the next run at the clean-tree guard.
    source = SCRIPT.read_text(encoding="utf-8")

    assert '".bumpversion.toml"' in source


def test_the_unreleased_section_is_promoted_under_the_new_version(
    repository: Path,
) -> None:
    project = release.read_project(repository)

    release.promote_unreleased(project, version="0.2.0", today="2026-09-13")

    body = (repository / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.2.0 — 2026-09-13" in body and "Coś nowego." in body


def test_promotion_leaves_an_empty_unreleased_section_behind(
    repository: Path,
) -> None:
    project = release.read_project(repository)

    release.promote_unreleased(project, version="0.2.0", today="2026-09-13")

    body = (repository / "CHANGELOG.md").read_text(encoding="utf-8")
    _, unreleased, _ = release.split_unreleased(body)
    assert unreleased.strip() == ""


def make_plan(repository: Path, *, resuming: bool = False) -> object:
    return release.Plan(
        project=release.read_project(repository),
        version="0.1.1",
        tag="v0.1.1",
        resuming=resuming,
    )


def test_a_non_interactive_session_needs_explicit_consent(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(release.CONFIRMATION_VARIABLE, raising=False)
    monkeypatch.setattr(release.sys.stdin, "isatty", lambda: False)

    with pytest.raises(release.ReleaseRefused, match="Sesja nieinteraktywna"):
        release.require_confirmation(make_plan(repository))


def test_consent_naming_the_version_lets_a_non_interactive_session_through(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(release.CONFIRMATION_VARIABLE, "0.1.1")
    monkeypatch.setattr(release.sys.stdin, "isatty", lambda: False)

    assert release.require_confirmation(make_plan(repository)) is None


def test_a_bare_flag_no_longer_authorises_a_release(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A `=1` left in a shell profile would otherwise authorise every future
    # release, including one nobody meant to make.
    monkeypatch.setenv(release.CONFIRMATION_VARIABLE, "1")
    monkeypatch.setattr(release.sys.stdin, "isatty", lambda: True)

    with pytest.raises(release.ReleaseRefused, match="jednej konkretnej wersji"):
        release.require_confirmation(make_plan(repository))


def test_consent_for_another_version_does_not_carry_over(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(release.CONFIRMATION_VARIABLE, "0.2.0")
    monkeypatch.setattr(release.sys.stdin, "isatty", lambda: False)

    with pytest.raises(release.ReleaseRefused, match="jednej konkretnej wersji"):
        release.require_confirmation(make_plan(repository))


def test_the_consequences_name_the_irreversible_step(repository: Path) -> None:
    described = release.describe_consequences(make_plan(repository))

    assert "nie da się" in described and "PyPI" in described


def test_a_resumed_release_says_so_instead_of_warning_afresh(repository: Path) -> None:
    described = release.describe_consequences(make_plan(repository, resuming=True))

    assert "Dokańczam przerwane wydanie" in described


def test_the_command_line_reports_a_refusal_on_stderr(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    unpublished: None,
    offline: None,
) -> None:
    (repository / "brudny.txt").write_text("x", encoding="utf-8")

    code = release.main(["fixes", "--dry-run", "--root", str(repository)])

    assert (code, "Wydanie wstrzymane" in capsys.readouterr().err) == (1, True)


def test_the_command_line_succeeds_on_a_dry_run(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    assert release.main(["fixes", "--dry-run", "--root", str(repository)]) == 0


def test_the_script_rejects_an_unknown_bump_kind() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "wielkie"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
