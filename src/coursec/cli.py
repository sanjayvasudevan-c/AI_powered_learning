"""coursec CLI.

D0 ships stubs only: `build`, `serve`, `quiz` each announce that they are
unimplemented and exit non-zero. A stub must never exit 0 — a green stub is
worse than a missing command because it hides the gap silently.
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def build(pdf: Path = typer.Argument(..., help="Source chapter PDF to compile.")) -> None:
    """Compile a source PDF into the Concept Graph and emit targets."""
    typer.echo(f"coursec build: not implemented yet (pdf={pdf})", err=True)
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
