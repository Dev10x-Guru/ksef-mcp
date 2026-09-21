"""Installing the skill that tells the agent these tools exist and how to use them."""

from __future__ import annotations

from pathlib import Path

from ksef_mcp.cli.console import Console, affirmative, ask_with_default
from ksef_mcp.cli.exits import EXIT_OK, EXIT_SKILL_KEPT
from ksef_mcp.metadata import VERSION
from ksef_mcp.setup import skill
from ksef_mcp.setup.skill import SkillScope


def confirm_overwrite(console: Console) -> bool:
    return affirmative(
        ask_with_default(
            console,
            prompt="Nadpisać zainstalowany skill? (t/n)",
            default="n",
        )
    )


def run_skill_install(
    console: Console,
    *,
    scope: SkillScope,
    home: Path,
    working_directory: Path,
) -> int:
    comparison = skill.compare_skill(
        skill.skill_path(scope, home=home, working_directory=working_directory)
    )
    console.write(f"Zakres: {scope} — {comparison.path}")
    if comparison.absent:
        skill.write_skill(comparison)
        console.write(f"Skill zainstalowany dla wersji {VERSION}.")
        return EXIT_OK
    if comparison.up_to_date:
        console.write(f"Skill jest aktualny dla wersji {VERSION} — nic nie zmieniam.")
        return EXIT_OK
    console.write("Zainstalowany skill różni się od nowego:")
    for line in skill.describe_difference(comparison):
        console.write(f"  {line}")
    if not confirm_overwrite(console):
        console.write("Zostawiam zainstalowany skill bez zmian.")
        return EXIT_SKILL_KEPT
    skill.write_skill(comparison)
    console.write(f"Skill zaktualizowany do wersji {VERSION}.")
    return EXIT_OK
