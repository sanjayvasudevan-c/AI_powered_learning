from pathlib import Path

from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind
from coursec.viz.graph_html import render_graph_html


def test_render_graph_html_writes_a_file_containing_concept_names(
    graph: Graph, tmp_path: Path
) -> None:
    a = graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name="Ohm's law",
            concept_type=ConceptType.formula,
        )
    )
    b = graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name="Voltage",
            concept_type=ConceptType.definition,
        )
    )
    edge = graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=b.id,
            target_id=a.id,
            kind=EdgeKind.prerequisite_of,
            confidence=0.8,
        )
    )

    output_path = tmp_path / "graph.html"
    result = render_graph_html([a, b], [edge], output_path)

    assert result == output_path
    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    # pyvis JSON-encodes labels into an embedded <script>, so an apostrophe
    # comes out '-escaped rather than literal.
    assert "Ohm\\u0027s law" in html or "Ohm's law" in html
    assert "Voltage" in html
