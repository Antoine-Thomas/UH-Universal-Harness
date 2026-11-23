"""Tests for the web_search toolset. No real network: httpx.get is monkeypatched."""

from __future__ import annotations

import httpx
import pytest

from uh.toolsets import WebSearchToolset, build_toolsets
from uh.toolsets.web_search import parse_results

SAMPLE_HTML = """
<html><body>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=abc">Example Page</a>
  <a class="result__snippet">An example snippet &amp; more.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://direct.example.org/">Direct Link</a>
  <a class="result__snippet">Second snippet.</a>
</div>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str = SAMPLE_HTML, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://html.duckduckgo.com/html/")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


def fake_get(*args, **kwargs) -> FakeResponse:
    return FakeResponse()


def test_parse_results_decodes_redirects() -> None:
    results = parse_results(SAMPLE_HTML)
    assert len(results) == 2
    assert results[0]["title"] == "Example Page"
    assert results[0]["url"] == "https://example.com/page"  # uddg unwrapped
    assert results[0]["snippet"] == "An example snippet & more."
    assert results[1]["url"] == "https://direct.example.org/"  # absolute passthrough


def test_parse_results_drops_non_http() -> None:
    html = '<a class="result__a" href="javascript:alert(1)">x</a>'
    assert parse_results(html) == []


def test_call_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", fake_get)
    out = WebSearchToolset().call("web_search", {"query": "test"})
    assert out["count"] == 2
    assert out["results"][0]["title"] == "Example Page"
    assert out["query"] == "test"


def test_call_caps_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", fake_get)
    out = WebSearchToolset().call("web_search", {"query": "test", "max_results": 1})
    assert len(out["results"]) == 1 and out["count"] == 1
    out = WebSearchToolset().call("web_search", {"query": "test", "max_results": 99})
    assert len(out["results"]) == 2  # capped at MAX_RESULTS=5, only 2 parsed


def test_call_rejects_bad_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", fake_get)
    toolset = WebSearchToolset()
    assert "error" in toolset.call("web_search", {"query": "  "})
    assert "error" in toolset.call("web_search", {})
    assert "error" in toolset.call("web_search", {"query": "x", "max_results": "abc"})
    assert "unknown tool" in toolset.call("nope", {})["error"]


def test_call_network_error_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args, **kwargs):
        raise httpx.ConnectError("dns failed")

    monkeypatch.setattr(httpx, "get", broken)
    out = WebSearchToolset(backoff=0.0).call("web_search", {"query": "test"})
    # Le nom du moteur est dans le message : « ça a échoué » et « DuckDuckGo a
    # échoué trois fois, Brave n'étant pas configuré » appellent des réactions
    # différentes.
    assert out["error"].startswith("no engine answered:")
    assert "duckduckgo: search failed after 3 attempt(s): ConnectError" in out["error"]
    assert out["type"] == "tool_error"


def test_a_dropped_connection_is_retried_and_can_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ConnectError: [WinError 10054]` is the normal failure of this endpoint.

    It killed the whole `/web` command on the first try, which the browser then
    displayed as "the model failed". One retry is what turns it into an answer.
    """
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("[WinError 10054] the peer closed the connection")
        return FakeResponse()

    monkeypatch.setattr(httpx, "get", flaky)
    out = WebSearchToolset(backoff=0.0).call("web_search", {"query": "test"})
    assert calls["n"] == 2, "the second attempt is what made this answer exist"
    assert out["count"] == 2


def test_a_persistent_drop_counts_its_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def broken(*args, **kwargs):
        calls["n"] += 1
        raise httpx.ConnectError("10054")

    monkeypatch.setattr(httpx, "get", broken)
    out = WebSearchToolset(attempts=3, backoff=0.0).call("web_search", {"query": "x"})
    assert calls["n"] == 3
    assert out["attempts"] == 3, "the message has to say it was tried, not just that it failed"


def test_a_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """403 and 404 are answers. Paying the same refusal three times is worse."""
    calls = {"n": 0}

    def refused(*args, **kwargs):
        calls["n"] += 1
        return FakeResponse(status=403)

    monkeypatch.setattr(httpx, "get", refused)
    out = WebSearchToolset(attempts=3, backoff=0.0).call("web_search", {"query": "x"})
    assert calls["n"] == 1
    assert out["attempts"] == 1
    assert "HTTPStatusError" in out["error"]


def test_call_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def throttled(*args, **kwargs):
        calls["n"] += 1
        return FakeResponse(status=503)

    monkeypatch.setattr(httpx, "get", throttled)
    out = WebSearchToolset(backoff=0.0).call("web_search", {"query": "test"})
    assert "error" in out
    assert calls["n"] == 3, "a 503 is a hiccup: the same request may work a second later"


def test_call_empty_page_reports_what_happened(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une page vide n'est pas un succès : la note nomme le moteur et ce qu'il a rendu."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(text="<html></html>"))
    out = WebSearchToolset().call("web_search", {"query": "test"})
    assert out["count"] == 0
    assert "no results" in out["note"]
    assert "duckduckgo: no result parsed" in out["note"]


def test_registered_in_build_toolsets() -> None:
    toolsets = build_toolsets()
    assert "web_search" in toolsets
    spec = toolsets["web_search"].tools[0]
    assert spec["function"]["name"] == "web_search"
    assert spec["function"]["parameters"]["required"] == ["query"]
