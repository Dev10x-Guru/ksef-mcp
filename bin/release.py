#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Wydaj nową wersję: podnieś numer, otaguj, wypchnij, utwórz wydanie GitHub.

Publikacja na PyPI jest nieodwracalna. Numeru wersji nie da się użyć
ponownie nawet po wycofaniu paczki ze sprzedaży, a tag `v*` wyzwala
`pypi-publish.yml` natychmiast po wypchnięciu. Wartość tego skryptu leży
więc nie w podnoszeniu numeru — to jedna komenda — lecz w kontrolach, które
biegną **przed** krokiem nieodwracalnym, oraz w tym, że każdy krok da się
powtórzyć.

**Każdy krok sam wykrywa, czy już się wykonał.** Wydanie jest ciągiem
operacji, z których kilka jest zdalnych, więc zerwana sieć między
wypchnięciem taga a utworzeniem wydania GitHub zostawia stan pośredni.
Skrypt, który w takiej sytuacji odmawia startu, zostawia człowieka
z ręcznym dokańczaniem publikacji — dlatego ten wznawia od miejsca,
w którym przerwano. Wzorzec przejęty z `bin/release.sh` w bl-zebra.

Z pierwowzoru w Dev10x-Claude nie przenoszą się fazy synchronizujące
`develop` z `main`: to repozytorium ma wyłącznie `main`.

**Sufiks `.dev0` jest znacznikiem buildów, nie modelem wydawniczym.**
Wydań `.devN` nie publikujemy — na PyPI trafiają wyłącznie numery czyste,
a sufiks żyje tylko w drzewie między wydaniami. Rozróżnienie jest istotne,
bo wcześniejsza wersja tego pliku odrzucała `.devN` w całości, mieszając
dwie różne rzeczy: model wydawniczy (równoległy rozwój dwóch gałęzi —
tego nadal nie robimy) i rozpoznawalność paczki zbudowanej z `main`
pomiędzy wydaniami. Bez sufiksu wheel zbudowany z `main` niesie numer
identyczny z wydanym na PyPI i nie da się ich odróżnić, co przy diagnozie
błędu zgłoszonego przez użytkownika kosztuje najwięcej.

Stąd `main` między wydaniami niesie `X.Y.(Z+1).dev0`, a wydanie zdejmuje
sufiks. Sufiks niesie przy okazji odpowiedź, o którą wcześniej trzeba było
pytać zdalnego taga: numer czysty w `pyproject.toml` znaczy „wydanie tej
wersji jest w toku", numer z sufiksem — „między wydaniami".

Uruchamiaj: `bin/release.py fixes|features|major [--dry-run]`.

Wymaga uprawnień administratora repozytorium. Commit z podniesioną wersją
idzie wprost na `main`, a ta gałąź wymaga przeglądu PR-a; `enforce_admins`
jest wyłączone, więc administratorowi push przejdzie, a każdemu innemu
opiekunowi odbije się w połowie wydania. To ograniczenie, nie usterka —
ale lepiej je przeczytać tutaj niż diagnozować przy zablokowanym pushu.

W sesji nieinteraktywnej — a więc także w każdej sesji agenta — skrypt
odmawia działania bez `CONFIRM_RELEASE=1`. Zgoda człowieka na nieodwracalną
publikację nie jest czymś, co agent może wywnioskować z kontekstu.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BUMP_KINDS: dict[str, str] = {
    "fixes": "patch",
    "features": "minor",
    "major": "major",
}

UNRELEASED_HEADING = "## Bez wydania"

PYPI_PROJECT_URL = "https://pypi.org/pypi/{name}/json"

CONFIRMATION_VARIABLE = "CONFIRM_RELEASE"

RELEASE_BRANCH = "main"

DEVELOPMENT_SUFFIX = ".dev0"

VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(\.dev\d+)?$")


class ReleaseRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Version:
    """A released number, plus whether the tree is still working towards it."""

    major: int
    minor: int
    patch: int
    development: bool

    def __str__(self) -> str:
        suffix = DEVELOPMENT_SUFFIX if self.development else ""
        return f"{self.major}.{self.minor}.{self.patch}{suffix}"

    @property
    def released(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_version(text: str) -> Version:
    matched = VERSION_PATTERN.match(text)
    if matched is None:
        raise ReleaseRefused(
            f"Nie rozumiem numeru wersji `{text}`. Oczekuję X.Y.Z albo X.Y.Z.dev0."
        )
    major, minor, patch, development = matched.groups()
    return Version(
        major=int(major),
        minor=int(minor),
        patch=int(patch),
        development=development is not None,
    )


def version_to_release(current: Version, *, kind: str) -> str:
    # A `.dev0` already claims the next patch number, so releasing fixes from
    # it means dropping the suffix rather than counting one higher — otherwise
    # every release would skip a number.
    if kind == "patch":
        if current.development:
            return current.released
        return f"{current.major}.{current.minor}.{current.patch + 1}"
    if kind == "minor":
        return f"{current.major}.{current.minor + 1}.0"
    return f"{current.major + 1}.0.0"


def next_development_version(released: str) -> str:
    major, minor, patch = (int(part) for part in released.split("."))
    return f"{major}.{minor}.{patch + 1}{DEVELOPMENT_SUFFIX}"


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    version: str


@dataclass(frozen=True)
class Plan:
    project: Project
    version: str
    tag: str
    resuming: bool


def run(command: list[str], *, root: Path) -> str:
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ReleaseRefused(f"`{' '.join(command)}` zakończyło się błędem: {detail}")
    return (completed.stdout or "").strip()


def succeeds(command: list[str], *, root: Path) -> bool:
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    return completed.returncode == 0


def read_project(root: Path) -> Project:
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return Project(
        root=root,
        name=manifest["project"]["name"],
        version=manifest["project"]["version"],
    )


def next_version(project: Project, *, kind: str) -> str:
    return version_to_release(parse_version(project.version), kind=kind)


def local_tag_commit(project: Project, *, tag: str) -> str | None:
    # Rozwijane przez `^{commit}`, żeby tag adnotowany dał commit, na który
    # wskazuje, a nie skrót samego obiektu taga.
    completed = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
        cwd=project.root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or None


def remote_tag_commit(project: Project, *, tag: str) -> str | None:
    # Tag adnotowany ma na zdalnym repozytorium DWA refy: `X` wskazuje obiekt
    # taga, a `X^{}` rozwinięty commit. To osobne wiersze, nie przekształcenie
    # jednego w drugi, więc pytamy o oba i wolimy rozwinięty.
    listing = run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        root=project.root,
    )
    peeled: str | None = None
    unpeeled: str | None = None
    for line in listing.splitlines():
        commit, _, reference = line.partition("\t")
        if reference.endswith("^{}"):
            peeled = commit
        else:
            unpeeled = commit
    return peeled or unpeeled


def github_release_exists(project: Project, *, tag: str) -> bool:
    # `gh release view` exits non-zero both when the release is absent and
    # when it could not ask — no auth, no network, rate limit. Reading the
    # second as the first would turn a finished release into a "resume", so
    # the ability to ask is established separately and a failure to ask is
    # refused rather than guessed.
    if not succeeds(["gh", "auth", "status"], root=project.root):
        raise ReleaseRefused(
            "`gh` nie jest uwierzytelnione, więc nie wiem, czy wydanie GitHub "
            "już istnieje. Bez tej odpowiedzi nie odróżnię wydania "
            "dokończonego od przerwanego w połowie. Uruchom `gh auth login`."
        )
    return succeeds(["gh", "release", "view", tag], root=project.root)


def resolve_plan(project: Project, *, kind: str) -> Plan:
    # Bez sufiksu wersji roboczej stan „wydano właśnie tę wersję" wygląda tak
    # samo jak „wydawanie jej przerwano w połowie": w obu pyproject.toml niesie
    # numer, na który wskazuje tag. Rozróżnia je dopiero kompletność — wydanie
    # GitHub powstaje jako ostatnie, więc jego brak znaczy, że nie dokończono.
    #
    # Rozstrzyga tag ZDALNY, nie lokalny. Tag lokalny nie wyzwolił niczego,
    # więc traktowanie go jako wznowienia pomijałoby kontrolę obecności na
    # PyPI i kontrolę dziennika — i wypychało numer, którego nikt nie
    # zamierzał wydać. Tag zdalny bez lokalnego też się zdarza: świeży klon
    # po awarii. Stąd trzy stany, nie dwa — a czwartym, bez żadnego taga, jest
    # drzewo, które zgubiło sufiks, i tego nie da się wydać (#78).
    current = parse_version(project.version)
    if current.development:
        # Between releases: nothing is half-finished, because a release always
        # ends by putting the suffix back (see `ensure_development_bump`).
        version = next_version(project, kind=BUMP_KINDS[kind])
        return Plan(project=project, version=version, tag=f"v{version}", resuming=False)
    in_flight = f"v{project.version}"
    published_tag = remote_tag_commit(project, tag=in_flight)
    if published_tag is not None:
        if github_release_exists(project, tag=in_flight):
            version = next_version(project, kind=BUMP_KINDS[kind])
            return Plan(project=project, version=version, tag=f"v{version}", resuming=False)
        return Plan(project=project, version=project.version, tag=in_flight, resuming=True)
    if local_tag_commit(project, tag=in_flight) is not None:
        raise ReleaseRefused(
            f"Tag {in_flight} istnieje lokalnie, ale nie na zdalnym repozytorium. "
            "Nie wiem, czy to ślad po próbie, czy przerwane tagowanie, a różnica "
            "decyduje o tym, czy wolno wydać ten numer.\n"
            f"    Usuń go, jeśli był próbą:  git tag -d {in_flight}\n"
            f"    Wypchnij, jeśli to wydanie: git push origin {in_flight}"
        )
    # Czwarty stan, bez śladu po jakimkolwiek wydaniu tego numeru: drzewo
    # zgubiło sufiks. Pas bezpieczeństwa na wypadek, gdyby zawiodły oba kroki,
    # które go pilnują — `ensure_development_bump` po wydaniu i rekoncyliacja
    # w `outstanding_development_bump` — albo gdyby ktoś zdjął go ręcznie.
    raise ReleaseRefused(
        f"`pyproject.toml` niesie `{project.version}` bez sufiksu `{DEVELOPMENT_SUFFIX}`, "
        f"a wydania {project.version} nikt nie zaczął — nie ma taga {in_flight} ani "
        "lokalnie, ani na zdalnym repozytorium. Między wydaniami numer nosi sufiks, "
        "więc bez niego wheel zbudowany z `main` jest nieodróżnialny od tego, co leży "
        "na PyPI, a ja nie wiem, czy wydanie już poszło.\n"
        f"    Jeśli {project.version} nigdy nie wyszło, otwórz prace nad tym numerem:\n"
        f"        uv run bump-my-version bump --new-version "
        f"{project.version}{DEVELOPMENT_SUFFIX}\n"
        f"    Jeśli wyszło, a tag zniknął, otwórz prace nad następnym:\n"
        f"        uv run bump-my-version bump --new-version "
        f"{next_development_version(project.version)}"
    )


def require_clean_tree(project: Project) -> None:
    if run(["git", "status", "--porcelain"], root=project.root):
        raise ReleaseRefused(
            "Drzewo robocze nie jest czyste. Wydanie zamraża stan, którego "
            "nie widać w commicie, więc najpierw zacommituj albo odłóż zmiany."
        )


def require_release_branch(project: Project) -> None:
    branch = run(["git", "symbolic-ref", "--short", "HEAD"], root=project.root)
    if branch != RELEASE_BRANCH:
        raise ReleaseRefused(
            f"Wydajemy wyłącznie z `{RELEASE_BRANCH}`, a HEAD wskazuje `{branch}`."
        )


def require_synced_with_remote(project: Project) -> None:
    run(["git", "fetch", "origin", RELEASE_BRANCH], root=project.root)
    local = run(["git", "rev-parse", "HEAD"], root=project.root)
    remote = run(["git", "rev-parse", f"origin/{RELEASE_BRANCH}"], root=project.root)
    if local != remote:
        raise ReleaseRefused(
            f"Lokalny `{RELEASE_BRANCH}` rozjechał się z `origin/{RELEASE_BRANCH}`. "
            "Wydanie z rozjazdu opublikowałoby kod, którego nie ma na zdalnym "
            "repozytorium."
        )


def published_versions(name: str) -> set[str]:
    # Every failure direction here refuses rather than proceeds. A missing
    # answer means "I do not know whether the number is free", and the one
    # answer that must never be guessed is "free".
    try:
        with urllib.request.urlopen(PYPI_PROJECT_URL.format(name=name), timeout=15) as response:
            document = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return set()
        raise ReleaseRefused(f"PyPI odpowiedziało błędem {error.code}.") from error
    except urllib.error.URLError as error:
        raise ReleaseRefused(
            f"Nie udało się zapytać PyPI o wydane wersje: {error.reason}. "
            "Bez tej odpowiedzi nie wiem, czy numer jest wolny."
        ) from error
    # Read the key outright: a `.get(..., {})` would turn a changed API schema
    # into "no releases published", and that is the answer that lets a burnt
    # number through without a sound.
    if "releases" not in document:
        raise ReleaseRefused(
            "Odpowiedź PyPI nie zawiera listy wydań — schemat API się zmienił. "
            "Nie zgaduję, że wydań nie ma."
        )
    return set(document["releases"])


def require_unpublished_version(plan: Plan) -> None:
    # Kontrola nieobecna w obu pierwowzorach, a najtańsza z wszystkich.
    # Pomijana przy wznawianiu: tam numer JEST już na PyPI i właśnie o to
    # chodzi — dokańczamy wydanie, nie zaczynamy nowego.
    if plan.resuming:
        return
    if plan.version in published_versions(plan.project.name):
        raise ReleaseRefused(
            f"Wersja {plan.version} jest już na PyPI jako `{plan.project.name}`. "
            "Numeru nie da się użyć ponownie — podnieś wyżej."
        )


def changelog_path(project: Project) -> Path:
    return project.root / "CHANGELOG.md"


def find_unreleased_heading(body: str) -> re.Match[str] | None:
    # Anchored to a whole line. A substring search matches the heading quoted
    # inside prose — CHANGELOG.md explains this very section in a sentence —
    # and then the split lands mid-paragraph, silently releasing the
    # explanation instead of the changes.
    return re.search(
        rf"^{re.escape(UNRELEASED_HEADING)}[ \t]*$",
        body,
        flags=re.MULTILINE,
    )


def split_unreleased(body: str) -> tuple[str, str, str]:
    heading = find_unreleased_heading(body)
    if heading is None:
        raise ReleaseRefused(f"CHANGELOG.md nie zawiera nagłówka `{UNRELEASED_HEADING}`.")
    after = heading.end()
    rest = body[after:]
    following = re.search(r"^## ", rest, flags=re.MULTILINE)
    end = after + (following.start() if following else len(rest))
    return body[: heading.start()], body[after:end], body[end:]


def require_described_changes(plan: Plan) -> None:
    if plan.resuming:
        return
    path = changelog_path(plan.project)
    if not path.is_file():
        raise ReleaseRefused("Brak CHANGELOG.md — nie ma czym opisać wydania.")
    _, unreleased, _ = split_unreleased(path.read_text(encoding="utf-8"))
    if not unreleased.strip():
        raise ReleaseRefused(
            "Sekcja bez wydania jest pusta. Wydanie bez opisu zmian wygląda "
            "na udokumentowane i przez to jest gorsze niż brak dziennika."
        )


def promote_unreleased(project: Project, *, version: str, today: str) -> None:
    path = changelog_path(project)
    before, unreleased, after = split_unreleased(path.read_text(encoding="utf-8"))
    path.write_text(
        f"{before}{UNRELEASED_HEADING}\n\n## {version} — {today}\n{unreleased.rstrip()}\n{after}",
        encoding="utf-8",
    )


def describe_consequences(plan: Plan) -> str:
    opening = (
        f"  Dokańczam przerwane wydanie {plan.project.name} {plan.version}:"
        if plan.resuming
        else f"  Wydanie {plan.project.name} {plan.version} pociąga skutki nieodwracalne:"
    )
    return "\n".join(
        (
            "",
            opening,
            "",
            f"  1. Tag {plan.tag} wyzwoli `pypi-publish.yml`, które opublikuje",
            f"     paczkę na PyPI jako `{plan.project.name}`. Numeru {plan.version}",
            "     nie da się użyć ponownie nawet po wycofaniu ze sprzedaży.",
            f"  2. Commit z podniesioną wersją trafi na `{RELEASE_BRANCH}`.",
            f"  3. Powstanie wydanie GitHub dla {plan.tag}.",
            "",
        )
    )


def require_confirmation(plan: Plan) -> None:
    sys.stdout.write(describe_consequences(plan))
    # The consent carries the version, never a bare flag. A `=1` left in the
    # shell profile would authorise every future release — including one the
    # person did not mean to make, since `release-major` and `release-fixes`
    # differ by a single word. Naming the number makes the consent expire on
    # its own.
    #
    # Do NOT add a stricter rule for major releases. Irreversibility is the
    # same for a patch: a burnt 0.1.1 is burnt exactly as a burnt 1.0.0, and
    # a rule that singles out `major` asserts in code that patch releases are
    # less final. It would also collide with the resume path, which recovers
    # the version from pyproject.toml and ignores `kind` entirely.
    #
    # What this gate does not cover, stated so nobody mistakes the script for
    # the whole defence: `pypi-publish.yml` fires on any `v*` tag, so a tag
    # pushed by hand publishes without passing through here at all. A required
    # reviewer on the `pypi` environment would cover that too; the maintainer
    # weighed it in 2026-09 and declined it as unnecessary for a single-author
    # project. The residual risk is an accidental or stray tag, and it is
    # accepted deliberately, not overlooked.
    exported = os.environ.get(CONFIRMATION_VARIABLE)
    if exported is not None:
        if exported != plan.version:
            raise ReleaseRefused(
                f"{CONFIRMATION_VARIABLE} niesie `{exported}`, a wydawana wersja "
                f"to {plan.version}. Zgoda dotyczy jednej konkretnej wersji."
            )
        sys.stdout.write(f"  Zgoda przez {CONFIRMATION_VARIABLE}={plan.version}.\n\n")
        return
    if not sys.stdin.isatty():
        raise ReleaseRefused(
            f"Sesja nieinteraktywna bez zgody. Ustaw "
            f"{CONFIRMATION_VARIABLE}={plan.version}, jeśli naprawdę chcesz wydać "
            "tę wersję. Zgoda człowieka na nieodwracalną publikację nie jest "
            "czymś, co da się wywnioskować z kontekstu."
        )
    if input("  Wydać? Wpisz numer wersji, żeby potwierdzić: ").strip() != plan.version:
        raise ReleaseRefused("Potwierdzenie nie zgadza się z numerem wersji.")


def ensure_version_bumped(plan: Plan, *, today: str) -> None:
    if read_project(plan.project.root).version == plan.version:
        return
    run(
        ["uv", "run", "bump-my-version", "bump", "--new-version", plan.version],
        root=plan.project.root,
    )
    promote_unreleased(plan.project, version=plan.version, today=today)
    # `.bumpversion.toml` carries `current_version` and is rewritten by the
    # bump. Leaving it out makes the release commit internally inconsistent
    # and leaves the tree dirty, which blocks the next run at the first guard.
    run(
        ["git", "add", "pyproject.toml", "uv.lock", "CHANGELOG.md", ".bumpversion.toml"],
        root=plan.project.root,
    )
    run(["git", "commit", "-m", f"🔖 Wydaje {plan.version}"], root=plan.project.root)


def describe_tag_conflict(plan: Plan, *, remote: str, head: str) -> str:
    opening = (
        f"Tag {plan.tag} na zdalnym repozytorium wskazuje {remote}, a HEAD to "
        f"{head}. Nie nadpisuję wydanego taga."
    )
    # Never advise deleting a tag whose version already reached PyPI: the
    # number cannot be reused, so the deletion destroys history and buys a
    # failed re-publish. A wrong instruction here is worse than the refusal
    # it accompanies, because it is the one a hurried person will follow.
    if plan.version in published_versions(plan.project.name):
        return (
            f"{opening}\n"
            f"    Wersja {plan.version} jest już na PyPI — kasowanie taga nic "
            "nie odzyska, bo numeru nie da się użyć ponownie.\n"
            "    Podnieś numer i wydaj następną wersję."
        )
    return (
        f"{opening} Wersja nie jest jeszcze na PyPI, więc tag da się wycofać:\n"
        f"    git push --delete origin {plan.tag}\n"
        f"    gh release delete {plan.tag}"
    )


def ensure_tag(plan: Plan) -> None:
    head = run(["git", "rev-parse", "HEAD"], root=plan.project.root)
    remote = remote_tag_commit(plan.project, tag=plan.tag)
    if remote is not None and remote != head:
        raise ReleaseRefused(describe_tag_conflict(plan, remote=remote, head=head))
    local = local_tag_commit(plan.project, tag=plan.tag)
    if local is not None and local != head:
        run(["git", "tag", "-d", plan.tag], root=plan.project.root)
        local = None
    if local is None:
        run(
            ["git", "tag", "-a", plan.tag, "-m", f"{plan.project.name} {plan.version}"],
            root=plan.project.root,
        )


def ensure_pushed(plan: Plan) -> None:
    # Gałąź przed tagiem: gdyby tag poszedł pierwszy, PyPI dostałoby paczkę
    # zbudowaną z commita, którego nie ma jeszcze na zdalnej gałęzi.
    run(["git", "push", "origin", RELEASE_BRANCH], root=plan.project.root)
    if remote_tag_commit(plan.project, tag=plan.tag) is None:
        run(["git", "push", "origin", plan.tag], root=plan.project.root)


def ensure_github_release(plan: Plan) -> None:
    if github_release_exists(plan.project, tag=plan.tag):
        return
    run(["gh", "release", "create", plan.tag, "--generate-notes"], root=plan.project.root)


def apply_development_bump(project: Project, *, development: str) -> None:
    run(
        ["uv", "run", "bump-my-version", "bump", "--new-version", development],
        root=project.root,
    )
    run(["git", "add", "pyproject.toml", "uv.lock", ".bumpversion.toml"], root=project.root)
    run(["git", "commit", "-m", f"🔖 Otwiera prace nad {development}"], root=project.root)
    run(["git", "push", "origin", RELEASE_BRANCH], root=project.root)


def ensure_development_bump(plan: Plan) -> None:
    """Put the suffix back, so the next wheel built from `main` says so.

    Runs after the release is irreversible on purpose. It changes nothing a
    user can see and must never be able to block the publication it follows.
    Idempotent by reading the tree rather than trusting the plan: a rerun
    after a failed push finds the suffix already in place and does nothing.
    """
    if parse_version(read_project(plan.project.root).version).development:
        return
    apply_development_bump(plan.project, development=next_development_version(plan.version))


def outstanding_development_bump(project: Project) -> str | None:
    """The number `main` should have reopened on, when it never did.

    Once the suffix is the invariant, a clean number in `pyproject.toml` means
    a release of exactly that number is in flight. If that release turns out
    to be *finished* — tag on the remote, GitHub release present — then the
    only step left undone is the reopening bump, which runs last and is the
    one most likely to be lost to a dropped connection.

    Without this, the next run reads the finished release as "nothing in
    flight" and starts a new one, skipping the reopening for good and leaving
    every wheel built from `main` indistinguishable from the published
    package — the exact thing the suffix exists to prevent (#73).

    Returns None when the tree already carries a suffix, when no release of
    the current number reached the remote, or when one is genuinely half-done
    — that last case is a resume, which `resolve_plan` already handles.
    """
    current = parse_version(project.version)
    if current.development:
        return None
    tag = f"v{current.released}"
    if remote_tag_commit(project, tag=tag) is None:
        return None
    if not github_release_exists(project, tag=tag):
        return None
    return next_development_version(current.released)


def release(*, root: Path, kind: str, dry_run: bool) -> str:
    project = read_project(root)
    require_clean_tree(project)
    require_release_branch(project)
    require_synced_with_remote(project)
    # Before deciding anything, settle a reopening that never landed. Doing it
    # here rather than inside `resolve_plan` keeps that function reading the
    # tree as it finds it, and means the plan below is computed from a version
    # that already holds the invariant.
    pending = outstanding_development_bump(project)
    if pending is not None:
        if dry_run:
            return f"Przebieg próbny: najpierw domknięcie {project.version} → {pending}."
        apply_development_bump(project, development=pending)
        project = read_project(root)
    plan = resolve_plan(project, kind=kind)
    require_unpublished_version(plan)
    require_described_changes(plan)
    if dry_run:
        stage = "wznowienie" if plan.resuming else f"{project.version} → {plan.version}"
        return f"Przebieg próbny: {stage}, tag {plan.tag}."
    require_confirmation(plan)
    today = datetime.now(tz=UTC).date().isoformat()
    ensure_version_bumped(plan, today=today)
    ensure_tag(plan)
    ensure_pushed(plan)
    ensure_github_release(plan)
    ensure_development_bump(plan)
    return f"Wydano {project.name} {plan.version}. Tag {plan.tag} wypchnięty."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Wydaje nową wersję i publikuje ją przez tag.",
    )
    parser.add_argument("kind", choices=sorted(BUMP_KINDS))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Przechodzi kontrole i pokazuje numer, nic nie zmieniając.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    try:
        print(release(root=arguments.root, kind=arguments.kind, dry_run=arguments.dry_run))
    except ReleaseRefused as refusal:
        print(f"Wydanie wstrzymane: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
