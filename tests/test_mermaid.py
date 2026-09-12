import httpx

from coursec.emit import mermaid


def test_render_via_mermaid_ink_returns_svg_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "mermaid.ink" in str(request.url)
        return httpx.Response(200, content=b"<svg>fake</svg>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = mermaid.render_via_mermaid_ink("graph TD; A-->B;", client=client)
    assert result == b"<svg>fake</svg>"


def test_render_via_mermaid_ink_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        mermaid.render_via_mermaid_ink("graph TD; A-->B;", client=client)
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected an HTTPStatusError")
