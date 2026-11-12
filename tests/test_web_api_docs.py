"""Jalon 7b.2: the API contract in ``docs/API.md`` must match the application.

A route that exists without being documented is a route a future interface will
not know about - or will use wrongly. This test makes the drift fail loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from uh.web.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "API.md"
INDEX = ROOT / "uh" / "web" / "static" / "index.html"


def app():
    return create_app(
        token="doc-token",
        port=1,
        host="127.0.0.1",
        workspace=ROOT,
        provider_factory=lambda kind, model: None,
        kind="stub",
        model="stub:1b",
    )


ROUTER_MARK = "## Router API"


def _web_section() -> str:
    return DOC.read_text(encoding="utf-8").split(ROUTER_MARK)[0]


def _router_section() -> str:
    parts = DOC.read_text(encoding="utf-8").split(ROUTER_MARK)
    return parts[1] if len(parts) > 1 else ""


def documented_routes() -> set[tuple[str, str]]:
    text = _web_section()
    found: set[tuple[str, str]] = set()
    for method, path in re.findall(r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`", text, re.M):
        path = path.split("?")[0]
        if path.startswith("/static"):
            continue  # a mount, documented separately, not an app route
        found.add((method, path))
    return found


def documented_router_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for method, path in re.findall(
        r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`", _router_section(), re.M
    ):
        found.add((method, path.split("?")[0]))
    return found


def router_application_routes() -> set[tuple[str, str]]:
    from uh.router.serve import create_router_app

    app = create_router_app(token="doc-token", port=8780, chain=[("stub", object())])
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", None) or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.add((method, path))
    return routes


def application_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app().routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path.startswith("/static"):
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.add((method, path))
    return routes


def test_every_route_is_documented() -> None:
    missing = application_routes() - documented_routes()
    assert not missing, f"routes missing from docs/API.md: {sorted(missing)}"


def test_no_documented_route_is_invented() -> None:
    extra = documented_routes() - application_routes()
    assert not extra, f"docs/API.md documents routes that do not exist: {sorted(extra)}"


def test_interface_element_ids_are_listed() -> None:
    documented = DOC.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', INDEX.read_text(encoding="utf-8")))
    missing = {element for element in ids if element not in documented}
    assert not missing, f"element ids missing from docs/API.md: {sorted(missing)}"


def test_static_dir_serves_the_replacement_interface(tmp_path: Path) -> None:
    import asyncio

    import httpx

    (tmp_path / "index.html").write_text("<html><body>replacement</body></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("// nothing", encoding="utf-8")

    replacement = create_app(
        token="doc-token",
        port=1,
        host="127.0.0.1",
        workspace=ROOT,
        provider_factory=lambda kind, model: None,
        kind="stub",
        model="stub:1b",
        static_dir=tmp_path,
    )

    async def scenario():
        transport = httpx.ASGITransport(app=replacement)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:1") as http:
            page = await http.get("/?token=doc-token")
            assert page.status_code == 200
            assert "replacement" in page.text
            # the protections are the same as for the bundled interface
            assert "unsafe-inline" not in page.headers["content-security-policy"]
            assert page.headers["x-frame-options"] == "DENY"
            asset = await http.get("/static/app.js")
            assert asset.status_code == 200
            assert (await http.get("/static/../app.py")).status_code in {404, 403}

    asyncio.run(scenario())


def test_router_routes_are_documented() -> None:
    missing = router_application_routes() - documented_router_routes()
    assert not missing, f"router routes missing from docs/API.md: {sorted(missing)}"


def test_no_router_route_is_invented() -> None:
    extra = documented_router_routes() - router_application_routes()
    assert not extra, f"docs/API.md documents router routes that do not exist: {sorted(extra)}"
