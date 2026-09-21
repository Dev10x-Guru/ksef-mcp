from pathlib import Path

import pytest

from ksef_mcp import cli
from ksef_mcp.setup import skill
from tests.cli.conftest import Recorder


def install_skill(recorder: Recorder, *, scope: str, tmp_path: Path) -> int:
    return cli.main(
        ["skill", "install", "--scope", scope],
        console=recorder.console,
        working_directory=tmp_path / "project",
        home=tmp_path / "home",
    )


@pytest.fixture
def skill_in_the_project(tmp_path: Path) -> Path:
    recorder = Recorder()
    install_skill(recorder, scope="project", tmp_path=tmp_path)
    return skill.skill_path(
        skill.SkillScope.PROJECT,
        home=tmp_path / "home",
        working_directory=tmp_path / "project",
    )


@pytest.mark.parametrize(("scope", "parent"), [("user", "home"), ("project", "project")])
def test_skill_install_writes_into_the_chosen_scope(
    tmp_path: Path,
    scope: str,
    parent: str,
) -> None:
    recorder = Recorder()

    code = install_skill(recorder, scope=scope, tmp_path=tmp_path)

    written = tmp_path / parent / ".claude" / "skills" / "ksef-mcp" / "SKILL.md"
    assert (code, written.is_file()) == (cli.EXIT_OK, True)


def test_skill_install_requires_an_explicit_scope() -> None:
    recorder = Recorder()

    with pytest.raises(SystemExit):
        cli.main(["skill", "install"], console=recorder.console)


def test_skill_install_leaves_an_up_to_date_skill_alone(
    skill_in_the_project: Path,
    tmp_path: Path,
) -> None:
    recorder = Recorder()

    code = install_skill(recorder, scope="project", tmp_path=tmp_path)

    assert (code, "jest aktualny" in recorder.transcript) == (cli.EXIT_OK, True)


def test_skill_install_shows_the_difference_before_overwriting(
    skill_in_the_project: Path,
    tmp_path: Path,
) -> None:
    skill_in_the_project.write_text("Własna wersja księgowej\n", encoding="utf-8")
    recorder = Recorder(answers=["t"])

    code = install_skill(recorder, scope="project", tmp_path=tmp_path)

    assert (code, "-Własna wersja księgowej" in recorder.transcript) == (cli.EXIT_OK, True)


def test_skill_install_keeps_local_edits_when_not_confirmed(
    skill_in_the_project: Path,
    tmp_path: Path,
) -> None:
    skill_in_the_project.write_text("Własna wersja księgowej\n", encoding="utf-8")
    recorder = Recorder(answers=["n"])

    code = install_skill(recorder, scope="project", tmp_path=tmp_path)

    assert (code, skill_in_the_project.read_text(encoding="utf-8")) == (
        cli.EXIT_SKILL_KEPT,
        "Własna wersja księgowej\n",
    )
