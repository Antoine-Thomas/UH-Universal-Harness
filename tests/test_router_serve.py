"""Jalon 6b: `uh router serve`, an OpenAI-compatible local endpoint.

No network, no real key: the chain is a stub or a RouterProvider driven by
``httpx.MockTransport``. Every security rule in the brief has a test here, and
the "known values" test looks for the token, the provider key and a prompt
fragment **in the logs** as well as in the responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time

import httpx
import pytest

pytest.importorskip("fastapi")

from uh import config as uh_config  # noqa: E402
from uh import keys as keys_mod  # noqa: E402
from uh.providers import ProviderError  # noqa: E402
from uh.router import serve  # noqa: E402

TOKEN = "known-router-token-6b"
PROVIDER_KEY = "known-provider-key-6b"
PROMPT = "prompt-marker-6b-never-logged"
BASE = "http://127.0.0.1:8780"


class StubProvider:
    name = "stub"
    model = "stub:1b"
    streaming = True

    def __init__(self, *, fail_after_token: bool = False, delay: float = 0.0) -> None:
        self.calls = 0
        self.fail_after_token = fail_after_token
        self.delay = delay

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if on_token is not None:
            on_token("bon")
            if self.fail_after_token:
                raise ProviderError("upstream broke mid-stream")
            on_token("jour")
            return {"role": "assistant", "content": "bonjour", "tool_calls": []}
        return {"role": "assistant", "content": "bonjour", "tool_calls": []}


class SilentProvider(StubProvider):
    streaming = False


def allow_remote() -> None:
    """Remote calls need both the switch and a recorded consent."""
    from uh.router.policy import record_consent

    uh_config.write_layer("user", "allow_remote", True)
    record_consent()


def make_app(chain=None, **kwargs):
    return serve.create_router_app(
        token=TOKEN,
        port=8780,
        chain=chain if chain is not None else [("stub", StubProvider())],
        **kwargs,
    )


def call(app, method="GET", path="/v1/models", *, token=TOKEN, host="127.0.0.1:8780", **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("Host", host)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8780") as http:
            return await http.request(method, path, headers=headers, **kwargs)

    return asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# authentication and host check
# --------------------------------------------------------------------------- #
def test_missing_token_is_401() -> None:
    response = call(make_app(), token=None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_wrong_token_is_401() -> None:
    assert call(make_app(), token="nope").status_code == 401


def test_cookie_alone_is_not_authentication() -> None:
    response = call(make_app(), token=None, headers={"Cookie": f"token={TOKEN}"})
    assert response.status_code == 401


def test_forged_host_is_refused() -> None:
    response = call(make_app(), host="evil.example:8780")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "host_not_allowed"


def test_valid_request_sets_no_cors_header() -> None:
    response = call(make_app())
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def test_models_include_auto_and_are_openai_shaped() -> None:
    body = call(make_app()).json()
    assert body["object"] == "list"
    ids = [row["id"] for row in body["data"]]
    assert "auto" in ids
    assert all("tools" in row for row in body["data"])


# --------------------------------------------------------------------------- #
# chat, non-stream and stream
# --------------------------------------------------------------------------- #
def test_non_stream_completion() -> None:
    response = call(
        make_app(chain=[("stub", SilentProvider())]),
        method="POST",
        path="/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": PROMPT}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "bonjour"
    assert body["choices"][0]["finish_reason"] == "stop"


def test_stream_emits_chunks_then_done() -> None:
    response = call(
        make_app(),
        method="POST",
        path="/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": PROMPT}], "stream": True},
    )
    assert response.status_code == 200
    frames = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[-1] == "data: [DONE]"
    content = ""
    for frame in frames[:-1]:
        payload = json.loads(frame[6:])
        content += payload["choices"][0]["delta"].get("content", "")
    assert content == "bonjour"


def test_mid_stream_failure_is_explicit_and_never_replayed() -> None:
    bad = StubProvider(fail_after_token=True)
    spare = StubProvider()
    response = call(
        make_app(chain=[("bad", bad), ("spare", spare)]),
        method="POST",
        path="/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": PROMPT}], "stream": True},
    )
    assert "stream_error" in response.text
    assert response.text.rstrip().endswith("data: [DONE]")
    assert spare.calls == 0, "the chain must not replay once tokens were sent"


def test_empty_messages_is_400() -> None:
    response = call(
        make_app(), method="POST", path="/v1/chat/completions", json={"messages": []}
    )
    assert response.status_code == 400


def test_body_too_large_is_413() -> None:
    app = make_app(max_body=1024)
    response = call(
        app,
        method="POST",
        path="/v1/chat/completions",
        content=b"x" * 4096,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_concurrency_cap_answers_429() -> None:
    app = make_app(chain=[("slow", StubProvider(delay=0.4))], max_concurrency=1)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        headers = {"Host": "127.0.0.1:8780", "Authorization": f"Bearer {TOKEN}"}
        body = {"model": "auto", "messages": [{"role": "user", "content": "x"}]}
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            first = asyncio.create_task(http.post("/v1/chat/completions", json=body, headers=headers))
            await asyncio.sleep(0.15)
            second = await http.post("/v1/chat/completions", json=body, headers=headers)
            await first
            return second

    assert asyncio.run(scenario()).status_code == 429


# --------------------------------------------------------------------------- #
# upstream safety
# --------------------------------------------------------------------------- #
def test_client_authorization_never_reaches_the_provider() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "model": "m",
            },
        )

    from uh.router.provider import RouterProvider

    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", PROVIDER_KEY)
    allow_remote()
    router = RouterProvider(transport=httpx.MockTransport(handler), cfg=uh_config.load())
    app = make_app(chain=[("router", router)])

    response = call(
        app,
        method="POST",
        path="/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": PROMPT}]},
    )
    assert response.status_code == 200
    assert seen, "the provider was never called"
    authorization = seen[0].get("authorization")
    assert authorization == f"Bearer {PROVIDER_KEY}"
    assert TOKEN not in authorization


def test_local_only_refuses_before_any_upstream_call() -> None:
    called: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, json={"choices": []})

    from uh.router.provider import RouterProvider

    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", PROVIDER_KEY)
    uh_config.write_layer("user", "allow_remote", False)
    cfg = uh_config.load()
    router = RouterProvider(transport=httpx.MockTransport(handler), cfg=cfg)
    app = make_app(chain=[("router", router)])

    response = call(
        app,
        method="POST",
        path="/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": PROMPT}]},
    )
    assert response.status_code == 502
    assert "local-only" in response.json()["error"]["message"]
    assert not called


# --------------------------------------------------------------------------- #
# secrets never leak
# --------------------------------------------------------------------------- #
def test_no_token_key_or_prompt_in_responses_logs_or_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", PROVIDER_KEY)
    allow_remote()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    from uh.router.provider import RouterProvider

    router = RouterProvider(transport=httpx.MockTransport(handler), cfg=uh_config.load())
    app = make_app(chain=[("router", router), ("stub", StubProvider())])

    bodies = []
    for path, payload in (
        ("/v1/models", None),
        ("/v1/chat/completions", {"model": "auto", "messages": [{"role": "user", "content": PROMPT}]}),
        (
            "/v1/chat/completions",
            {"model": "auto", "messages": [{"role": "user", "content": PROMPT}], "stream": True},
        ),
    ):
        response = call(app, method="POST" if payload else "GET", path=path, json=payload)
        bodies.append(response.text)

    logs = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (TOKEN, PROVIDER_KEY, PROMPT):
        for body in bodies:
            assert secret not in body
        assert secret not in logs

    assert "v1/chat/completions" in logs  # our own line is there...
    # ...with the provider that really answered: the router refused (401), the
    # stub answered, and the trace belongs to this request only
    assert "stub/" in logs


# --------------------------------------------------------------------------- #
# token file
# --------------------------------------------------------------------------- #
def test_token_is_created_once_and_rotated() -> None:
    first = serve.ensure_token()
    assert len(first) >= 30
    assert serve.ensure_token() == first
    assert serve.token_path().read_text(encoding="utf-8").strip() == first
    if os.name != "nt":
        mode = serve.token_path().stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    rotated = serve.rotate_token()
    assert rotated != first
    assert serve.read_token() == rotated


def test_non_loopback_bind_is_refused() -> None:
    with pytest.raises(ValueError, match="loopback only"):
        serve.create_router_app(token=TOKEN, port=8780, host="0.0.0.0")
    assert serve.run_server(host="192.168.1.10", port=8780) == 1

# --------------------------------------------------------------------------- #
# 6b.1 - reproductions of the three reported defects
# --------------------------------------------------------------------------- #
class SlowStream(StubProvider):
    """Emits a token, then waits: the stream stays open as long as we want."""

    def __init__(self, delay: float = 0.5) -> None:
        super().__init__(delay=0.0)
        self.delay = delay

    def chat(self, messages, tools, *, on_token=None):
        if on_token is not None:
            on_token("start")
            time.sleep(self.delay)
            on_token("end")
            return {"role": "assistant", "content": "startend", "tool_calls": []}
        return {"role": "assistant", "content": "startend", "tool_calls": []}


def test_chunked_body_without_content_length_is_capped() -> None:
    """A chunked request has no Content-Length: the cap must count real bytes."""
    app = make_app(max_body=1024)

    async def body():
        for _ in range(64):  # 64 KiB, far over the 1 KiB cap
            yield b"x" * 1024

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        headers = {"Host": "127.0.0.1:8780", "Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await http.post(
                "/v1/chat/completions",
                content=body(),
                headers={**headers, "Content-Type": "application/json"},
            )

    response = asyncio.run(scenario())
    assert response.status_code == 413


def _raw_scope(payload: dict) -> dict:
    body = json.dumps(payload).encode()
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8780"),
            (b"authorization", f"Bearer {TOKEN}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8780),
        "state": {},
    }


async def _drive(app, payload: dict, *, on_headers=None, scope_payload: dict | None = None):
    """Drive one request at the ASGI level: we see the state between headers and body."""
    queue: asyncio.Queue = asyncio.Queue()
    body = json.dumps(payload).encode()
    await queue.put({"type": "http.request", "body": body, "more_body": False})
    status: dict[str, int] = {}
    finished = asyncio.Event()

    async def receive():
        return await queue.get()

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
            if on_headers is not None:
                await on_headers()
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            finished.set()

    await app(_raw_scope(scope_payload or payload), receive, send)
    await asyncio.wait_for(finished.wait(), timeout=5)
    return status["code"]


def test_streaming_keeps_its_concurrency_slot_until_the_stream_ends() -> None:
    """call_next returns at headers: the slot must live until the body ends.

    httpx's ASGITransport buffers the whole body, so it cannot see the defect; the
    request is driven at the ASGI level instead, and we read ``in_flight`` right
    after the response headers, while the generator is still running.
    """
    app = make_app(chain=[("slow", SlowStream(delay=0.6))], max_concurrency=1)
    payload = {"model": "auto", "messages": [{"role": "user", "content": "x"}], "stream": True}
    observed: dict[str, int] = {}

    async def scenario():
        async def on_headers() -> None:
            observed["mid_flight"] = app.state.in_flight
            observed["concurrent"] = await _drive(app, payload)

        await _drive(app, payload, on_headers=on_headers)
        observed["after"] = app.state.in_flight

    asyncio.run(scenario())
    assert observed["mid_flight"] == 1, "the slot was released before the stream ended"
    assert observed["concurrent"] == 429, "a concurrent turn was accepted during a stream"
    assert observed["after"] == 0, "the counter did not come back to zero"


def test_client_disconnect_releases_the_slot() -> None:
    """A client that leaves mid-stream must not keep a slot for ever."""
    app = make_app(chain=[("slow", SlowStream(delay=5.0))], max_concurrency=1)
    payload = {"model": "auto", "messages": [{"role": "user", "content": "x"}], "stream": True}
    started = asyncio.Event()

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        body = json.dumps(payload).encode()
        await queue.put({"type": "http.request", "body": body, "more_body": False})
        await queue.put({"type": "http.disconnect"})

        async def receive():
            return await queue.get()

        async def send(message):
            if message["type"] == "http.response.start":
                started.set()

        task = asyncio.create_task(app(_raw_scope(payload), receive, send))
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.wait_for(task, timeout=5)  # the app must unwind, not hang
        return app.state.in_flight

    assert asyncio.run(scenario()) == 0


class ModelBound(StubProvider):
    """Answers only for one prompt marker, like a provider that lacks the model."""

    def __init__(self, name: str, marker: str) -> None:
        super().__init__()
        self.trace_name = name
        self.marker = marker

    def chat(self, messages, tools, *, on_token=None):
        text = str(messages[-1].get("content") or "")
        if self.marker not in text:
            raise ProviderError(f"{self.trace_name}: no such model")
        self.last_trace = {"provider": self.trace_name, "model": self.marker}
        return {"role": "assistant", "content": self.trace_name, "tool_calls": []}


def test_log_line_names_the_provider_of_that_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A shared last_trace credits a concurrent answer to the wrong provider."""
    caplog.set_level(logging.INFO, logger="uh.router.serve")
    app = make_app(chain=[("a", ModelBound("prov-a", "ma")), ("b", ModelBound("prov-b", "mb"))])

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        headers = {"Host": "127.0.0.1:8780", "Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            await asyncio.gather(
                http.post(
                    "/v1/chat/completions",
                    json={"model": "auto", "messages": [{"role": "user", "content": "ma"}]},
                    headers=headers,
                ),
                http.post(
                    "/v1/chat/completions",
                    json={"model": "auto", "messages": [{"role": "user", "content": "mb"}]},
                    headers=headers,
                ),
            )

    asyncio.run(scenario())
    lines = [
        record.getMessage()
        for record in caplog.records
        if "chat/completions" in record.getMessage()
    ]
    providers = sorted(line.split()[3].split("/")[0] for line in lines if len(line.split()) >= 4)
    assert providers == ["prov-a", "prov-b"], f"log lines: {lines}"
