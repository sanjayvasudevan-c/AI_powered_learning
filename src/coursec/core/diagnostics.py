"""Structured diagnostics.

CLAUDE.md §10: "Diagnostics are structured objects with severity, not log
strings." Every pass reports through a `DiagnosticSink` rather than raising,
printing, or logging directly, so that a build's full diagnostic list can be
collected, counted, and — per §2 — treated as a build failure when any
error-severity diagnostic exists.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Severity = Literal["error", "warning", "info"]


class Diagnostic(BaseModel):
    """One structured finding emitted by a pass.

    `node_id` is optional: a diagnostic can predate the existence of any IR
    node (e.g. a parse failure before a `Block` is created).
    """

    severity: Severity
    code: str
    message: str
    node_id: str | None = None
    pass_name: str


class DiagnosticSink:
    """Collects `Diagnostic`s emitted during a build.

    Passes append to a shared sink rather than raising on recoverable
    problems, so the certificate (D6) can report the full list rather than
    only the first failure.
    """

    def __init__(self) -> None:
        self._diagnostics: list[Diagnostic] = []

    def add(self, diagnostic: Diagnostic) -> None:
        self._diagnostics.append(diagnostic)

    def emit(
        self,
        *,
        severity: Severity,
        code: str,
        message: str,
        pass_name: str,
        node_id: str | None = None,
    ) -> Diagnostic:
        """Construct and add a `Diagnostic` in one call."""
        diagnostic = Diagnostic(
            severity=severity,
            code=code,
            message=message,
            node_id=node_id,
            pass_name=pass_name,
        )
        self.add(diagnostic)
        return diagnostic

    def has_errors(self) -> bool:
        """True iff at least one collected diagnostic is error-severity.

        Per CLAUDE.md §2: a violation is a build failure, never a warning.
        """
        return any(d.severity == "error" for d in self._diagnostics)

    def all(self) -> list[Diagnostic]:
        return list(self._diagnostics)

    def by_severity(self, severity: Severity) -> list[Diagnostic]:
        return [d for d in self._diagnostics if d.severity == severity]

    def __len__(self) -> int:
        return len(self._diagnostics)

    def __iter__(self):
        return iter(self._diagnostics)
