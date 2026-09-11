"""coursec CLI.

D0 shipped three stubs. D1 gave `build` its first real stage (ingest); D2
adds understand (concept extraction + syllabus anchoring) and structure
(prerequisite DAG). `build` runs the pipeline built so far and exits
non-zero only if it fails. `serve` and `quiz` remain not-implemented stubs —
each still prints that and exits 1, since a green stub is worse than a
missing command.
"""

from __future__ import annotations

from pathlib import Path

import typer

from coursec.core.anthropic_backend import anthropic_backend
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.passes import compose as compose_pass
from coursec.passes import evidence as evidence_pass
from coursec.passes import gap as gap_pass
from coursec.passes import origin_linter
from coursec.passes import structure as structure_pass
from coursec.passes import syllabus as syllabus_pass
from coursec.passes import understand as understand_pass
from coursec.passes import verify as verify_pass
from coursec.passes.ingest import block_type_histogram, ingest_pdf
from coursec.viz.graph_html import render_graph_html

app = typer.Typer(no_args_is_help=True, add_completion=False)

BUILD_DB_PATH = Path("build/coursec.db")
GRAPH_HTML_PATH = Path("build/graph.html")


@app.command()
def build(pdf: Path = typer.Argument(..., help="Source chapter PDF to compile.")) -> None:
    """Compile a source PDF into the Concept Graph and emit targets.

    Runs ingest -> understand -> structure -> gap -> evidence -> compose ->
    verify so far. Later stages layer assess/emit on top of this same call.
    """
    if not pdf.exists():
        typer.echo(f"coursec build: no such file: {pdf}", err=True)
        raise typer.Exit(code=1)

    BUILD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUILD_DB_PATH.unlink(missing_ok=True)
    sink = DiagnosticSink()

    with Graph(BUILD_DB_PATH) as graph:
        blocks = ingest_pdf(pdf, graph, sink)

        histogram = block_type_histogram(blocks)
        typer.echo(f"ingest: {len(blocks)} blocks from {pdf}")
        for block_type, count in sorted(histogram.items()):
            typer.echo(f"  {block_type}: {count}")

        try:
            concepts, section_by_concept = understand_pass.understand(
                blocks, graph, sink, backend=anthropic_backend
            )
        except RuntimeError as exc:
            typer.echo(f"understand: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        syllabus_nodes = syllabus_pass.load_syllabus(graph)
        links = syllabus_pass.link_concepts_to_syllabus(graph, concepts, syllabus_nodes)
        gaps = syllabus_pass.coverage_report(syllabus_nodes, links)
        link_rate = sum(1 for link in links if link.syllabus_node_id) / len(links) if links else 0.0
        abstain_rate = 1.0 - link_rate

        typer.echo(f"understand: {len(concepts)} concepts")
        typer.echo(f"  link rate: {link_rate:.0%}")
        typer.echo(f"  abstain rate: {abstain_rate:.0%}")
        typer.echo(f"  coverage gaps: {len(gaps)}")

        try:
            prerequisite_edges, part_of_edges = structure_pass.structure(
                graph, concepts, section_by_concept, sink, backend=anthropic_backend
            )
        except RuntimeError as exc:
            typer.echo(f"structure: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        typer.echo(f"structure: {len(prerequisite_edges)} prerequisite edges")

        render_graph_html(concepts, prerequisite_edges, GRAPH_HTML_PATH)
        typer.echo(f"wrote {GRAPH_HTML_PATH}")

        gap_vectors = gap_pass.compute_gap_vectors(graph, concepts)
        histogram = gap_pass.gap_histogram(gap_vectors)
        typer.echo("gap histogram:")
        for dimension, count in histogram.items():
            typer.echo(f"  {dimension}: {count}")

        budgets = gap_pass.allocate_budgets(gap_vectors, global_cap=40)
        try:
            evidence_pass.retrieve_evidence(
                graph, concepts, gap_vectors, budgets, sink,
                search=evidence_pass.no_search_backend,
            )
        except RuntimeError as exc:
            typer.echo(f"evidence: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        try:
            lesson_blocks = []
            for concept in concepts:
                lesson_blocks.extend(
                    compose_pass.compose_concept(graph, concept, sink, backend=anthropic_backend)
                )
        except RuntimeError as exc:
            typer.echo(f"compose: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        try:
            numeric_origin_violations = 0
            for block in lesson_blocks:
                verify_pass.verify_lesson_block(graph, block, sink, backend=anthropic_backend)
                verify_pass.run_compute_check(graph, block, sink)
                numeric_origin_violations += origin_linter.lint_lesson_block(graph, block, sink)
        except RuntimeError as exc:
            typer.echo(f"verify: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        unsupported_count = sum(
            1 for d in sink.all() if d.code == "sentence_dropped_unsupported"
        )
        contradicted_count = sum(
            1 for d in sink.all() if d.code == "sentence_dropped_contradicted"
        )
        quarantine_count = sum(1 for block in lesson_blocks if block.status == "quarantined")

        typer.echo(f"compose: {len(lesson_blocks)} lesson blocks")
        typer.echo("verify:")
        typer.echo(f"  unsupported sentences dropped: {unsupported_count}")
        typer.echo(f"  contradicted sentences dropped: {contradicted_count}")
        typer.echo(f"  numeric-origin violations: {numeric_origin_violations}")
        typer.echo(f"  quarantined slots: {quarantine_count}")

    for diagnostic in sink.all():
        typer.echo(f"  [{diagnostic.severity}] {diagnostic.code}: {diagnostic.message}", err=True)

    if sink.has_errors():
        raise typer.Exit(code=1)


@app.command()
def serve() -> None:
    """Serve the Streamlit quiz UI."""
    typer.echo("coursec serve: not implemented yet", err=True)
    raise typer.Exit(code=1)


@app.command()
def quiz() -> None:
    """Run a quiz session from the command line."""
    typer.echo("coursec quiz: not implemented yet", err=True)
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
