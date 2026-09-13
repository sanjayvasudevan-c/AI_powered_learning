"""The `learn` pass — CLAUDE.md §4: owns `Mastery` and the
`remediates`/`mastery_of` edge kinds; reads `Item` and each response as it
happens. A response is not its own graph node — same footing as `gap`'s
transient `GapVector`/`RetrievalBudget`: the ownership table names it
lower-case ("responses") on purpose.

Two things D7 needs beyond "was the answer right":

- **Bayesian Knowledge Tracing** — the two-state HMM update this file
  implements as one closed-form function (`bkt_update`), so "how likely is
  this student to actually know concept X" is a real posterior, not a raw
  percent-correct.
- **Root-cause propagation** — a wrong answer on concept C is walked
  backward along `prerequisite_of` edges to the *deepest* prerequisite
  whose own mastery is still weak (README: "root-cause propagation to the
  deepest weak prerequisite") — remediating C itself is wasted effort if
  the real gap is further upstream.

Every mastery update is a new `Mastery` row, never an overwrite — the same
append-only convention `Verdict` uses for D4's critic: the *history* of a
student's posterior is data, current mastery is just its latest row.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass

from sqlmodel import select

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import EdgeKind, Item, Mastery

PASS_NAME = "learn"

# A concept below this posterior counts as "weak" for root-cause purposes —
# named rather than eyeballed, same discipline pilot.py applies to its own
# quarantine cutoffs.
WEAK_THRESHOLD = 0.6


@dataclass(frozen=True)
class BKTParams:
    """Standard 2-state BKT parameters.

    Illustrative defaults, not fit to any real cohort — the same
    "screening, not calibration" honesty pilot.py insists on for its own
    numbers applies here: nothing in this file claims these four constants
    are correct for any real course, only that the update rule consuming
    them (`bkt_update`) is the textbook one.
    """

    p_init: float = 0.3  # prior P(already knows it), before any evidence
    p_transit: float = 0.3  # P(learns it) per opportunity, if not yet known
    p_slip: float = 0.1  # P(answers wrong despite knowing)
    p_guess: float = 0.25  # P(answers right despite not knowing)

    def __post_init__(self) -> None:
        for name in ("p_init", "p_transit", "p_slip", "p_guess"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name}={value!r} must be in [0, 1]")


DEFAULT_PARAMS = BKTParams()


def bkt_update(prior: float, correct: bool, params: BKTParams = DEFAULT_PARAMS) -> float:
    """One step of the standard BKT posterior update: Bayes' rule against
    the observed response, then the learning-opportunity transition.

    Pure and total — no I/O, no graph — so a property test can hold it to
    the textbook formula directly (tests/test_learn.py). With
    `p_slip + p_guess <= 1` (true of `DEFAULT_PARAMS`), a correct answer
    never leaves the posterior lower than an incorrect one would from the
    same prior — `record_response` checks this holds in practice, not just
    in theory.
    """
    if not 0.0 <= prior <= 1.0:
        raise ValueError(f"prior={prior!r} must be in [0, 1]")
    if correct:
        numerator = prior * (1.0 - params.p_slip)
        denominator = numerator + (1.0 - prior) * params.p_guess
    else:
        numerator = prior * params.p_slip
        denominator = numerator + (1.0 - prior) * (1.0 - params.p_guess)
    posterior_given_evidence = numerator / denominator if denominator > 0 else prior
    return posterior_given_evidence + (1.0 - posterior_given_evidence) * params.p_transit


def latest_mastery(
    graph: Graph, concept_id: str, student_id: str, *, params: BKTParams = DEFAULT_PARAMS
) -> float:
    """This student's current posterior for `concept_id` — the most recent
    `Mastery` row, or `params.p_init` if the pair has never been observed.
    Silence is not evidence of mastery."""
    rows = graph.session.exec(
        select(Mastery)
        .where(Mastery.concept_id == concept_id, Mastery.student_id == student_id)
        .order_by(Mastery.created_at.desc())
    ).all()
    return rows[0].probability if rows else params.p_init


def record_response(
    graph: Graph,
    sink: DiagnosticSink,
    *,
    student_id: str,
    concept_id: str,
    correct: bool,
    params: BKTParams = DEFAULT_PARAMS,
) -> Mastery:
    """Update `student_id`'s mastery of `concept_id` from one observed
    response, appending a new `Mastery` row (never overwriting the last)."""
    prior = latest_mastery(graph, concept_id, student_id, params=params)
    posterior = bkt_update(prior, correct, params)
    content_hash = hashlib.sha256(
        f"{student_id}:{concept_id}:{correct}:{prior}:{posterior}".encode()
    ).hexdigest()
    mastery = graph.add(
        Mastery(
            created_by_pass=PASS_NAME,
            content_hash=content_hash,
            concept_id=concept_id,
            student_id=student_id,
            probability=posterior,
        )
    )
    if correct and posterior < prior:
        # Can't happen given bkt_update's monotonicity guarantee under
        # DEFAULT_PARAMS (see tests/test_learn.py) — belt-and-braces, the
        # same posture verify.py takes toward trusting its own critic.
        sink.emit(
            severity="warning",
            code="mastery_decreased_on_correct_answer",
            message=(
                f"concept {concept_id}: correct answer decreased mastery "
                f"({prior:.3f} -> {posterior:.3f}) — check BKTParams"
            ),
            pass_name=PASS_NAME,
            node_id=concept_id,
        )
    return mastery


@dataclass(frozen=True)
class RootCause:
    concept_id: str
    depth: int  # hops upstream from the missed concept; 0 = the concept itself


def diagnose_root_cause(
    graph: Graph,
    concept_id: str,
    student_id: str,
    *,
    threshold: float = WEAK_THRESHOLD,
    params: BKTParams = DEFAULT_PARAMS,
) -> RootCause:
    """Walk `prerequisite_of` edges upstream from a missed concept, one hop
    at a time, following the weakest direct prerequisite as long as one is
    still below `threshold` — stopping at the deepest concept in an
    unbroken chain of weakness.

    A solid prerequisite breaks the chain: its own weak ancestors, if any,
    aren't blamed for a failure the solid concept between them and C would
    already have caught. Ties among equally-weak candidate prerequisites
    are broken on `content_hash` — deterministic, not the concept graph's
    random `id` (same convention as structure.py's cycle-breaking).
    """
    current = concept_id
    visited = {current}
    depth = 0
    while True:
        candidates = [
            p
            for p in graph.neighbors(current, kind=EdgeKind.prerequisite_of, direction="in")
            if p.id not in visited
        ]
        weak = [
            (p, latest_mastery(graph, p.id, student_id, params=params))
            for p in candidates
        ]
        weak = [(p, m) for p, m in weak if m < threshold]
        if not weak:
            return RootCause(concept_id=current, depth=depth)
        weak.sort(key=lambda pair: (pair[1], pair[0].content_hash))
        current = weak[0][0].id
        visited.add(current)
        depth += 1


def mcq_options(item: Item) -> list[tuple[str, bool]]:
    """`(text, is_correct)` pairs for an mcq `Item`, in a fixed but
    non-obvious order — seeded by the item's own id, so the same item
    always presents the same order (reproducible sessions) without always
    putting the key first, which would make the quiz gameable."""
    content = json.loads(item.content)
    options = [(item.key, True)] + [
        (d["text"], False) for d in content.get("distractors", [])
    ]
    random.Random(item.id).shuffle(options)
    return options


def select_next_item(
    graph: Graph,
    student_id: str,
    *,
    params: BKTParams = DEFAULT_PARAMS,
    asked_item_ids: frozenset[str] = frozenset(),
) -> Item | None:
    """The next item to ask: an accepted item on the concept this student's
    mastery is currently weakest on, excluding items already asked this
    session. Ties broken deterministically on `Item.content_hash`."""
    candidates = [
        item
        for item in graph.session.exec(select(Item).where(Item.status == "accepted")).all()
        if item.id not in asked_item_ids
    ]
    if not candidates:
        return None

    def key(item: Item) -> tuple[float, str]:
        mastery = latest_mastery(graph, item.concept_id, student_id, params=params)
        return (mastery, item.content_hash)

    return min(candidates, key=key)
