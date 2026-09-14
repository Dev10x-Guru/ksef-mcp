from pathlib import Path

import pytest

from ksef_mcp import skill
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.skill import SkillScope


@pytest.fixture
def rendered() -> str:
    return skill.render_skill()


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    path = tmp_path / ".claude" / "skills" / SERVER_NAME / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(skill.render_skill(), encoding="utf-8")
    return path


def test_rendered_skill_names_the_server_version(rendered: str) -> None:
    assert VERSION in rendered


@pytest.mark.parametrize(
    "teaching",
    [
        "lokalnym archiwum",
        "własny rytm",
        "Listę pokazuj, nie streszczaj po swojemu",
        "Treść faktury nie wchodzi do kontekstu",
        "Oznaczaj środowisko w każdej odpowiedzi",
    ],
)
def test_rendered_skill_teaches_the_domain_rules(rendered: str, teaching: str) -> None:
    assert teaching in rendered


def test_rendered_skill_opens_with_trigger_metadata(rendered: str) -> None:
    assert rendered.startswith("---\nname: ksef-mcp\n")


@pytest.mark.parametrize(
    ("scope", "parent"),
    [(SkillScope.USER, "home"), (SkillScope.PROJECT, "project")],
)
def test_scope_decides_the_destination(tmp_path: Path, scope: SkillScope, parent: str) -> None:
    path = skill.skill_path(
        scope,
        home=tmp_path / "home",
        working_directory=tmp_path / "project",
    )

    assert path == tmp_path / parent / ".claude" / "skills" / SERVER_NAME / "SKILL.md"


def test_absent_skill_is_reported_as_absent(tmp_path: Path) -> None:
    comparison = skill.compare_skill(tmp_path / "SKILL.md")

    assert (comparison.absent, comparison.up_to_date) == (True, False)


def test_identical_skill_is_reported_as_up_to_date(installed: Path) -> None:
    comparison = skill.compare_skill(installed)

    assert (comparison.absent, comparison.up_to_date) == (False, True)


def test_edited_skill_is_reported_as_different(installed: Path) -> None:
    installed.write_text("Własna wersja księgowej\n", encoding="utf-8")

    comparison = skill.compare_skill(installed)

    assert (comparison.absent, comparison.up_to_date) == (False, False)


def test_difference_shows_what_would_be_lost(installed: Path) -> None:
    installed.write_text("Własna wersja księgowej\n", encoding="utf-8")

    difference = skill.describe_difference(skill.compare_skill(installed))

    assert "-Własna wersja księgowej" in difference


def test_write_creates_the_whole_destination_path(tmp_path: Path) -> None:
    comparison = skill.compare_skill(
        skill.skill_path(
            SkillScope.PROJECT,
            home=tmp_path / "home",
            working_directory=tmp_path / "project",
        )
    )

    written = skill.write_skill(comparison)

    assert written.read_text(encoding="utf-8") == skill.render_skill()
