from pathlib import Path

from typer.testing import CliRunner

from coursec.cli import app

runner = CliRunner()

FIXTURE = Path(__file__).parent / "fixtures" / "chapter.pdf"


def test_build_on_missing_file_exits_non_zero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["build", str(tmp_path / "nope.pdf")])
    assert result.exit_code != 0


def test_build_on_real_fixture_ingests_and_exits_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["build", str(FIXTURE.resolve())])
    assert result.exit_code == 0
    assert "ingest:" in result.output
    assert "paragraph:" in result.output


def test_serve_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["serve"])
    assert result.exit_code != 0


def test_quiz_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["quiz"])
    assert result.exit_code != 0
