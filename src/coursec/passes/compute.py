"""The compute pass — part of `verify` (CLAUDE.md §4: verify owns
`ExecResult`).

Every formula/worked-example claim of the form `lhs = rhs` is executed as
SymPy in a **subprocess**, with a timeout, checking (a) symbolic equivalence
and (b) numerical agreement on randomized substitutions. This is CLAUDE.md
I2 made concrete: the language model phrases explanations, it never
originates a number — a formula's *correctness* is a computed fact, not a
generated one.

The subprocess isolation here is demo-grade: a timeout and a fixed, minimal
script, not OS-level sandboxing (no network access is not independently
enforced — CLAUDE.md §9 says this limitation should be documented, not
hidden, and this is that documentation).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coursec.passes.compose import Computation

SUBPROCESS_TIMEOUT_SECONDS = 5
RANDOM_SUBSTITUTION_TRIALS = 5
NUMERIC_TOLERANCE = 1e-6

_SANDBOX_SCRIPT = r"""
import json, random, sys
import sympy

def main():
    data = json.loads(sys.stdin.read())
    try:
        lhs = sympy.sympify(data["lhs"])
        rhs = sympy.sympify(data["rhs"])
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"parse error: {exc}"}))
        return

    try:
        symbolic_equivalent = bool(sympy.simplify(lhs - rhs) == 0)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"simplify error: {exc}"}))
        return

    free = sorted(lhs.free_symbols | rhs.free_symbols, key=str)
    random.seed(0)
    agreements = []
    for _ in range(data.get("trials", 5)):
        subs = {s: random.uniform(1.0, 10.0) for s in free}
        try:
            lv = complex(lhs.evalf(subs=subs))
            rv = complex(rhs.evalf(subs=subs))
            tol = data.get("tolerance", 1e-6) * max(1.0, abs(lv))
            agreements.append(abs(lv - rv) < tol)
        except Exception:
            agreements.append(False)

    numeric_agreement = all(agreements) if agreements else symbolic_equivalent
    print(json.dumps({
        "ok": True,
        "symbolic_equivalent": symbolic_equivalent,
        "numeric_agreement": numeric_agreement,
    }))

main()
"""


@dataclass
class ComputeResult:
    success: bool
    symbolic_equivalent: bool | None = None
    numeric_agreement: bool | None = None
    error: str | None = None

    @property
    def agrees(self) -> bool:
        return self.success and bool(self.symbolic_equivalent) and bool(self.numeric_agreement)


def check_equivalence(
    lhs: str,
    rhs: str,
    *,
    timeout: float = SUBPROCESS_TIMEOUT_SECONDS,
    trials: int = RANDOM_SUBSTITUTION_TRIALS,
    tolerance: float = NUMERIC_TOLERANCE,
) -> ComputeResult:
    """Run `lhs == rhs` through SymPy in a subprocess. `lhs`/`rhs` are
    untrusted (they came from a generation step) — never `eval`'d in this
    process."""
    payload = json.dumps({"lhs": lhs, "rhs": rhs, "trials": trials, "tolerance": tolerance})
    try:
        proc = subprocess.run(  # noqa: S603 — fixed script, no shell, untrusted data via stdin only
            [sys.executable, "-c", _SANDBOX_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ComputeResult(success=False, error="timeout")

    if proc.returncode != 0 or not proc.stdout.strip():
        return ComputeResult(success=False, error=proc.stderr.strip()[-500:] or "no output")

    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return ComputeResult(success=False, error="malformed subprocess output")

    if not result.get("ok"):
        return ComputeResult(success=False, error=result.get("error", "unknown error"))

    return ComputeResult(
        success=True,
        symbolic_equivalent=result["symbolic_equivalent"],
        numeric_agreement=result["numeric_agreement"],
    )


def check_worked_example(computation: Computation) -> ComputeResult:
    """Evaluate `computation.formula` with `computation.substitutions`
    plugged in, and compare against `computation.claimed_result` — the
    concrete form "numerical agreement on randomized substitutions" takes
    when the substitutions are exactly what the worked example says it used.
    Reuses `check_equivalence`'s sandboxed evaluator rather than a second
    execution path."""
    substituted = computation.formula
    for name, value in computation.substitutions.items():
        substituted = re.sub(rf"\b{re.escape(name)}\b", f"({value})", substituted)
    return check_equivalence(substituted, str(computation.claimed_result), trials=1)
