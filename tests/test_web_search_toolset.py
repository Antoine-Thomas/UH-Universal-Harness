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
            raise httpx.HTTPStatusError("boom", request=None, response=None)


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
    out = WebSearchToolset().call("web_search", {"query": "test"})
    assert out["error"].startswith("search failed: ConnectError")


def test_call_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(status=503))
    assert "error" in WebSearchToolset().call("web_search", {"query": "test"})


def test_call_empty_page_reports_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(text="<html></html>"))
    out = WebSearchToolset().call("web_search", {"query": "test"})
    assert out["count"] == 0 and "throttled" in out["note"]


def test_registered_in_build_toolsets() -> None:
    toolsets = build_toolsets()
    assert "web_search" in toolsets
    spec = toolsets["web_search"].tools[0]
    assert spec["function"]["name"] == "web_search"
    assert spec["function"]["parameters"]["required"] == ["query"]
