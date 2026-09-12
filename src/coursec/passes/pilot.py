"""The synthetic pilot — part of `assess` (CLAUDE.md §4: writes `ItemStats`).

**This is screening, not calibration — say so in every output string and
comment** (PROMPTS.md D5). n=12 does not support a calibration claim; do not
let the wording drift.

12 simulated students, evenly spaced across a fixed, known ability range,
each seeded with a misconception profile drawn from real `Misconception`
nodes, answer every surviving item. A per-item 2PL fit — thetas held fixed
and known, which is exactly what makes this a screening approximation and
not joint calibration — flags near-zero discrimination or a degenerate
(extreme) difficulty for quarantine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Item, ItemStats

PASS_NAME = "assess"

COHORT_SIZE = 12
THETA_RANGE = (-2.0, 2.0)

# |a| below this: the item barely distinguishes stronger from weaker
# students in the screen — a screening-level red flag, not a calibrated cut.
DISCRIMINATION_QUARANTINE_THRESHOLD = 0.1
# |b| above this: the fit pushed difficulty to an extreme, the signature of
# an item everyone in the screen got right (or wrong) — degenerate, not
# informative about where the item actually sits.
DIFFICULTY_QUARANTINE_THRESHOLD = 4.0


def _logistic(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-x))


@dataclass
class SimulatedStudent:
    student_id: str
    theta: float
    misconception_ids: set[str] = field(default_factory=set)


def build_synthetic_cohort(
    misconception_ids: list[str], *, n: int = COHORT_SIZE, seed: int = 0
) -> list[SimulatedStudent]:
    """n students on a fixed, evenly-spaced ability grid — known thetas are
    what make this a *screening* fit (see module docstring), and a fixed
    grid plus a fixed seed are what make it deterministic."""
    rng = np.random.default_rng(seed)
    thetas = np.linspace(THETA_RANGE[0], THETA_RANGE[1], n)
    students = []
    for i, theta in enumerate(thetas):
        # Lower-ability students are modeled as more likely to carry a given
        # misconception — a simple, disclosed rule, not a fitted one.
        hold_prob = float(np.clip(0.5 - theta * 0.15, 0.05, 0.9))
        profile = {mid for mid in misconception_ids if rng.random() < hold_prob}
        students.append(SimulatedStudent(f"sim-{i}", float(theta), profile))
    return students


@dataclass
class SimItem:
    item_id: str
    item_type: str
    key: str
    distractors: list[tuple[str, str]]  # (text, misconception_id)


def _load_sim_item(item: Item) -> SimItem:
    content = json.loads(item.content)
    distractors = [(d["text"], d["misconception_id"]) for d in content.get("distractors", [])]
    return SimItem(item_id=item.id, item_type=item.item_type, key=item.key, distractors=distractors)


def choose_wrong_answer(
    student: SimulatedStudent, item: SimItem, *, rng: np.random.Generator
) -> str:
    """Which distractor a student who got it wrong picks: one whose linked
    misconception they hold, if any — this is the mechanism that makes a
    wrong answer diagnostic (D7) rather than just "incorrect"."""
    if item.item_type != "mcq" or not item.distractors:
        return "incorrect"
    matching = [d for d in item.distractors if d[1] in student.misconception_ids]
    chosen = matching[0] if matching else item.distractors[int(rng.integers(len(item.distractors)))]
    return chosen[0]


def simulate_response(
    student: SimulatedStudent, item: SimItem, *, rng: np.random.Generator
) -> bool:
    """True iff correct — the only thing this pilot's 2PL fit needs.
    `choose_wrong_answer` is exposed separately (D7's root-cause readout is
    what actually consumes *which* wrong answer a student picked); the
    correctness-only response matrix here is what fit_2pl operates on.
    Underlying correctness draw uses a fixed baseline difficulty/
    discrimination (0, 1) — the (a, b) this pilot exists to recover must not
    be handed to the simulator itself, or the "fit" would be circular."""
    return bool(rng.random() < float(_logistic(student.theta)))


def simulate_responses(
    students: list[SimulatedStudent], items: list[SimItem], *, seed: int = 0
) -> np.ndarray:
    """(n_students, n_items) matrix of 1.0/0.0."""
    rng = np.random.default_rng(seed)
    matrix = np.zeros((len(students), len(items)))
    for i, student in enumerate(students):
        for j, item in enumerate(items):
            matrix[i, j] = 1.0 if simulate_response(student, item, rng=rng) else 0.0
    return matrix


def fit_2pl(
    response_matrix: np.ndarray, thetas: np.ndarray, *, seed: int = 0
) -> list[tuple[float, float]]:
    """Per-item 2PL MLE, thetas fixed and known (a screening fit — see
    module docstring). Deterministic given a fixed seed: Nelder-Mead from a
    fixed starting point on fixed data introduces no further randomness."""
    n_items = response_matrix.shape[1]
    rng = np.random.default_rng(seed)
    fits: list[tuple[float, float]] = []
    for j in range(n_items):
        y = response_matrix[:, j]

        def neg_log_likelihood(params: np.ndarray, y: np.ndarray = y) -> float:
            a, b = params
            p = np.clip(_logistic(a * (thetas - b)), 1e-6, 1 - 1e-6)
            return -float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))

        init = np.array([1.0, 0.0]) + rng.normal(scale=1e-9, size=2)
        result = minimize(
            neg_log_likelihood,
            init,
            method="Nelder-Mead",
            options={"xatol": 1e-6, "fatol": 1e-6, "maxiter": 2000},
        )
        fits.append((float(result.x[0]), float(result.x[1])))
    return fits


def is_degenerate(discrimination: float, difficulty: float) -> bool:
    return (
        abs(discrimination) < DISCRIMINATION_QUARANTINE_THRESHOLD
        or abs(difficulty) > DIFFICULTY_QUARANTINE_THRESHOLD
    )


def run_pilot(
    graph: Graph, items: list[Item], sink: DiagnosticSink, *, seed: int = 0
) -> list[ItemStats]:
    if not items:
        return []

    misconception_ids = sorted(
        {
            d["misconception_id"]
            for item in items
            for d in json.loads(item.content).get("distractors", [])
        }
    )
    students = build_synthetic_cohort(misconception_ids, seed=seed)
    sim_items = [_load_sim_item(i) for i in items]
    thetas = np.array([s.theta for s in students])
    matrix = simulate_responses(students, sim_items, seed=seed)
    fits = fit_2pl(matrix, thetas, seed=seed)

    written: list[ItemStats] = []
    for item, (a, b) in zip(items, fits, strict=True):
        quarantined = is_degenerate(a, b)
        stats = graph.add(
            ItemStats(
                created_by_pass=PASS_NAME,
                content_hash=hashlib.sha256(f"{item.id}:{a}:{b}".encode()).hexdigest(),
                item_id=item.id,
                discrimination=a,
                difficulty=b,
                quarantined=quarantined,
            )
        )
        if quarantined:
            item.status = "quarantined"
            graph.session.add(item)
            sink.emit(
                severity="warning",
                code="item_quarantined_by_pilot_screening",
                message=(
                    f"item {item.id} quarantined by the pilot SCREENING "
                    f"(a={a:.2f}, b={b:.2f}, n={COHORT_SIZE}) — a screening flag, not a "
                    "calibration claim"
                ),
                pass_name=PASS_NAME,
                node_id=item.id,
            )
        written.append(stats)
    graph.session.commit()
    return written
