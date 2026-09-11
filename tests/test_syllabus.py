from pathlib import Path

import yaml

from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType
from coursec.passes import syllabus

REAL_SYLLABUS_PATH = Path(__file__).parents[1] / "data" / "syllabus.yaml"


def _concept(graph: Graph, name: str) -> Concept:
    return graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name=name,
            concept_type=ConceptType.definition,
        )
    )


def test_real_syllabus_yaml_has_20_to_40_entries() -> None:
    entries = yaml.safe_load(REAL_SYLLABUS_PATH.read_text())
    assert 20 <= len(entries) <= 40


def test_load_syllabus_reads_yaml_into_syllabus_node(graph: Graph) -> None:
    nodes = syllabus.load_syllabus(graph, REAL_SYLLABUS_PATH)
    entries = yaml.safe_load(REAL_SYLLABUS_PATH.read_text())
    assert len(nodes) == len(entries)
    assert {n.code for n in nodes} == {e["code"] for e in entries}


def test_identical_title_links_with_high_confidence(graph: Graph) -> None:
    nodes = syllabus.load_syllabus(graph, REAL_SYLLABUS_PATH)
    concept = _concept(graph, "The Nature of Astronomy")
    links = syllabus.link_concepts_to_syllabus(graph, [concept], nodes)
    assert len(links) == 1
    assert links[0].syllabus_node_id is not None
    linked_node = next(n for n in nodes if n.id == links[0].syllabus_node_id)
    assert linked_node.title == "The Nature of Astronomy"


def test_unrelated_concept_abstains(graph: Graph) -> None:
    nodes = syllabus.load_syllabus(graph, REAL_SYLLABUS_PATH)
    concept = _concept(graph, "xyzzy quux nonsense token unrelated to astronomy")
    links = syllabus.link_concepts_to_syllabus(graph, [concept], nodes)
    assert links[0].syllabus_node_id is None


def test_coverage_report_flags_exactly_the_missing_topics(graph: Graph) -> None:
    nodes = syllabus.load_syllabus(graph, REAL_SYLLABUS_PATH)
    entries = yaml.safe_load(REAL_SYLLABUS_PATH.read_text())

    missing_titles = {entries[0]["title"], entries[10]["title"], entries[-1]["title"]}
    concepts = [
        _concept(graph, entry["title"]) for entry in entries if entry["title"] not in missing_titles
    ]

    links = syllabus.link_concepts_to_syllabus(graph, concepts, nodes)
    gaps = syllabus.coverage_report(nodes, links)

    assert {n.title for n in gaps} == missing_titles


def test_no_concepts_means_no_links_and_no_false_gaps(graph: Graph) -> None:
    nodes = syllabus.load_syllabus(graph, REAL_SYLLABUS_PATH)
    links = syllabus.link_concepts_to_syllabus(graph, [], nodes)
    assert links == []
    gaps = syllabus.coverage_report(nodes, links)
    assert len(gaps) == len(nodes)
