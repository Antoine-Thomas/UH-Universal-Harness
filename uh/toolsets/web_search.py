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

import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, List

import httpx

DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 5
TIMEOUT = 10.0
#: A dropped connection is the normal failure of this endpoint: DuckDuckGo (or a
#: local middlebox) closes the socket under a request it has not answered yet.
#: Seen in the field as ``ConnectError: [WinError 10054]`` on every ``/web``
#: command, which used to surface as a red "the model failed" bubble after 100 ms
#: - the model had not been called at all. One retry costs 0.4 s and turns a
#: transient close into an answer.
ATTEMPTS = 3
BACKOFF_S = 0.4
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


class _SearchFailed(Exception):
    """Every attempt failed, or the endpoint answered with something final."""

    def __init__(self, cause: BaseException, attempts: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempts = attempts


class WebSearchToolset:
    name = "web_search"
    tools = TOOLS

    def __init__(
        self,
        *,
        endpoint: str = DDG_ENDPOINT,
        timeout: float = TIMEOUT,
        attempts: int = ATTEMPTS,
        backoff: float = BACKOFF_S,
        sleep: Any = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.attempts = max(1, int(attempts))
        self.backoff = max(0.0, float(backoff))
        self._sleep = sleep or time.sleep

    def _fetch(self, params: Dict[str, Any]) -> httpx.Response:
        """GET the endpoint, retrying only what a retry can fix.

        A transport error (connect, read, protocol, timeout) and a 5xx/429 are
        hiccups: the same request may work a second later. Any other status is an
        ANSWER - retrying a 403 just pays the same refusal twice.
        """
        failure: BaseException | None = None
        for attempt in range(1, self.attempts + 1):
            if attempt > 1:
                self._sleep(self.backoff * (2 ** (attempt - 2)))
            try:
                response = httpx.get(
                    self.endpoint,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=self.timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", 0) or 0
                if status != 429 and status < 500:
                    raise _SearchFailed(exc, attempt) from exc
                failure = exc
            except httpx.TransportError as exc:
                failure = exc
        raise _SearchFailed(failure or RuntimeError("no attempt was made"), self.attempts)

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
            response = self._fetch({"q": query})
        except _SearchFailed as exc:
            # The attempts count is part of the message: "it failed" and "it
            # failed three times over a second" call for different reactions.
            return {
                "error": (
                    f"search failed after {exc.attempts} attempt(s): "
                    f"{type(exc.cause).__name__}: {exc.cause}"
                ),
                "type": "tool_error",
                "tool": "web_search",
                "attempts": exc.attempts,
            }
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
