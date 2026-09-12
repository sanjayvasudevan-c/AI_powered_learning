from coursec.core.diagnostics import Diagnostic
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, SyllabusNode
from coursec.emit import certificate
from coursec.passes.syllabus import SyllabusLink


def _concept(graph: Graph, name: str = "c") -> Concept:
    return graph.add(
        Concept(
            created_by_pass="understand", content_hash="h", name=name,
            concept_type=ConceptType.definition,
        )
    )


def test_certificate_reports_coverage_percentage(graph: Graph) -> None:
    concept = _concept(graph)
    node = graph.add(
        SyllabusNode(created_by_pass="understand", content_hash="h", code="1.1", title="t", order=1)
    )
    links = [SyllabusLink(concept.id, node.id, 0.9)]
    data = certificate.build_certificate_data(
        graph, [concept], [], syllabus_nodes=[node], links=links
    )
    assert data.coverage_pct == 100.0


def test_certificate_counts_diagnostics_by_code(graph: Graph) -> None:
    concept = _concept(graph)
    def diag(code: str, message: str, severity: str = "warning") -> Diagnostic:
        return Diagnostic(severity=severity, code=code, message=message, pass_name="verify")

    diagnostics = [
        diag("sentence_dropped_unsupported", "x"),
        diag("sentence_dropped_contradicted", "y"),
        diag("numeric_origin_violation", "z", severity="error"),
        diag("slot_quarantined", "w"),
        diag("item_rejected", "v"),
    ]
    data = certificate.build_certificate_data(
        graph, [concept], diagnostics, syllabus_nodes=[], links=[]
    )
    assert data.unsupported_count == 1
    assert data.contradicted_count == 1
    assert data.numeric_origin_violations == 1
    assert data.quarantine_count == 1
    assert data.item_rejected_count == 1


def test_certificate_html_contains_concept_and_diagnostic_rows(graph: Graph) -> None:
    concept = _concept(graph, "voltage")
    diagnostics = [Diagnostic(severity="error", code="x", message="bad thing", pass_name="verify")]
    data = certificate.build_certificate_data(
        graph, [concept], diagnostics, syllabus_nodes=[], links=[]
    )
    html = certificate.render_certificate_html(data)
    assert "voltage" in html
    assert "bad thing" in html
    assert 'class="error"' in html


def test_write_certificate_creates_parent_dirs(graph: Graph, tmp_path) -> None:
    concept = _concept(graph)
    data = certificate.build_certificate_data(graph, [concept], [], syllabus_nodes=[], links=[])
    path = tmp_path / "nested" / "certificate.html"
    certificate.write_certificate(path, data)
    assert path.exists()
    assert "Build Certificate" in path.read_text(encoding="utf-8")
