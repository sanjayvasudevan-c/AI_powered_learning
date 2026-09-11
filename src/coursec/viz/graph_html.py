"""Render the concept graph to a standalone HTML file (PROMPTS.md D2).

Colours nodes by `Concept.concept_type` for now; D4 switches this to
contract status once concepts have one. `cdn_resources="in_line"` makes the
output a single self-contained file — no network access needed to view it.
"""

from __future__ import annotations

from pathlib import Path

from pyvis.network import Network

from coursec.core.models import Concept, Edge

TYPE_COLORS: dict[str, str] = {
    "definition": "#4C72B0",
    "formula": "#DD8452",
    "procedure": "#55A868",
    "theorem": "#C44E52",
    "phenomenon": "#8172B2",
    "example": "#937860",
}
DEFAULT_COLOR = "#999999"


def render_graph_html(
    concepts: list[Concept],
    prerequisite_edges: list[Edge],
    output_path: Path,
) -> Path:
    net = Network(
        height="800px", width="100%", directed=True, notebook=False, cdn_resources="in_line"
    )
    for concept in concepts:
        net.add_node(
            concept.id,
            label=concept.name,
            color=TYPE_COLORS.get(concept.concept_type.value, DEFAULT_COLOR),
            title=f"{concept.concept_type.value} (salience={concept.salience:.2f})",
        )
    node_ids = {c.id for c in concepts}
    for edge in prerequisite_edges:
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            continue  # e.g. an edge to a non-Concept node isn't drawn here
        confidence = f"{edge.confidence:.2f}" if edge.confidence is not None else "?"
        net.add_edge(edge.source_id, edge.target_id, title=f"confidence={confidence}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(output_path), open_browser=False, notebook=False)
    return output_path
