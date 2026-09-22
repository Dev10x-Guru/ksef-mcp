"""Wording the taxpayer acts on — above all, which path they take for their invoices."""

from __future__ import annotations

from pathlib import Path

from ksef_mcp import config, messages


def transcript(lines: tuple[str, ...]) -> str:
    return "\n".join(lines)


def test_the_archive_location_names_the_directory_holding_the_invoices() -> None:
    told = messages.describe_archive_location(
        Path("/dane/ksef-mcp/subjects/1234567890/test/invoices"),
        cloud_marker=None,
    )

    assert "/dane/ksef-mcp/subjects/1234567890/test/invoices" in transcript(told)


def test_the_archive_location_says_which_directory_the_backup_has_to_cover() -> None:
    told = messages.describe_archive_location(Path("/dane/faktury"), cloud_marker=None)

    assert "kopią zapasową" in transcript(told)


def test_a_synced_archive_path_is_flagged_as_leaving_the_house() -> None:
    told = messages.describe_archive_location(
        Path("/home/podatnik/Dropbox/dane"),
        cloud_marker="dropbox",
    )

    assert "cudzy serwer" in transcript(told)


def test_an_unsynced_archive_path_is_not_flagged() -> None:
    told = messages.describe_archive_location(Path("/dane/faktury"), cloud_marker=None)

    assert "cudzy serwer" not in transcript(told)


def test_the_working_directory_warning_is_about_statements_and_pdfs(tmp_path: Path) -> None:
    prepared = config.prepare_directory(tmp_path / "Dropbox" / "zestawienia")

    told = messages.describe_working_directory_choice(prepared, cloud_marker="dropbox")

    assert "Zestawienia i PDF-y" in transcript(told)


def test_the_working_directory_caution_describes_rather_than_alarms(tmp_path: Path) -> None:
    # GH-252: the reader may well have chosen this on purpose.
    prepared = config.prepare_directory(tmp_path / "Dropbox" / "zestawienia")

    told = messages.describe_working_directory_choice(prepared, cloud_marker="dropbox")

    assert "Uwaga" not in transcript(told)


def test_the_working_directory_without_a_sync_marker_is_only_described(tmp_path: Path) -> None:
    prepared = config.prepare_directory(tmp_path / "zestawienia")

    told = messages.describe_working_directory_choice(prepared, cloud_marker=None)

    assert told == messages.describe_prepared_directory(prepared)
