import json
from pathlib import Path

from typer.testing import CliRunner

from coursec.cli import app

runner = CliRunner()

FIXTURE = Path(__file__).parent / "fixtures" / "chapter.pdf"


def _fake_backend(model: str, prompt: str, params: dict) -> str:
    # understand's extraction prompts ask for a JSON array; structure's
    # pairwise-judgement prompts ask for a JSON object. A scripted "nothing
    # here" response to both keeps this test fast, free, and deterministic —
    # D2's own passes are exercised in tests/test_understand.py and
    # tests/test_structure.py with more interesting scripted responses.
    if "source_index" in prompt:
        return "[]"
    return json.dumps({"prerequisite": "neither", "confidence": 0.5})


def test_build_on_missing_file_exits_non_zero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["build", str(tmp_path / "nope.pdf")])
    assert result.exit_code != 0


def test_build_on_real_fixture_runs_ingest_through_structure(
    tmp_path: Path, monkeypatch
) -> None:
    # Not monkeypatch.chdir: data/syllabus.yaml is resolved relative to the
    # project root (how `coursec build` is actually run), so only the
    # build-output paths move into tmp_path.
    monkeypatch.setattr("coursec.cli.anthropic_backend", _fake_backend)
    monkeypatch.setattr("coursec.cli.BUILD_DB_PATH", tmp_path / "coursec.db")
    monkeypatch.setattr("coursec.cli.GRAPH_HTML_PATH", tmp_path / "graph.html")

    result = runner.invoke(app, ["build", str(FIXTURE.resolve())])

    assert result.exit_code == 0, result.output
    assert "ingest:" in result.output
    assert "paragraph:" in result.output
    assert "understand:" in result.output
    assert "link rate:" in result.output
    assert "structure:" in result.output
    assert "gap histogram:" in result.output
    assert (tmp_path / "graph.html").exists()


def _fake_backend_with_one_concept(model: str, prompt: str, params: dict) -> str:
    if "source_index" in prompt:
        if "[0]" in prompt:
            return json.dumps(
                [{"name": "a concept", "type": "definition", "salience": 0.9, "source_index": 0}]
            )
        return "[]"
    return json.dumps({"prerequisite": "neither", "confidence": 0.5})


def test_build_reaches_evidence_and_fails_loudly_with_no_search_backend(
    tmp_path: Path, monkeypatch
) -> None:
    # A concept with a real (but short, hence gapped) definition gives it a
    # nonzero retrieval budget, so build should reach the evidence stage —
    # and stop there cleanly, since no search backend is configured.
    monkeypatch.setattr("coursec.cli.anthropic_backend", _fake_backend_with_one_concept)
    monkeypatch.setattr("coursec.cli.BUILD_DB_PATH", tmp_path / "coursec.db")
    monkeypatch.setattr("coursec.cli.GRAPH_HTML_PATH", tmp_path / "graph.html")

    result = runner.invoke(app, ["build", str(FIXTURE.resolve())])

    assert result.exit_code != 0
    assert "gap histogram:" in result.output
    assert "no search backend is configured" in result.output


def test_build_without_api_key_fails_loudly_not_silently(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["build", str(FIXTURE.resolve())])

    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_serve_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["serve"])
    assert result.exit_code != 0


def test_quiz_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["quiz"])
    assert result.exit_code != 0
