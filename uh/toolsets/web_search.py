"""Web search toolset: DuckDuckGo HTML endpoint, no API key, no extra dependency.

Design notes
------------
* Endpoint: ``https://html.duckduckgo.com/html/?q=...`` — the no-JS HTML
  frontend. Results are ``<a class="result__a" href="...">`` plus
  ``<a class="result__snippet">``; hrefs are redirect wrappers
  (``//duckduckgo.com/l/?uddg=<url-encoded target>``), decoded here.
* Transport: ``httpx`` (already a core dependency, used by the providers).
  Parsing: stdlib ``html.parser`` — no new entry in ``pyproject.toml``.
* Fail-closed: network errors, HTTP errors, bot-wall answers and malformed
  markup all return ``{"error": ...}`` or an empty result list — this toolset
  never raises out of ``call`` for anything the network can do.
* DuckDuckGo rate-limits the HTML endpoint; this is a best-effort search, not
  a guaranteed one. A 202/empty page means "throttled", reported as such.
"""

from __future__ import annotations

import urllib.parse
from html.parser import HTMLParser
from typing import Dict, List

import httpx

DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 5
TIMEOUT = 10.0
# A plain httpx UA gets an empty 202 (bot wall); a browser UA gets results.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web via DuckDuckGo. Returns up to 5 results "
                "(title, url, snippet). No API key needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results, 1-5 (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class _ResultsParser(HTMLParser):
    """Extract (title, url, snippet) triples from the DDG HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._capture: str | None = None  # "title" | "snippet"
        self._chunks: List[str] = []
        self._href: str = ""

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        if tag != "a":
            return
        classes = dict(attrs).get("class", "")
        if "result__a" in classes:
            self._capture = "title"
            self._chunks = []
            self._href = dict(attrs).get("href", "")
        elif "result__snippet" in classes:
            self._capture = "snippet"
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._capture:
            return
        text = " ".join("".join(self._chunks).split())
        if self._capture == "title":
            self.results.append({"title": text, "url": _decode_href(self._href), "snippet": ""})
        elif self._capture == "snippet" and self.results:
            self.results[-1]["snippet"] = text
        self._capture = None
        self._chunks = []


def _decode_href(href: str) -> str:
    """Unwrap DDG redirect links; pass through anything already absolute."""
    if href.startswith(("http://", "https://")):
        return href
    parsed = urllib.parse.urlparse(f"https:{href}" if href.startswith("//") else href)
    uddg = urllib.parse.parse_qs(parsed.query).get("uddg")
    return uddg[0] if uddg else href


def parse_results(html: str) -> List[Dict[str, str]]:
    parser = _ResultsParser()
    parser.feed(html)
    return [r for r in parser.results if r["url"].startswith(("http://", "https://"))]


class WebSearchToolset:
    name = "web_search"
    tools = TOOLS

    def __init__(self, *, endpoint: str = DDG_ENDPOINT, timeout: float = TIMEOUT) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    def call(self, name: str, args: dict | None = None) -> dict:
        if name != "web_search":
            return {"error": f"unknown tool: {name}"}
        args = dict(args or {})
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        try:
            max_results = min(MAX_RESULTS, max(1, int(args.get("max_results") or MAX_RESULTS)))
        except (TypeError, ValueError):
            return {"error": "max_results must be an integer between 1 and 5"}
        try:
            response = httpx.get(
                self.endpoint,
                params={"q": query},
                headers={"User-Agent": USER_AGENT},
                timeout=self.timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - any network failure is a tool error
            return {"error": f"search failed: {type(exc).__name__}: {exc}"}
        results = parse_results(response.text)
        if not results:
            return {
                "query": query,
                "results": [],
                "count": 0,
                "note": "no results parsed - possibly throttled by DuckDuckGo, retry later",
            }
        return {"query": query, "results": results[:max_results], "count": min(len(results), max_results)}


__all__ = ["WebSearchToolset", "parse_results", "DDG_ENDPOINT", "MAX_RESULTS"]
