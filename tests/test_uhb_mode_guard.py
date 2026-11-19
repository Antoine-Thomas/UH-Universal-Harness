"""Point 4: `POST /api/uhb/mode` is a local, same-origin, token-bearing gesture.

Switching the mode is the switch that turns code execution on. It is reachable
from the cockpit, and from nowhere else: not from a page the operator happens to
have open (Origin / Sec-Fetch-Site / cookie rules), not from a machine that is not
this one (Host + loopback bind), not without the token, and not from the model's
own toolbox — a tool that could flip it would be a permission that grants itself.

Each test fails for one, and only one, reason, so a regression names its cause.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-mode-guard"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MODE = "/api/uhb/mode"


class StubProvider:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools=None, stream=False, **kwargs):  # pragma: no cover
        raise RuntimeError("no turn is run in these tests")


def make_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, host: str = "127.0.0.1", port: int = 8765
):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("UH_UHB_URL", "")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=port,
        host=host,
        workspace=tmp_path,
        provider_factory=lambda kind, model: StubProvider(),
        kind="stub",
        model="stub:1b",
    )


def post(app, *, headers=None, cookies=None, json_body=None):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE, cookies=cookies) as http:
            return await http.request("POST", MODE, headers=headers, json=json_body or {"mode": "dev"})

    return asyncio.run(run())


@pytest.fixture()
def uhb_untouched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any instantiation of the UHB client is a failure: the guard runs first."""
    touched: list[str] = []

    class Bomb:
        def __init__(self, *args, **kwargs):
            touched.append("Uhbtoolset")
            raise AssertionError("UHB was touched by a request that should have been refused")

    monkeypatch.setattr("uh.toolsets.uhb.Uhbtoolset", Bomb)
    return touched


# --------------------------------------------------------------------------- #
# the browser-side proofs
# --------------------------------------------------------------------------- #
def test_a_cross_origin_post_is_refused(tmp_path: Path, monkeypatch, uhb_untouched) -> None:
    app = make_app(tmp_path, monkeypatch)

    response = post(app, headers={**AUTH, "origin": "https://evil.example"})

    assert response.status_code == 403, response.text
    assert "origin not allowed" in response.text
    assert uhb_untouched == [], "the mode of UHB is untouched by a refused request"


def test_a_cross_site_fetch_is_refused_even_without_an_origin(
    tmp_path: Path, monkeypatch, uhb_untouched
) -> None:
    """A page cannot forge `Sec-Fetch-Site`; that is why the header is trusted."""
    app = make_app(tmp_path, monkeypatch)

    response = post(app, headers={**AUTH, "sec-fetch-site": "cross-site"})

    assert response.status_code == 403, response.text
    assert "cross-site request refused" in response.text
    assert uhb_untouched == []


def test_a_non_loopback_host_header_is_refused(tmp_path: Path, monkeypatch, uhb_untouched) -> None:
    """DNS rebinding: the request arrives with somebody else's name in Host."""
    app = make_app(tmp_path, monkeypatch)

    response = post(app, headers={**AUTH, "host": "evil.example"})

    assert response.status_code == 403, response.text
    assert "host not allowed" in response.text
    assert uhb_untouched == []


def test_without_a_token_the_route_answers_401(tmp_path: Path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    response = post(app)

    assert response.status_code == 401, response.text
    assert "token" in response.text.lower()


def test_a_cookie_only_call_without_origin_proof_is_refused(
    tmp_path: Path, monkeypatch, uhb_untouched
) -> None:
    """The real CSRF case: the browser sends the cookie, the page sends the request."""
    app = make_app(tmp_path, monkeypatch)

    response = post(app, cookies={"uh_token_8765": TOKEN})

    assert response.status_code == 403, response.text
    assert "preuve d'origine" in response.text
    assert uhb_untouched == []


def test_a_cookie_with_the_right_origin_is_allowed_through_the_guard(
    tmp_path: Path, monkeypatch
) -> None:
    """The guard must let the cockpit work: a same-origin cookie call passes."""
    app = make_app(tmp_path, monkeypatch)

    response = post(
        app,
        cookies={"uh_token_8765": TOKEN},
        headers={"origin": BASE, "sec-fetch-site": "same-origin"},
    )

    # Past the guard, the route answers for itself: UHB is disabled in this test.
    assert response.status_code == 400, response.text
    assert "UHB is disabled" in response.text


# --------------------------------------------------------------------------- #
# the machine-side proofs
# --------------------------------------------------------------------------- #
def test_a_server_listening_beyond_loopback_refuses_to_change_the_mode(
    tmp_path: Path, monkeypatch
) -> None:
    """A remote cockpit must not be able to open code execution, token or not."""
    app = make_app(tmp_path, monkeypatch, host="0.0.0.0")

    response = post(app, headers={**AUTH, "origin": BASE})

    assert response.status_code == 403, response.text
    assert "loopback" in response.text


def test_no_tool_can_reach_the_mode_route() -> None:
    """The model's toolbox must not contain a way to grant itself a permission.

    A source-level guard on purpose: the failure mode is a future tool (or an MCP
    server registered here) that POSTs to this route, and no runtime test would see
    it until it had already flipped the mode.
    """
    root = Path(__file__).resolve().parents[1]
    forbidden = ("/api/uhb/mode", "api/uhb/mode")
    offenders: list[str] = []
    for folder in (root / "uh" / "toolsets", root / "uh" / "mcp.py", root / "uh" / "mcp_http.py",
                   root / "uh" / "agent.py", root / "uh" / "providers"):
        paths = [folder] if folder.is_file() else list(folder.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(marker in text for marker in forbidden):
                offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"a tool path can reach the mode route: {offenders}"


def test_the_mode_route_exists_once_and_requires_the_token() -> None:
    """Two definitions would be a second door; the token dependency is the lock."""
    source = (Path(__file__).resolve().parents[1] / "uh" / "web" / "app.py").read_text(
        encoding="utf-8"
    )
    assert source.count('@app.post("/api/uhb/mode"') == 1
    line = next(ligne for ligne in source.splitlines() if '@app.post("/api/uhb/mode"' in ligne)
    assert "Depends(require_token)" in line
