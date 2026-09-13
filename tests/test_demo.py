"""D8 demo harness tests.

Four scenarios are network-free and asserted to actually catch their
planted defect. The fifth (`_scenario_contradiction_surfaced_not_overwritten`)
needs the local embedding model (evidence.py always scores admitted chunks
against one) — the same one-time Hugging Face download this sandbox's
network policy blocks for 10 pre-existing tests (see test_understand.py,
test_syllabus.py, test_evidence.py). Rather than special-casing it away,
its test still asserts "pass", and only tolerates "error" when the detail
is actually a network-class failure — a real regression in the scenario's
own logic still fails this test.
"""

from pathlib import Path

from typer.testing import CliRunner

from coursec.cli import app
from coursec.demo import harness

FIXTURE = Path(__file__).parent / "fixtures" / "chapter.pdf"
runner = CliRunner()

_NETWORK_ERROR_TOKENS = (
    "ProxyError", "ConnectionError", "Timeout", "httpx", "httpcore", "URLError",
)


def _assert_pass_or_network_error(result: harness.ScenarioResult) -> None:
    if result.status == "error":
        assert any(token in result.detail for token in _NETWORK_ERROR_TOKENS), result.detail
    else:
        assert result.status == "pass", result.detail


def test_real_chapter_scenario_catches_unsupported_claim_and_wrong_computation() -> None:
    result = harness._scenario_real_chapter_defects_caught(FIXTURE)
    assert result.status == "pass", result.detail
    assert result.measured["unsupported_sentences_dropped"] >= 1
    assert result.measured["compute_divergences"] >= 1


def test_cycle_scenario_breaks_the_cycle() -> None:
    result = harness._scenario_cycle_broken_deterministically()
    assert result.status == "pass", result.detail
    assert result.measured["surviving_edges"] == 2


def test_empty_dossier_scenario_refuses_without_calling_the_backend() -> None:
    result = harness._scenario_empty_dossier_refuses()
    assert result.status == "pass", result.detail
    assert result.measured["lesson_blocks_written"] == 0
    assert result.measured["refusals_logged"] == 4


def test_indefensible_distractor_scenario_rejects_every_item() -> None:
    result = harness._scenario_indefensible_distractor_rejected()
    assert result.status == "pass", result.detail
    assert result.measured["items_accepted"] == 0
    assert result.measured["items_rejected"] > 0


def test_contradiction_scenario_passes_or_fails_only_on_network() -> None:
    # Routed through `_run_scenario`, the same wrapper `run_demo` uses: an
    # environment without the embedding model available raises well before
    # this scenario gets to build its own ScenarioResult.
    result = harness._run_scenario(
        "contradiction", harness._scenario_contradiction_surfaced_not_overwritten
    )
    _assert_pass_or_network_error(result)


def test_run_demo_runs_every_scenario_and_none_crash_the_run() -> None:
    report = harness.run_demo(FIXTURE)
    assert len(report.scenarios) == 5
    assert {s.status for s in report.scenarios} <= {"pass", "fail", "error"}
    for result in report.scenarios[:4]:
        assert result.status == "pass", (result.name, result.detail)
    _assert_pass_or_network_error(report.scenarios[4])


def test_render_demo_report_html_contains_every_scenario() -> None:
    report = harness.DemoReport(
        pdf="chapter.pdf",
        scenarios=[
            harness.ScenarioResult(
                name="a passing scenario", invariant="I1", expected_code="x",
                status="pass", measured={"count": 3},
            ),
            harness.ScenarioResult(
                name="a failing scenario", invariant="I2", expected_code="y",
                status="fail", measured={}, detail="did not catch it",
            ),
        ],
    )
    html = harness.render_demo_report_html(report)
    assert "a passing scenario" in html
    assert "a failing scenario" in html
    assert "count=3" in html
    assert "1/2 scenarios passed" in html


def test_write_demo_report_creates_the_file(tmp_path: Path) -> None:
    report = harness.DemoReport(pdf="chapter.pdf", scenarios=[])
    path = tmp_path / "demo_report.html"
    harness.write_demo_report(path, report)
    assert path.exists()
    assert "Demo Harness Report" in path.read_text()


def test_demo_report_all_passed_property() -> None:
    passing = harness.ScenarioResult(name="a", invariant="i", expected_code="c", status="pass")
    failing = harness.ScenarioResult(name="b", invariant="i", expected_code="c", status="fail")
    assert harness.DemoReport(pdf="x", scenarios=[passing]).all_passed is True
    assert harness.DemoReport(pdf="x", scenarios=[passing, failing]).all_passed is False


# -- CLI wiring ----------------------------------------------------------------


def test_demo_cli_on_missing_pdf_exits_non_zero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", str(tmp_path / "nope.pdf")])
    assert result.exit_code != 0


def test_demo_cli_exits_zero_and_writes_report_when_all_scenarios_pass(
    tmp_path: Path, monkeypatch
) -> None:
    fake_report = harness.DemoReport(
        pdf=str(FIXTURE),
        scenarios=[
            harness.ScenarioResult(
                name="s", invariant="I1", expected_code="c", status="pass", measured={"n": 1}
            )
        ],
    )
    monkeypatch.setattr("coursec.cli.demo_harness.run_demo", lambda pdf: fake_report)
    monkeypatch.setattr("coursec.cli.DEMO_REPORT_PATH", tmp_path / "demo_report.html")

    result = runner.invoke(app, ["demo", str(FIXTURE)])

    assert result.exit_code == 0, result.output
    assert "[PASS]" in result.output
    assert "1/1 scenarios passed" in result.output
    assert (tmp_path / "demo_report.html").exists()


def test_demo_cli_exits_non_zero_when_a_scenario_fails(tmp_path: Path, monkeypatch) -> None:
    fake_report = harness.DemoReport(
        pdf=str(FIXTURE),
        scenarios=[
            harness.ScenarioResult(
                name="s", invariant="I1", expected_code="expected_code",
                status="fail", measured={}, detail="the harness's own bug",
            )
        ],
    )
    monkeypatch.setattr("coursec.cli.demo_harness.run_demo", lambda pdf: fake_report)
    monkeypatch.setattr("coursec.cli.DEMO_REPORT_PATH", tmp_path / "demo_report.html")

    result = runner.invoke(app, ["demo", str(FIXTURE)])

    assert result.exit_code != 0
    assert "[FAIL]" in result.output
    assert "expected_code" in result.output
