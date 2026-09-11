"""coursec CLI.

D0 shipped three stubs. D1 gives `build` a real first stage (ingest); it now
runs the pipeline built so far and exits non-zero only if that fails. `serve`
and `quiz` remain not-implemented stubs — each still prints that and exits 1,
since a green stub is worse than a missing command.
"""

from __future__ import annotations

from pathlib import Path

import typer

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.passes.ingest import block_type_histogram, ingest_pdf

app = typer.Typer(no_args_is_help=True, add_completion=False)

BUILD_DB_PATH = Path("build/coursec.db")


@app.command()
def build(pdf: Path = typer.Argument(..., help="Source chapter PDF to compile.")) -> None:
    """Compile a source PDF into the Concept Graph and emit targets.

    D1: runs ingest only (Block/SourceSpan) and reports what it found. Later
    stages layer understand/structure/gap/evidence/compose/verify/assess/emit
    on top of this same call.
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
