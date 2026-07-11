from pathlib import Path

from tikpoc.cli import main
from tikpoc.db import Database

from tests.test_importer import HEADER


def test_cli_imports_comment_export_into_database(tmp_path: Path, capsys) -> None:
    source = tmp_path / "comments.csv"
    database_path = tmp_path / "tasks.db"
    source.write_text(
        HEADER
        + "1,comment_panel,744,https://video,707,sec,sample,Name,https://profile,,a,0,0,false,now\n",
        encoding="utf-8",
    )

    result = main(["import", str(source), "--db", str(database_path)])

    assert result == 0
    assert "imported=1 duplicates=0" in capsys.readouterr().out
    assert Database(database_path).count_by_state() == {"pending": 1}


def test_cli_validate_reports_real_target_count(tmp_path: Path, capsys) -> None:
    source = tmp_path / "comments.csv"
    source.write_text(
        HEADER
        + "1,comment_panel,744,https://video,707,sec,sample,Name,https://profile,,a,0,0,false,now\n",
        encoding="utf-8",
    )

    result = main(["validate", str(source)])

    assert result == 0
    assert "targets=1 duplicates=0" in capsys.readouterr().out
