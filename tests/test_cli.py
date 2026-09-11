from pathlib import Path

from typer.testing import CliRunner

from coursec.cli import app

runner = CliRunner()


def test_build_stub_exits_non_zero(tmp_path: Path) -> None:
    fake_pdf = tmp_path / "chapter.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n")
    result = runner.invoke(app, ["build", str(fake_pdf)])
    assert result.exit_code != 0


def test_serve_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["serve"])
    assert result.exit_code != 0


def test_quiz_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["quiz"])
    assert result.exit_code != 0
