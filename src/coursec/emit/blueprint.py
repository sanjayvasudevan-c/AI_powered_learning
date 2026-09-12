"""Question-paper selection against a blueprint (PROMPTS.md D6): greedy,
and honest when it can't be satisfied. **Silent near-satisfaction is
forbidden** — an infeasible blueprint names the exact binding constraint,
never quietly returns "close enough".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from coursec.core.models import Item, ItemStats


@dataclass
class Blueprint:
    total_marks: int
    marks_per_item: int = 1
    bloom_mix: dict[str, float] = field(default_factory=dict)  # bloom_level -> fraction, sums to 1
    item_type_mix: dict[str, float] = field(default_factory=dict)  # item_type -> fraction
    unit_weightage: dict[str, float] = field(default_factory=dict)  # syllabus unit -> fraction


@dataclass
class SelectionResult:
    selected: list[Item]
    achieved_marks: int
    feasible: bool
    binding_constraint: str | None = None


def _target_counts(mix: dict[str, float], total: int) -> dict[str, int]:
    return {key: round(fraction * total) for key, fraction in mix.items()}


def select_for_blueprint(
    items_with_stats: list[tuple[Item, ItemStats | None]],
    blueprint: Blueprint,
    *,
    unit_of: dict[str, str] | None = None,
) -> SelectionResult:
    """Greedy selection: within each Bloom-level bucket, prefer the
    best-discriminating available items (ties broken by item id, for
    determinism). Checks Bloom mix, then item-type mix among what was
    selected, then unit weightage if `unit_of` (item_id -> syllabus unit)
    is given — the first constraint that can't be met is reported by name.
    """
    total_items_needed = (
        round(blueprint.total_marks / blueprint.marks_per_item) if blueprint.marks_per_item else 0
    )

    by_bloom: dict[str, list[tuple[Item, ItemStats | None]]] = {}
    for item, stats in items_with_stats:
        by_bloom.setdefault(item.bloom_level, []).append((item, stats))
    for pool in by_bloom.values():
        pool.sort(key=lambda pair: (-abs(pair[1].discrimination) if pair[1] else 0.0, pair[0].id))

    bloom_targets = _target_counts(blueprint.bloom_mix, total_items_needed)
    selected: list[Item] = []
    for level, needed in bloom_targets.items():
        pool = by_bloom.get(level, [])
        if len(pool) < needed:
            return SelectionResult(
                selected=[],
                achieved_marks=0,
                feasible=False,
                binding_constraint=(
                    f"bloom_level={level!r}: blueprint needs {needed} items, "
                    f"only {len(pool)} available"
                ),
            )
        selected.extend(item for item, _stats in pool[:needed])

    if blueprint.item_type_mix:
        type_targets = _target_counts(blueprint.item_type_mix, len(selected))
        by_type: dict[str, int] = {}
        for item in selected:
            by_type[item.item_type] = by_type.get(item.item_type, 0) + 1
        for item_type, needed in type_targets.items():
            have = by_type.get(item_type, 0)
            if have < needed:
                return SelectionResult(
                    selected=[],
                    achieved_marks=0,
                    feasible=False,
                    binding_constraint=(
                        f"item_type={item_type!r}: blueprint needs {needed} among the "
                        f"selected items, only {have} present"
                    ),
                )

    if blueprint.unit_weightage and unit_of:
        unit_targets = _target_counts(blueprint.unit_weightage, len(selected))
        by_unit: dict[str, int] = {}
        for item in selected:
            unit = unit_of.get(item.id, "")
            by_unit[unit] = by_unit.get(unit, 0) + 1
        for unit, needed in unit_targets.items():
            have = by_unit.get(unit, 0)
            if have < needed:
                return SelectionResult(
                    selected=[],
                    achieved_marks=0,
                    feasible=False,
                    binding_constraint=(
                        f"unit={unit!r}: blueprint needs {needed} among the selected "
                        f"items, only {have} present"
                    ),
                )

    return SelectionResult(
        selected=selected,
        achieved_marks=len(selected) * blueprint.marks_per_item,
        feasible=True,
    )
