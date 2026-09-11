from coursec.passes import compute
from coursec.passes.compose import Computation


def test_symbolically_equivalent_expressions_agree() -> None:
    result = compute.check_equivalence("x**2 - 1", "(x-1)*(x+1)")
    assert result.success is True
    assert result.symbolic_equivalent is True
    assert result.numeric_agreement is True
    assert result.agrees is True


def test_different_expressions_disagree() -> None:
    result = compute.check_equivalence("x + 1", "x + 2")
    assert result.success is True
    assert result.symbolic_equivalent is False
    assert result.agrees is False


def test_unparseable_expression_fails_without_crashing() -> None:
    result = compute.check_equivalence("this is not math (((", "1")
    assert result.success is False
    assert result.error is not None


def test_runs_in_a_real_subprocess_not_in_process_eval(monkeypatch) -> None:
    # If this ever regressed to a bare eval()/sympify() in-process, patching
    # subprocess.run would break it; a genuine subprocess call survives.
    import subprocess

    real_run = subprocess.run
    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    result = compute.check_equivalence("1+1", "2")
    assert result.agrees is True
    assert len(calls) == 1


def test_check_worked_example_agrees_with_correct_arithmetic() -> None:
    computation = Computation(formula="m*a", substitutions={"m": 2.0, "a": 3.0}, claimed_result=6.0)
    result = compute.check_worked_example(computation)
    assert result.agrees is True


def test_check_worked_example_flags_a_wrong_constant() -> None:
    computation = Computation(formula="m*a", substitutions={"m": 2.0, "a": 3.0}, claimed_result=5.0)
    result = compute.check_worked_example(computation)
    assert result.agrees is False
