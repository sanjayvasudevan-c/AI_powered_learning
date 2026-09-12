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
    monkeypatch.setattr("coursec.cli.CERTIFICATE_PATH", tmp_path / "certificate.html")
    monkeypatch.setattr("coursec.cli.EMIT_WORK_DIR", tmp_path / "emit")

    result = runner.invoke(app, ["build", str(FIXTURE.resolve())])

    assert result.exit_code == 0, result.output
    assert "ingest:" in result.output
    assert "paragraph:" in result.output
    assert "understand:" in result.output
    assert "link rate:" in result.output
    assert "structure:" in result.output
    assert "gap histogram:" in result.output
    assert (tmp_path / "graph.html").exists()
    assert (tmp_path / "certificate.html").exists()


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


def _comprehensive_backend(model: str, prompt: str, params: dict) -> str:
    """Reaches all the way through emit: one concept, one lesson block per
    slot (one with a deliberately wrong worked-example computation), one
    accepted item."""
    if "source_index" in prompt:
        if "[0]" in prompt:
            return json.dumps(
                [{"name": "a concept", "type": "formula", "salience": 0.9, "source_index": 0}]
            )
        return "[]"
    if "DOSSIER:" in prompt:
        import re

        ids = (
            re.findall(r"\[(SPAN:[a-f0-9]+)\]", prompt)
            + re.findall(r"\[(EVID:[a-f0-9]+)\]", prompt)
        )[:1]
        if not ids:
            return json.dumps({"refused": True, "reason": "no grounding"})
        if "worked_example" in prompt:
            return json.dumps(
                {
                    "refused": False,
                    "sentences": [{"text": "m*a with m=2, a=3 gives 5.", "evidence_ids": ids}],
                    "computation": {
                        "formula": "m*a", "substitutions": {"m": 2.0, "a": 3.0},
                        "claimed_result": 5.0,  # wrong: should be 6.0
                    },
                }
            )
        return json.dumps(
            {"refused": False, "sentences": [{"text": "A grounded sentence.", "evidence_ids": ids}]}
        )
    if "checking whether a sentence is entailed" in prompt:
        return json.dumps({"classification": "entailed"})
    if "Rewrite the sentence" in prompt:
        return json.dumps({"text": "repaired sentence"})
    if "List" in prompt and "misconceptions" in prompt.lower():
        return json.dumps(["a plausible misconception"])
    if prompt.startswith("Write one"):
        return json.dumps({"stem": "A stem.", "key": "the key",
                            "distractors": [{"text": "wrong", "misconception_index": 0}]})
    if "no course material" in prompt:
        return json.dumps({"answer": "not sure", "confidence": 0.2})
    if "defensibly_wrong" in prompt or "defensibly WRONG" in prompt:
        return json.dumps({"defensibly_wrong": True})
    if "Solve this question" in prompt:
        return json.dumps({"answer": "the key"})
    return json.dumps({"prerequisite": "neither", "confidence": 0.5})


def test_error_diagnostic_suppresses_all_pdf_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("coursec.cli.anthropic_backend", _comprehensive_backend)
    monkeypatch.setattr("coursec.cli.BUILD_DB_PATH", tmp_path / "coursec.db")
    monkeypatch.setattr("coursec.cli.GRAPH_HTML_PATH", tmp_path / "graph.html")
    monkeypatch.setattr("coursec.cli.CERTIFICATE_PATH", tmp_path / "certificate.html")
    monkeypatch.setattr("coursec.cli.EMIT_WORK_DIR", tmp_path / "emit")
    # This test is about the error-diagnostic -> no-PDF gate, not about
    # evidence retrieval — a no-op search lets the pipeline reach compose.
    monkeypatch.setattr("coursec.passes.evidence.no_search_backend", lambda query: [])

    result = runner.invoke(app, ["build", str(FIXTURE.resolve())])

    # The wrong worked-example computation (5.0 instead of 6.0) trips
    # verify's compute_divergence — an error-severity diagnostic.
    assert result.exit_code != 0
    assert "compute_divergence" in result.output
    assert "producing no PDF at all" in result.output
    assert (tmp_path / "certificate.html").exists()  # the audit trail still gets written
    emitted_pdfs = list((tmp_path / "emit").glob("*.pdf")) if (tmp_path / "emit").exists() else []
    assert emitted_pdfs == []


def test_serve_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["serve"])
    assert result.exit_code != 0


def test_quiz_stub_exits_non_zero() -> None:
    result = runner.invoke(app, ["quiz"])
    assert result.exit_code != 0
