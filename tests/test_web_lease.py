"""The cockpit's lease: a closed window stops the server it opened; a reload does not.

No sleep, no second brain, no signal. The clock, the UHB stop and the process exit are
all injected, so what is asserted is the DECISION and its order - not the platform the
test happens to run on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import (  # noqa: E402
    LEASE_TTL_HIDDEN_S,
    LEASE_TTL_S,
    create_app,
    lease_expired,
    lease_tick,
    start_lease_watchdog,
)

TOKEN = "lease-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, app_window: bool = True):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(token=TOKEN, port=8765, workspace=tmp_path, app_window=app_window)


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #
def test_a_reload_is_not_a_closed_window() -> None:
    """F5 leaves a gap of about a second; 45 s of silence is a window that is gone."""
    assert not lease_expired(
        last_seen=100.0,
        now=100.0 + LEASE_TTL_S - 5,
        visible=True,
        active=False,
        app_window=True,
    )
    assert lease_expired(
        last_seen=100.0,
        now=100.0 + LEASE_TTL_S + 1,
        visible=True,
        active=False,
        app_window=True,
    )


def test_a_minimised_window_keeps_the_longer_budget() -> None:
    """Killing a product because the user switched to another app is not a fix."""
    assert not lease_expired(
        last_seen=0.0,
        now=LEASE_TTL_S * 2,
        visible=False,
        active=False,
        app_window=True,
    )
    assert lease_expired(
        last_seen=0.0,
        now=LEASE_TTL_HIDDEN_S + 1,
        visible=False,
        active=False,
        app_window=True,
    )


def test_a_turn_in_flight_is_never_interrupted() -> None:
    """The same perimeter as `/api/system/quit`: a running turn is a 409, not a kill."""
    assert not lease_expired(
        last_seen=0.0,
        now=LEASE_TTL_S * 10,
        visible=True,
        active=True,
        app_window=True,
    )


def test_a_server_started_by_hand_is_never_stopped() -> None:
    """`uh web` without --app belongs to whoever started it: quit is their gesture."""
    assert not lease_expired(
        last_seen=0.0,
        now=1e9,
        visible=True,
        active=False,
        app_window=False,
    )


# --------------------------------------------------------------------------- #
# the tick: order matters
# --------------------------------------------------------------------------- #
def test_a_tick_asks_uhb_to_stop_before_the_process_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    app.state.lease_seen = 1_000.0
    order: list[str] = []

    acted = lease_tick(
        app,
        now=1_000.0 + LEASE_TTL_S + 1,
        stop=lambda: order.append("uhb") or True,
        exit_now=lambda code: order.append(f"exit:{code}"),
    )

    assert acted is True
    assert order == ["uhb", "exit:0"], "UHB is stopped before the server leaves"


def test_a_tick_leaves_a_live_window_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    app.state.lease_seen = 1_000.0
    order: list[str] = []

    acted = lease_tick(
        app,
        now=1_000.0 + LEASE_TTL_S - 1,
        stop=lambda: order.append("uhb") or True,
        exit_now=lambda code: order.append(f"exit:{code}"),
    )

    assert acted is False
    assert order == []


def test_a_tick_with_a_live_turn_does_not_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    app.state.lease_seen = 1_000.0
    app.state.active["session-1"] = object()
    order: list[str] = []

    acted = lease_tick(
        app,
        now=1_000.0 + LEASE_TTL_S * 4,
        stop=lambda: order.append("uhb") or True,
        exit_now=lambda code: order.append(f"exit:{code}"),
    )

    assert acted is False
    assert order == []


def test_a_failing_stop_does_not_keep_a_dead_window_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leak is a warning; serving a window nobody can see is the bug being fixed."""
    app = make_app(tmp_path, monkeypatch)
    app.state.lease_seen = 1_000.0
    exits: list[int] = []

    def explode() -> bool:
        raise OSError("no token, no route")

    acted = lease_tick(
        app,
        now=1_000.0 + LEASE_TTL_S + 1,
        stop=explode,
        exit_now=exits.append,
    )

    assert acted is True
    assert exits == [0]


def test_no_window_means_no_watchdog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch, app_window=False)

    assert start_lease_watchdog(app) is None


# --------------------------------------------------------------------------- #
# the route the page calls
# --------------------------------------------------------------------------- #
def test_the_page_renews_its_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    app.state.clock = lambda: 5_000.0

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await http.post(
                "/api/system/alive", json={"visible": False}, headers=AUTH
            )

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json() == {"alive": True, "window": True, "ttl_s": LEASE_TTL_HIDDEN_S}
    assert app.state.lease_seen == 5_000.0
    assert app.state.lease_visible is False


def test_the_lease_route_needs_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await http.post("/api/system/alive", json={"visible": True})

    assert asyncio.run(scenario()).status_code == 401


def test_a_cross_site_page_cannot_hold_a_dead_window_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lease is a state-changing POST: the Origin guard applies to it too."""
    app = make_app(tmp_path, monkeypatch)
    app.state.clock = lambda: 7_000.0

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await http.post(
                "/api/system/alive",
                json={"visible": True},
                headers={**AUTH, "Origin": "http://evil.example"},
            )

    assert asyncio.run(scenario()).status_code == 403
    assert app.state.lease_seen != 7_000.0
