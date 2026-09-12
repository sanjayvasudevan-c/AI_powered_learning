"""The certificate — `build/certificate.html` (PROMPTS.md D6): per-concept
contract status, syllabus coverage %, unsupported-sentence count, numeric-
origin violations, item rejections by gate, quarantine count, and the full
diagnostic list. This is the artifact a human reads to decide whether to
trust a build, so it never summarizes away a bad number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Template

from coursec.core.diagnostics import Diagnostic
from coursec.core.graph import Graph
from coursec.core.models import Concept, SyllabusNode
from coursec.emit.contract import compute_all_contract_statuses
from coursec.passes.syllabus import SyllabusLink

_TEMPLATE = Template(
    """<!doctype html>
<html><head><meta charset="utf-8"><title>Build Certificate</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem; color: #111; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
td, th { border: 1px solid #ccc; padding: 4px 10px; text-align: left; }
.complete { color: #087f23; } .partial { color: #a15c00; }
.error { color: #c62828; font-weight: bold; } .warning { color: #a15c00; } .info { color: #555; }
</style></head>
<body>
<h1>Build Certificate</h1>

<h2>Summary</h2>
<ul>
<li>Syllabus coverage: {{ "%.0f"|format(coverage_pct) }}%</li>
<li>Unsupported sentences dropped: {{ unsupported_count }}</li>
<li>Contradicted sentences dropped: {{ contradicted_count }}</li>
<li>Numeric-origin violations: {{ numeric_origin_violations }}</li>
<li>Quarantined lesson slots: {{ quarantine_count }}</li>
<li>Item rejections: {{ item_rejected_count }}</li>
</ul>

<h2>Concept contract status</h2>
<table>
<tr><th>Concept</th><th>Status</th><th>Unmet slots</th></tr>
{% for row in concept_rows -%}
{% set label = 'complete' if row.complete else 'partial' -%}
<tr><td>{{ row.name }}</td>
<td class="{{ label }}">{{ label }}</td>
<td>{{ row.unmet_slots | join(', ') }}</td></tr>
{% endfor -%}
</table>

<h2>Diagnostics ({{ diagnostics | length }})</h2>
<table>
<tr><th>Severity</th><th>Code</th><th>Message</th></tr>
{% for d in diagnostics -%}
<tr><td class="{{ d.severity }}">{{ d.severity }}</td>
<td>{{ d.code }}</td><td>{{ d.message }}</td></tr>
{% endfor -%}
</table>
</body></html>
"""
)


@dataclass
class ConceptRow:
    name: str
    complete: bool
    unmet_slots: list[str]


@dataclass
class CertificateData:
    coverage_pct: float
    unsupported_count: int
    contradicted_count: int
    numeric_origin_violations: int
    quarantine_count: int
    item_rejected_count: int
    concept_rows: list[ConceptRow] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


def build_certificate_data(
    graph: Graph,
    concepts: list[Concept],
    diagnostics: list[Diagnostic],
    *,
    syllabus_nodes: list[SyllabusNode],
    links: list[SyllabusLink],
) -> CertificateData:
    statuses = compute_all_contract_statuses(graph, concepts)
    linked = sum(1 for link in links if link.syllabus_node_id)
    coverage_pct = (linked / len(syllabus_nodes) * 100) if syllabus_nodes else 0.0

    def count(code: str) -> int:
        return sum(1 for d in diagnostics if d.code == code)

    concept_rows = [
        ConceptRow(
            name=c.name,
            complete=statuses[c.id].complete,
            unmet_slots=statuses[c.id].unmet_slots,
        )
        for c in sorted(concepts, key=lambda c: c.name)
    ]
    return CertificateData(
        coverage_pct=coverage_pct,
        unsupported_count=count("sentence_dropped_unsupported"),
        contradicted_count=count("sentence_dropped_contradicted"),
        numeric_origin_violations=count("numeric_origin_violation"),
        quarantine_count=count("slot_quarantined"),
        item_rejected_count=count("item_rejected"),
        concept_rows=concept_rows,
        diagnostics=diagnostics,
    )


def render_certificate_html(data: CertificateData) -> str:
    return _TEMPLATE.render(
        coverage_pct=data.coverage_pct,
        unsupported_count=data.unsupported_count,
        contradicted_count=data.contradicted_count,
        numeric_origin_violations=data.numeric_origin_violations,
        quarantine_count=data.quarantine_count,
        item_rejected_count=data.item_rejected_count,
        concept_rows=data.concept_rows,
        diagnostics=data.diagnostics,
    )


def write_certificate(path: Path, data: CertificateData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_certificate_html(data), encoding="utf-8")
