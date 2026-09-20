"""Testy `bin/release.py` — strażnika nieodwracalnej publikacji.

Sprawdzane jest przede wszystkim to, co skrypt ODMAWIA zrobić. Sama zmiana
numeru wersji jest jedną komendą; wartością jest zestaw kontroli, które
biegną, zanim tag stanie się nieodwracalny, więc to one mają pokrycie.

Testy nie sięgają do sieci ani do prawdziwego repozytorium: PyPI jest
podmieniane, a `git` uruchamiany na repozytorium zakładanym w `tmp_path`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

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


BUNDLE = b"// zastepczy generator, nie artefakt Ministerstwa\n"

VENDOR_NOTE = """\
# Nota licencyjna — artefakt obcy

| | |
|---|---|
| Rozmiar | {byte_count} bajtów |
| SHA-256 | `{digest}` |
"""


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)


def lay_down_vendor_bundle(root: Path) -> None:
    """Zastępczy bundel wraz z notą, która go opisuje.

    Wydanie sprawdza teraz, czy zwendorowany generator zgadza się z notą
    (#108), więc drzewo bez obu plików nie przeszłoby pierwszej bramki i
    każdy test tutaj wywracałby się na kontroli, o którą nie pyta.
    """
    vendor = root / "src" / "ksef_mcp" / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "ksef-fe-invoice-converter.1.1.39.js").write_bytes(BUNDLE)
    (vendor / "LICENCJA-MF.md").write_text(
        VENDOR_NOTE.format(
            byte_count=len(BUNDLE),
            digest=hashlib.sha256(BUNDLE).hexdigest(),
        ),
        encoding="utf-8",
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(LOCKFILE, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    lay_down_vendor_bundle(tmp_path)
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "start")
    return tmp_path


@pytest.fixture
def reopened(repository: Path) -> Path:
    """Drzewo między wydaniami: 0.1.0 wydane, prace otwarte na 0.1.1.

    Fixture `repository` zostaje na numerze czystym, bo to on odwzorowuje
    wydanie w toku — stan, którego potrzebują testy wznawiania, taga tylko
    lokalnego i rekoncyliacji. Świeże wydanie zaczyna się natomiast wyłącznie
    z drzewa z sufiksem, więc testy, które je zaczynają, dostają to drzewo
    tutaj, zamiast polegać na numerze sprzed wprowadzenia niezmiennika.
    """
    (repository / "pyproject.toml").write_text(
        PYPROJECT.replace('version = "0.1.0"', 'version = "0.1.1.dev0"'), encoding="utf-8"
    )
    git(repository, "commit", "-am", "otwiera prace nad 0.1.1")
    return repository


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


def test_a_truncated_vendor_bundle_stops_the_release(
    reopened: Path,
    unpublished: None,
    offline: None,
) -> None:
    # Przerwany transfer z portalu MF jest zdarzeniem zaobserwowanym, nie
    # hipotezą, a opublikowanej paczki nie da się wycofać (#108).
    bundle = reopened / "src" / "ksef_mcp" / "vendor" / "ksef-fe-invoice-converter.1.1.39.js"
    bundle.write_bytes(BUNDLE[:10])
    git(reopened, "commit", "-am", "obcina bundel")

    with pytest.raises(release.ReleaseRefused, match="zapowiada"):
        release.release(root=reopened, kind="fixes", dry_run=True)


def test_releasing_off_main_stops_the_release(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    git(repository, "checkout", "-b", "gałąź-robocza")

    with pytest.raises(release.ReleaseRefused, match="wyłącznie z `main`"):
        release.release(root=repository, kind="fixes", dry_run=True)


def test_an_already_published_version_stops_the_release(
    reopened: Path,
    offline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release, "published_versions", lambda name: {"0.1.1"})

    with pytest.raises(release.ReleaseRefused, match="nie da się użyć ponownie"):
        release.release(root=reopened, kind="fixes", dry_run=True)


def test_an_empty_unreleased_section_stops_the_release(
    reopened: Path,
    unpublished: None,
    offline: None,
) -> None:
    (reopened / "CHANGELOG.md").write_text(
        "# Dziennik zmian\n\n## Bez wydania\n\n## 0.0.9 — 2026-01-01\n\n- Stare.\n",
        encoding="utf-8",
    )
    git(reopened, "commit", "-am", "pusty dziennik")

    with pytest.raises(release.ReleaseRefused, match="pusta"):
        release.release(root=reopened, kind="fixes", dry_run=True)


def test_a_missing_changelog_stops_the_release(
    reopened: Path,
    unpublished: None,
    offline: None,
) -> None:
    (reopened / "CHANGELOG.md").unlink()
    git(reopened, "commit", "-am", "bez dziennika")

    with pytest.raises(release.ReleaseRefused, match="Brak CHANGELOG"):
        release.release(root=reopened, kind="fixes", dry_run=True)


def test_a_dry_run_changes_nothing(
    reopened: Path,
    unpublished: None,
    offline: None,
) -> None:
    before = (reopened / "pyproject.toml").read_text(encoding="utf-8")

    release.release(root=reopened, kind="features", dry_run=True)

    assert (reopened / "pyproject.toml").read_text(encoding="utf-8") == before


def test_a_dry_run_names_the_next_version(
    reopened: Path,
    unpublished: None,
    offline: None,
) -> None:
    reported = release.release(root=reopened, kind="features", dry_run=True)

    assert "0.1.1.dev0 → 0.2.0" in reported and "v0.2.0" in reported


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


def test_an_unreconciled_reopening_is_settled_instead_of_refused(
    repository: Path,
    unpublished: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Czysty numer z dokończonym wydaniem to stan „wydanie poszło, podbicie
    # nie", a nie zgubiony sufiks. Domyka się sam, więc bramka odmawiająca
    # wydania bez sufiksu nie może go dotknąć — inaczej zerwana sieć po
    # utworzeniu wydania GitHub blokowałaby każde następne wydanie.
    published_tag(monkeypatch, with_release=True)

    reported = release.release(root=repository, kind="fixes", dry_run=True)

    assert "domknięcie" in reported and "0.1.1.dev0" in reported


def test_a_tree_that_lost_the_suffix_stops_the_release(
    repository: Path,
    unpublished: None,
    offline: None,
) -> None:
    # Bez taga — lokalnego czy zdalnego — czysty numer nie opisuje żadnego
    # wydania w toku, więc został po nim tylko brak sufiksu.
    with pytest.raises(release.ReleaseRefused, match="bez sufiksu"):
        release.release(root=repository, kind="fixes", dry_run=True)


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
    reopened: Path,
    offline: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(name: str) -> set[str]:
        raise release.ReleaseRefused("Nie udało się zapytać PyPI")

    monkeypatch.setattr(release, "published_versions", refuse)

    with pytest.raises(release.ReleaseRefused, match="PyPI"):
        release.release(root=reopened, kind="fixes", dry_run=True)


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
    reopened: Path,
    unpublished: None,
    offline: None,
) -> None:
    (reopened / "CHANGELOG.md").write_text(
        "# Dziennik zmian\n\nSekcję `## Bez wydania` prowadzi człowiek.\n\n## Bez wydania\n\n",
        encoding="utf-8",
    )
    git(reopened, "commit", "-am", "sam akapit objaśniający")

    with pytest.raises(release.ReleaseRefused, match="pusta"):
        release.release(root=reopened, kind="fixes", dry_run=True)


def test_the_real_changelog_splits_on_the_heading_not_the_prose() -> None:
    # Against the repository's own file, not a fixture. The substring bug this
    # guards was invisible to synthetic cases and only showed when the function
    # met the real document — whose opening prose quotes the heading verbatim.
    body = (SCRIPT.parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")

    before, unreleased, _ = release.split_unreleased(body)

    assert "prowadzi człowiek" in before and "prowadzi człowiek" not in unreleased


def test_subsections_travel_with_the_unreleased_section() -> None:
    # Wycinek kończy się dopiero na `## `, więc wszystkie `### ` podsekcje
    # jadą do wydania. Sprawdzane na dokumencie syntetycznym o kształcie
    # prawdziwego dziennika: odpytywanie żywego CHANGELOG.md wiązałoby wynik
    # ze stanem wydania — po każdej publikacji sekcja jest pusta z założenia.
    body = (
        "# Dziennik zmian\n\n"
        "Sekcję `## Bez wydania` prowadzi człowiek, a skrypt ją przenosi.\n\n"
        "## Bez wydania\n\n"
        "### Dodane\n\n- Nowa komenda.\n\n"
        "### Bezpieczeństwo\n\n- Ograniczone ponawianie żądań.\n\n"
        "## 0.0.9 — 2026-01-01\n\n- Stare.\n"
    )

    _, unreleased, after = release.split_unreleased(body)

    assert "### Dodane" in unreleased and "### Bezpieczeństwo" in unreleased
    assert "Stare." in after and "Stare." not in unreleased


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
    reopened: Path,
    unpublished: None,
    offline: None,
) -> None:
    assert release.main(["fixes", "--dry-run", "--root", str(reopened)]) == 0


def test_the_script_rejects_an_unknown_bump_kind() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "wielkie"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0


# Poniżej: arytmetyka wersji i `resolve_plan`, sprawdzane bezpośrednio —
# bez drzewa git ani PyPI. Skonsolidowane tu z dawnego `tests/test_release.py`
# (GH-128), które dublowało scenariusze wyżej na poziomie `release.release()`;
# trzy testy pokrywające się z odpowiednikami wyżej (przerwane wydanie, tag
# tylko lokalny, drzewo bez sufiksu) zostały przy przenosinach usunięte jako
# zbędne — silniejsza wersja integracyjna zostaje, słabsza jednostkowa odpada.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.2.0", (0, 2, 0, False)),
        ("0.2.1.dev0", (0, 2, 1, True)),
        ("10.0.3", (10, 0, 3, False)),
    ],
)
def test_a_version_is_read_with_its_development_marker(
    text: str, expected: tuple[int, int, int, bool]
) -> None:
    parsed = release.parse_version(text)

    assert (parsed.major, parsed.minor, parsed.patch, parsed.development) == expected


@pytest.mark.parametrize("text", ["0.2", "0.2.0.post1", "v0.2.0", "0.2.0dev0", ""])
def test_a_number_we_cannot_read_stops_the_release(text: str) -> None:
    # Guessing here would publish a number nobody chose.
    with pytest.raises(release.ReleaseRefused):
        release.parse_version(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.2.0", "0.2.0"), ("0.2.1.dev0", "0.2.1.dev0")],
)
def test_a_version_round_trips_through_its_text(text: str, expected: str) -> None:
    assert str(release.parse_version(text)) == expected


def test_the_released_number_never_carries_the_suffix() -> None:
    assert release.parse_version("0.2.1.dev0").released == "0.2.1"


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        # A `.dev0` already claims the next patch, so fixes drops the suffix
        # rather than counting higher — otherwise every release skips a number.
        ("0.2.1.dev0", "patch", "0.2.1"),
        ("0.2.1.dev0", "minor", "0.3.0"),
        ("0.2.1.dev0", "major", "1.0.0"),
        # The transitional state: a tree still on a clean number, from before
        # the suffix was introduced.
        ("0.2.0", "patch", "0.2.1"),
        ("0.2.0", "minor", "0.3.0"),
        ("0.2.0", "major", "1.0.0"),
    ],
)
def test_the_release_number_follows_from_the_tree_and_the_kind(
    current: str, kind: str, expected: str
) -> None:
    assert release.version_to_release(release.parse_version(current), kind=kind) == expected


@pytest.mark.parametrize(
    ("released", "expected"),
    [("0.2.1", "0.2.2.dev0"), ("1.0.0", "1.0.1.dev0"), ("0.3.0", "0.3.1.dev0")],
)
def test_work_reopens_on_the_next_patch(released: str, expected: str) -> None:
    assert release.next_development_version(released) == expected


@pytest.fixture
def outstanding(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, bool, bool], str | None]:
    """The reconciliation decision, without git or gh in the way."""

    def decide(version: str, *, tagged: bool, released: bool) -> str | None:
        monkeypatch.setattr(
            release, "remote_tag_commit", lambda project, *, tag: "abc123" if tagged else None
        )
        monkeypatch.setattr(release, "github_release_exists", lambda project, *, tag: released)
        project = release.Project(root=Path("/nonexistent"), name="ksef-mcp", version=version)
        return release.outstanding_development_bump(project)

    return decide


def test_a_tree_already_reopened_needs_no_reconciliation(
    outstanding: Callable[..., str | None],
) -> None:
    assert outstanding("0.2.2.dev0", tagged=True, released=True) is None


def test_a_number_that_never_reached_the_remote_is_not_reconciled(
    outstanding: Callable[..., str | None],
) -> None:
    assert outstanding("0.2.1", tagged=False, released=False) is None


def test_a_half_finished_release_is_left_to_the_resume_path(
    outstanding: Callable[..., str | None],
) -> None:
    # Tag pushed but no GitHub release: that is a release to finish, not a
    # reopening to reconcile.
    assert outstanding("0.2.1", tagged=True, released=False) is None


def test_a_finished_release_on_a_clean_number_reopens_the_next_one(
    outstanding: Callable[..., str | None],
) -> None:
    # The state that used to start a brand-new release and silently skip the
    # reopening for good — every later wheel then matched the published one.
    assert outstanding("0.2.1", tagged=True, released=True) == "0.2.2.dev0"


class PlanLike(Protocol):
    """The shape of `release.Plan`, which a path-loaded module cannot export."""

    version: str
    tag: str
    resuming: bool


@pytest.fixture
def plan_for(monkeypatch: pytest.MonkeyPatch) -> Callable[..., PlanLike]:
    """The plan a tree resolves to, without git in the way."""

    def resolve(
        version: str,
        *,
        tagged_remotely: bool,
        tagged_locally: bool,
        released: bool,
    ) -> PlanLike:
        monkeypatch.setattr(
            release,
            "remote_tag_commit",
            lambda project, *, tag: "abc123" if tagged_remotely else None,
        )
        monkeypatch.setattr(
            release,
            "local_tag_commit",
            lambda project, *, tag: "abc123" if tagged_locally else None,
        )
        monkeypatch.setattr(release, "github_release_exists", lambda project, *, tag: released)
        project = release.Project(root=Path("/nonexistent"), name="ksef-mcp", version=version)
        return release.resolve_plan(project, kind="fixes")

    return resolve


def test_the_refusal_names_both_ways_back_to_the_invariant(
    plan_for: Callable[..., PlanLike],
) -> None:
    with pytest.raises(release.ReleaseRefused) as refusal:
        plan_for("0.2.1", tagged_remotely=False, tagged_locally=False, released=False)

    assert "0.2.1.dev0" in str(refusal.value) and "0.2.2.dev0" in str(refusal.value)


def test_a_reopened_tree_plans_a_fresh_release(plan_for: Callable[..., PlanLike]) -> None:
    plan = plan_for("0.2.1.dev0", tagged_remotely=False, tagged_locally=False, released=False)

    assert (plan.version, plan.tag, plan.resuming) == ("0.2.1", "v0.2.1", False)


def test_a_finished_release_plans_the_number_after_it(plan_for: Callable[..., PlanLike]) -> None:
    # Reached only when the reconciliation in `release()` was bypassed — the
    # plan still has to read a finished release as "start the next one" rather
    # than as drift, or the refusal would swallow a legitimate state.
    plan = plan_for("0.2.1", tagged_remotely=True, tagged_locally=True, released=True)

    assert (plan.version, plan.tag, plan.resuming) == ("0.2.2", "v0.2.2", False)


def test_releasing_twice_in_a_row_never_reuses_a_number() -> None:
    # The invariant the suffix exists to protect: whatever a release produces,
    # the tree reopens above it, so the next release cannot land on it again.
    released = release.version_to_release(release.parse_version("0.2.1.dev0"), kind="patch")
    reopened = release.next_development_version(released)

    following = release.version_to_release(release.parse_version(reopened), kind="patch")

    assert following != released
