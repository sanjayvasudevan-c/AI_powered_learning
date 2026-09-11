from coursec.core.diagnostics import Diagnostic, DiagnosticSink


def _diag(severity: str, code: str = "x") -> Diagnostic:
    return Diagnostic(severity=severity, code=code, message="msg", pass_name="test")


def test_empty_sink_has_no_errors() -> None:
    sink = DiagnosticSink()
    assert sink.has_errors() is False


def test_sink_with_only_warnings_and_info_has_no_errors() -> None:
    sink = DiagnosticSink()
    sink.add(_diag("warning"))
    sink.add(_diag("info"))
    assert sink.has_errors() is False


def test_sink_with_one_error_has_errors() -> None:
    sink = DiagnosticSink()
    sink.add(_diag("warning"))
    sink.add(_diag("error"))
    assert sink.has_errors() is True


def test_emit_constructs_and_adds() -> None:
    sink = DiagnosticSink()
    diagnostic = sink.emit(severity="error", code="e1", message="boom", pass_name="ingest")
    assert diagnostic in sink.all()
    assert sink.has_errors() is True


def test_by_severity_filters() -> None:
    sink = DiagnosticSink()
    sink.add(_diag("error", "e1"))
    sink.add(_diag("warning", "w1"))
    sink.add(_diag("error", "e2"))
    codes = {d.code for d in sink.by_severity("error")}
    assert codes == {"e1", "e2"}


def test_len_and_iter() -> None:
    sink = DiagnosticSink()
    sink.add(_diag("info"))
    sink.add(_diag("info"))
    assert len(sink) == 2
    assert len(list(sink)) == 2
