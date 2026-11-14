"""Security of the Connexions panel: URL, secrets, token, CSRF, deep links.

Every test here answers a question that was asked before the HTTP/SSE transport
was activated:

* is the token out of ``config.toml``, out of every API answer, out of the error
  messages, and masked in the UI?
* is a pasted URL restricted to what UH may fetch (SSRF)?
* does ``uh web`` refuse to listen beyond loopback, and does a page the user has
  open in another tab get to POST to the local API with the cookie alone?
* is ``?panel=`` a whitelist rather than a value handed to the DOM?
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import mcp_cloud, urlguard  # noqa: E402
from uh.web.app import check_bind, create_app  # noqa: E402

TOKEN = "test-token-security"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
#: A value that must never appear in a response, a log line or an error message.
SECRET = "shhh-this-is-the-mcp-token-9f3a"


def run(coro):
    return asyncio.run(coro)


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, host: str = "127.0.0.1"):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        host=host,
        workspace=tmp_path,
        provider_factory=lambda kind, model: _StubProvider(),
        kind="stub",
        model="stub:1b",
    )


class _StubProvider:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools=None, stream=False, **kwargs):  # pragma: no cover - unused
        raise RuntimeError("not used in these tests")


async def call(app, method: str, path: str, *, headers=None, cookies=None, json_body=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE, cookies=cookies) as http:
        return await http.request(method, path, headers=headers, json=json_body)


# --------------------------------------------------------------------------- #
# the URL a person pastes (SSRF)
# --------------------------------------------------------------------------- #
def test_a_loopback_http_url_is_accepted() -> None:
    checked = urlguard.check("http://127.0.0.1:8787")
    assert checked.loopback and not checked.insecure
    assert checked.url == "http://127.0.0.1:8787"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_names_are_loopback(host: str) -> None:
    assert urlguard.check(f"http://{host}:9000").loopback


@pytest.mark.parametrize(
    "raw",
    [
        "file:///C:/Users/me/.uh/mcp-secrets.toml",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "http://user:pass@127.0.0.1:8787",
        "http://127.0.0.1:8787/#fragment",
        "http://",
        "http://127.0.0.1:99999",
        " http://127.0.0.1:8787 /x",
    ],
)
def test_what_is_not_a_server_url_is_refused(raw: str) -> None:
    with pytest.raises(urlguard.URLRejected):
        urlguard.check(raw)


def test_plain_http_to_a_remote_host_needs_an_explicit_yes() -> None:
    with pytest.raises(urlguard.URLRejected) as refused:
        urlguard.check("http://mcp.example.com/sse")
    assert "https://" in str(refused.value)

    allowed = urlguard.check("http://mcp.example.com/sse", allow_insecure=True)
    assert allowed.insecure
    assert "en clair" in allowed.warning


def test_https_to_a_remote_host_is_fine() -> None:
    checked = urlguard.check("https://mcp.example.com/sse")
    assert not checked.loopback and not checked.insecure and not checked.warning


def test_a_name_that_merely_contains_localhost_is_not_loopback() -> None:
    assert urlguard.check("https://localhost.evil.example/x").loopback is False


def test_recording_a_remote_plain_http_server_needs_the_flag(tmp_path: Path) -> None:
    with pytest.raises(mcp_cloud.CloudError):
        mcp_cloud.connect_http("remote", "http://mcp.example.com/sse")
    assert not (tmp_path / "config.toml").exists()

    created = mcp_cloud.connect_http(
        "remote", "http://mcp.example.com/sse", allow_insecure=True
    )
    assert created["insecure"] is True
    assert "en clair" in created["warning"]


# --------------------------------------------------------------------------- #
# the token: file, responses, masks
# --------------------------------------------------------------------------- #
def test_the_secrets_file_is_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    path = mcp_cloud.write_secrets("s3", {"AWS_SECRET_ACCESS_KEY": SECRET})

    assert SECRET in path.read_text(encoding="utf-8"), "the value itself is fine in the file"
    if os.name == "nt":
        acl = mcp_cloud.permissions(path)
        assert acl["owner_only"], acl["detail"]
    else:
        assert (path.stat().st_mode & 0o777) == 0o600


def test_a_widened_secrets_file_is_narrowed_when_read(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    path = mcp_cloud.write_secrets("s3", {"AWS_SECRET_ACCESS_KEY": SECRET})
    if os.name == "nt":
        pytest.skip("the ACL is what decides on Windows; chmod is not the model")
    path.chmod(0o644)
    mcp_cloud.read_secrets()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_the_token_is_masked_and_never_returned(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    created = mcp_cloud.connect_http("positron", "http://127.0.0.1:8787", token=SECRET)
    assert SECRET not in json.dumps(created)
    assert created["token"] == "••••9f3a"
    assert created["secrets"] == ["MCP_HTTP_TOKEN"]

    (row,) = mcp_cloud.pending_http_servers()
    assert row["token"] == "••••9f3a"
    assert SECRET not in json.dumps(row)


def test_no_route_returns_the_token(tmp_path: Path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    headers = {**AUTH}

    async def scenario() -> list[httpx.Response]:
        created = await call(
            app,
            "POST",
            "/api/mcp/http",
            headers=headers,
            json_body={"name": "positron", "url": "http://127.0.0.1:8787", "token": SECRET},
        )
        assert created.status_code == 200, created.text
        return [
            created,
            await call(app, "GET", "/api/mcp/cloud", headers=headers),
            await call(app, "GET", "/api/tools/status", headers=headers),
            await call(app, "GET", "/api/status", headers=headers),
            await call(app, "POST", "/api/mcp/positron/test", headers=headers),
        ]

    responses = run(scenario())
    for response in responses:
        assert SECRET not in response.text, f"{response.request.url} leaked the token"
    assert "••••9f3a" in responses[0].text
    assert "••••9f3a" in responses[1].text


def test_an_error_message_does_not_quote_the_token(tmp_path: Path, monkeypatch) -> None:
    """A server that dumps its environment on start must not leak the token."""
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    mcp_cloud.write_secrets("echoer", {"TOKEN": SECRET})
    assert SECRET not in mcp_cloud.redact(f"env: TOKEN={SECRET}")
    assert "••••9f3a" in mcp_cloud.redact(f"env: TOKEN={SECRET}")


# --------------------------------------------------------------------------- #
# the local API: loopback, Origin, cookie vs bearer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_binds_are_accepted(host: str) -> None:
    assert check_bind(host) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "mcp.example.com"])
def test_a_remote_bind_needs_the_flag(host: str) -> None:
    refusal = check_bind(host)
    assert refusal is not None and "--allow-remote" in refusal
    assert check_bind(host, allow_remote=True) is None


def test_a_cookie_only_post_without_origin_is_refused(tmp_path: Path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    cookie = {"uh_token": TOKEN}

    async def scenario():
        return await call(
            app,
            "POST",
            "/api/mcp/http",
            cookies=cookie,
            json_body={"name": "evil", "url": "http://127.0.0.1:8787"},
        )

    refused = run(scenario())
    assert refused.status_code == 403
    assert "origine" in refused.text
    assert not (tmp_path / "home" / "config.toml").exists()


def test_a_cookie_post_from_another_origin_is_refused(tmp_path: Path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        return await call(
            app,
            "POST",
            "/api/mcp/http",
            headers={"Origin": "http://evil.example"},
            cookies={"uh_token": TOKEN},
            json_body={"name": "evil", "url": "http://127.0.0.1:8787"},
        )

    refused = run(scenario())
    assert refused.status_code == 400
    assert "origin" in refused.text


def test_a_cookie_post_from_the_page_itself_is_accepted(tmp_path: Path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        return await call(
            app,
            "POST",
            "/api/mcp/http",
            headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"},
            cookies={"uh_token": TOKEN},
            json_body={"name": "positron", "url": "http://127.0.0.1:8787"},
        )

    accepted = run(scenario())
    assert accepted.status_code == 200, accepted.text


def test_a_cross_site_request_is_refused_even_with_a_bearer(tmp_path: Path, monkeypatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        return await call(
            app,
            "POST",
            "/api/mcp/http",
            headers={**AUTH, "Sec-Fetch-Site": "cross-site"},
            json_body={"name": "evil", "url": "http://127.0.0.1:8787"},
        )

    refused = run(scenario())
    assert refused.status_code == 400


def test_a_bearer_post_without_origin_is_accepted(tmp_path: Path, monkeypatch) -> None:
    """A CLI or a test client has no browser origin to prove: its token is explicit."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        return await call(
            app,
            "POST",
            "/api/mcp/http",
            headers=AUTH,
            json_body={"name": "positron", "url": "http://127.0.0.1:8787"},
        )

    accepted = run(scenario())
    assert accepted.status_code == 200, accepted.text


def test_another_host_header_is_refused(tmp_path: Path, monkeypatch) -> None:
    """DNS rebinding: a page on evil.example that resolves to 127.0.0.1 too."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        return await call(app, "GET", "/api/status", headers={**AUTH, "Host": "evil.example"})

    refused = run(scenario())
    assert refused.status_code == 400


# --------------------------------------------------------------------------- #
# ?panel= is a whitelist
# --------------------------------------------------------------------------- #
def test_the_deep_link_is_a_whitelist_not_an_interpolation() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    start = source.index("function openPanelFromUrl()")
    body = source[start : start + 400]
    assert "PANEL_TABS.has(wanted)" in body, "the value must be checked against a set"
    assert "innerHTML" not in body
    assert "location.href =" not in body


def test_the_documented_tabs_are_the_ones_the_panel_knows() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    known = {"keys", "providers", "conn", "models", "uhb", "memory", "decision", "telemetry", "data"}
    for tab in known:
        assert f'"{tab}"' in source
