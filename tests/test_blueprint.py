from coursec.core.models import Item, ItemStats
from coursec.emit.blueprint import Blueprint, select_for_blueprint


def _item(item_id: str, bloom_level: str, item_type: str = "mcq") -> Item:
    return Item(
        id=item_id, created_by_pass="assess", content_hash=item_id, concept_id="c1",
        bloom_level=bloom_level, item_type=item_type, stem="s", key="k",
    )


def _stats(a: float) -> ItemStats:
    return ItemStats(
        created_by_pass="assess", content_hash="h", item_id="x", discrimination=a, difficulty=0.0
    )


def test_feasible_blueprint_selects_the_requested_count() -> None:
    items = [(_item("1", "remember"), _stats(1.0)), (_item("2", "remember"), _stats(0.5))]
    blueprint = Blueprint(total_marks=2, marks_per_item=1, bloom_mix={"remember": 1.0})
    result = select_for_blueprint(items, blueprint)
    assert result.feasible is True
    assert len(result.selected) == 2
    assert result.achieved_marks == 2


def test_prefers_higher_discrimination_items_first() -> None:
    items = [(_item("weak", "remember"), _stats(0.1)), (_item("strong", "remember"), _stats(0.9))]
    blueprint = Blueprint(total_marks=1, marks_per_item=1, bloom_mix={"remember": 1.0})
    result = select_for_blueprint(items, blueprint)
    assert [i.id for i in result.selected] == ["strong"]


def test_infeasible_bloom_mix_names_the_binding_constraint() -> None:
    items = [(_item("1", "remember"), None)]
    blueprint = Blueprint(total_marks=4, marks_per_item=1, bloom_mix={"remember": 1.0})
    result = select_for_blueprint(items, blueprint)
    assert result.feasible is False
    assert "remember" in result.binding_constraint
    assert "needs 4" in result.binding_constraint
    assert "only 1" in result.binding_constraint
    assert result.selected == []  # no silent near-satisfaction


def test_infeasible_item_type_mix_names_the_binding_constraint() -> None:
    items = [(_item(str(i), "remember", "mcq"), None) for i in range(4)]
    blueprint = Blueprint(
        total_marks=4, marks_per_item=1,
        bloom_mix={"remember": 1.0}, item_type_mix={"mcq": 0.5, "numeric": 0.5},
    )
    result = select_for_blueprint(items, blueprint)
    assert result.feasible is False
    assert "numeric" in result.binding_constraint


def test_feasible_with_no_items_when_total_marks_is_zero() -> None:
    blueprint = Blueprint(total_marks=0, marks_per_item=1)
    result = select_for_blueprint([], blueprint)
    assert result.feasible is True
    assert result.selected == []


def test_unit_weightage_names_the_binding_constraint_when_present() -> None:
    items = [(_item("1", "remember"), None), (_item("2", "remember"), None)]
    blueprint = Blueprint(
        total_marks=2, marks_per_item=1, bloom_mix={"remember": 1.0},
        unit_weightage={"1": 0.5, "2": 0.5},
    )
    unit_of = {"1": "1", "2": "1"}  # both items in unit "1" -> unit "2" starves
    result = select_for_blueprint(items, blueprint, unit_of=unit_of)
    assert result.feasible is False
    assert "unit='2'" in result.binding_constraint
