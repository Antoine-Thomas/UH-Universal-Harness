"""``uh router serve`` - a local, OpenAI-compatible entry point (Jalon 6b).

Other local programs (editors, scripts, OpenAI clients) can then use the keys and
the fallback chain UH already holds, without reconfiguring each provider.

Security is the point of this module, so the trust boundary is stated once:

* **loopback only** - any other bind address is refused at startup, no override;
* **its own token**, in ``$UH_HOME/router-token`` (0600 where possible), never the
  ``uh web`` token, compared in constant time, accepted **only** as
  ``Authorization: Bearer`` (no cookie, hence no CSRF);
* ``Host`` checked against loopback names and the bound port (DNS rebinding);
* no CORS header is ever emitted;
* the client's ``Authorization`` **never** reaches a provider: upstream calls use
  the stored provider key, and no other client header is relayed;
* a body cap and a concurrency cap keep a runaway client from burning free
  quotas - over the cap, a local ``429``;
* logs carry method, path, status, provider/model and duration only - never a
  prompt, a completion, a key or the token.

The fallback logic is **not** duplicated here: ``RouterProvider`` owns the pool,
the cooldowns, the breaker and the mid-stream rule (never replay after tokens
were emitted). This module only speaks HTTP around it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import config as uh_config
from ..providers import ProviderError

LOG = logging.getLogger("uh.router.serve")

LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")
DEFAULT_PORT = 8780
MAX_BODY_BYTES = 1_048_576  # 1 MiB: a chat request is a few kilobytes
MAX_CONCURRENCY = 4  # parallel turns accepted before answering 429
TOKEN_FILENAME = "router-token"
AUTO_MODEL = "auto"


# --------------------------------------------------------------------------- #
# token
# --------------------------------------------------------------------------- #
def token_path() -> Path:
    return uh_config.uh_home() / TOKEN_FILENAME


def _write_token(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")
    os.replace(tmp, path)
    try:  # best effort: Windows maps this to a read-only flag
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_token() -> str:
    """The current token, or ``""`` when none was ever created."""
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_token() -> str:
    current = read_token()
    if current:
        return current
    value = secrets.token_urlsafe(32)
    _write_token(token_path(), value)
    return value


def rotate_token() -> str:
    value = secrets.token_urlsafe(32)
    _write_token(token_path(), value)
    return value


# --------------------------------------------------------------------------- #
# chain
# --------------------------------------------------------------------------- #
def build_chain(cfg: Any = None, transport: Any = None) -> list[tuple[str, Any]]:
    """Local providers first, then the router. The router keeps the fallback."""
    from ..providers.ollama import OllamaProvider, list_models
    from .provider import RouterProvider

    cfg = cfg if cfg is not None else uh_config.load()
    chain: list[tuple[str, Any]] = []
    try:
        models = list_models()
    except ProviderError:
        models = []
    if models:
        chain.append(("ollama", OllamaProvider(str(models[0]["name"]))))
    try:
        chain.append(("router", RouterProvider(cfg=cfg, transport=transport)))
    except ProviderError:
        pass
    return chain


def _call_chain(
    chain: list[tuple[str, Any]],
    messages: list[dict],
    tools: list[dict],
    on_token: Optional[Callable[[str], None]] = None,
) -> dict:
    """First provider that answers wins; a failure after emitting never replays."""
    errors: list[str] = []
    emitted = False

    def wrapped(fragment: str) -> None:
        nonlocal emitted
        emitted = True
        if on_token is not None:
            on_token(fragment)

    for kind, provider in chain:
        try:
            if on_token is not None and getattr(provider, "streaming", False):
                answer: dict = provider.chat(messages, tools, on_token=wrapped)
            else:
                answer = provider.chat(messages, tools)
            return answer
        except ProviderError as exc:
            errors.append(f"{kind}: {exc}")
            if emitted:
                # the client already received tokens: stop, never replay
                raise
    raise ProviderError("no provider answered (" + "; ".join(errors[:4]) + ")")


# --------------------------------------------------------------------------- #
# OpenAI shapes
# --------------------------------------------------------------------------- #
async def _read_body(request: Request, limit: int) -> Optional[bytes]:
    """Read the body, stopping as soon as ``limit`` is exceeded.

    ``Content-Length`` is only a hint: a chunked request has none, so the cap has
    to count the bytes actually received - and must not buffer them all before
    refusing (a client sending gigabytes would exhaust memory first).
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _trace_of(chain: list[tuple[str, Any]]) -> dict[str, str]:
    """The provider that actually answered, from its own trace when it has one."""
    for kind, provider in chain:
        trace = getattr(provider, "last_trace", None)
        if trace:
            return {
                "provider": str(trace.get("provider") or kind),
                "model": str(trace.get("model") or "-"),
            }
    return {"provider": chain[0][0] if chain else "-", "model": "-"}


def _error(status: int, message: str, type_: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": type_, "code": code, "param": None}},
    )


def _completion(message: dict, model: str, completion_id: str) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    **({"tool_calls": message["tool_calls"]} if message.get("tool_calls") else {}),
                },
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _chunk(text: str, model: str, completion_id: str, done: bool = False) -> str:
    delta = {} if done else {"content": text}
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": "stop" if done else None}
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #
def create_router_app(
    *,
    token: str,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    chain: Optional[list[tuple[str, Any]]] = None,
    cfg: Any = None,
    transport: Any = None,
    max_concurrency: int = MAX_CONCURRENCY,
    max_body: int = MAX_BODY_BYTES,
) -> FastAPI:
    if host not in LOOPBACK_NAMES:
        raise ValueError(f"uh router serve refuses to bind {host}: loopback only")

    app = FastAPI(
        title="UH router",
        version="0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # no schema to browse: less surface
    )
    app.state.token = token
    app.state.port = port
    app.state.host = host
    app.state.chain = chain
    app.state.cfg = cfg
    app.state.transport = transport
    app.state.max_concurrency = max(1, int(max_concurrency))
    app.state.max_body = max(1024, int(max_body))
    app.state.in_flight = 0
    app.state.lock = threading.Lock()
    app.state.last_trace = {"provider": "-", "model": "-"}

    def allowed_hosts() -> set[str]:
        return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"::1:{port}"}

    def _authorised(request: Request) -> bool:
        header = request.headers.get("authorization") or ""
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return False
        return secrets.compare_digest(value.strip(), app.state.token)

    def _chain() -> list[tuple[str, Any]]:
        if app.state.chain is None:
            app.state.chain = build_chain(app.state.cfg, app.state.transport)
        chain: list[tuple[str, Any]] = app.state.chain
        return chain

    @app.middleware("http")
    async def guard(request: Request, call_next):
        started = time.perf_counter()
        host = request.headers.get("host") or ""
        if host not in allowed_hosts():
            return _error(400, "host not allowed", "invalid_request_error", "host_not_allowed")
        # Cookie auth is deliberately unsupported: a browser page cannot be
        # tricked into calling this server on the user's behalf.
        if not _authorised(request):
            return _error(401, "missing or invalid bearer token", "invalid_request_error", "invalid_api_key")

        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > app.state.max_body:
                    return _error(413, "request body too large", "invalid_request_error", "body_too_large")
            except ValueError:
                return _error(400, "invalid content-length", "invalid_request_error", "bad_request")

        with app.state.lock:
            if app.state.in_flight >= app.state.max_concurrency:
                return _error(429, "too many concurrent requests", "rate_limit_error", "too_many_requests")
            app.state.in_flight += 1
        try:
            response = await call_next(request)
        finally:
            with app.state.lock:
                app.state.in_flight -= 1

        trace = app.state.last_trace
        LOG.info(
            "%s %s %s %s/%s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            trace.get("provider", "-"),
            trace.get("model", "-"),
            (time.perf_counter() - started) * 1000,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # No CORS header, ever: a browser must not be able to read this API.
        return response

    @app.get("/v1/models")
    async def models() -> dict:
        rows: list[dict] = [{"id": AUTO_MODEL, "object": "model", "owned_by": "uh", "tools": True}]
        seen = {AUTO_MODEL}
        for kind, provider in _chain():
            if kind == "ollama":
                from ..providers.ollama import list_models

                try:
                    for entry in list_models():
                        name = str(entry["name"])
                        if name not in seen:
                            seen.add(name)
                            rows.append({"id": name, "object": "model", "owned_by": kind, "tools": True})
                except ProviderError:
                    continue
            else:
                state_rows = getattr(provider, "status_rows", None)
                for row in (state_rows() if callable(state_rows) else []):
                    for model in row.get("models") or []:
                        model_id = model.get("id") if isinstance(model, dict) else str(model)
                        if model_id and model_id not in seen:
                            seen.add(model_id)
                            rows.append(
                                {
                                    "id": model_id,
                                    "object": "model",
                                    "owned_by": row.get("provider", kind),
                                    "tools": bool(model.get("tools")) if isinstance(model, dict) else False,
                                }
                            )
        return {"object": "list", "data": rows}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        raw = await _read_body(request, app.state.max_body)
        if raw is None:
            # chunked request over the cap: Content-Length said nothing
            return _error(413, "request body too large", "invalid_request_error", "body_too_large")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return _error(400, "invalid JSON body", "invalid_request_error", "bad_request")
        if not isinstance(payload, dict):
            return _error(400, "body must be a JSON object", "invalid_request_error", "bad_request")

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return _error(400, "'messages' must be a non-empty array", "invalid_request_error", "bad_request")
        for message in messages:
            if not isinstance(message, dict) or "role" not in message:
                return _error(400, "each message needs a 'role'", "invalid_request_error", "bad_request")

        tools = payload.get("tools") or []
        if not isinstance(tools, list):
            return _error(400, "'tools' must be an array", "invalid_request_error", "bad_request")

        model = str(payload.get("model") or AUTO_MODEL)
        stream = bool(payload.get("stream"))
        chain = _chain()
        if not chain:
            return _error(
                503,
                "no provider available: install a local model or add a free provider key",
                "service_unavailable",
                "no_provider",
            )
        completion_id = f"chatcmpl-{secrets.token_hex(12)}"

        if not stream:
            try:
                answer = await asyncio.to_thread(_call_chain, chain, messages, tools)
            except ProviderError as exc:
                app.state.last_trace = {"provider": "-", "model": model}
                return _error(502, str(exc), "upstream_error", "provider_error")
            app.state.last_trace = {**_trace_of(chain), "model": _trace_of(chain)["model"] if _trace_of(chain)["model"] != "-" else model}
            return _completion(answer, model, completion_id)

        async def stream_body():
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def emit(kind: str, data: Any) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

            def work() -> None:
                try:
                    answer = _call_chain(
                        chain, messages, tools, on_token=lambda text: emit("token", text)
                    )
                    app.state.last_trace = _trace_of(chain)
                    emit("done", answer)
                except ProviderError as exc:
                    emit("error", str(exc))
                except Exception as exc:  # noqa: BLE001 - never leak a traceback to a client
                    emit("error", f"internal error ({type(exc).__name__})")

            threading.Thread(target=work, daemon=True, name="uh-router-serve").start()
            yield _chunk("", model, completion_id)  # role marker, like OpenAI
            while True:
                kind, data = await queue.get()
                if kind == "error":
                    # explicit error event, then the end: never a silent truncation
                    payload_err = {"error": {"message": data, "type": "upstream_error", "code": "stream_error"}}
                    yield f"data: {json.dumps(payload_err)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if kind == "done":
                    yield _chunk("", model, completion_id, done=True)
                    yield "data: [DONE]\n\n"
                    return
                yield _chunk(str(data), model, completion_id)

        return StreamingResponse(
            stream_body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> int:
    """Start uvicorn. Refuses any non-loopback address, without an override."""
    if host not in LOOPBACK_NAMES:
        print(
            f"uh router serve: refusing to bind {host}. Loopback only "
            "(127.0.0.1, localhost, ::1) - use an SSH tunnel if you need it elsewhere.",
        )
        return 1
    import uvicorn

    if os.environ.get("UH_HOME"):
        print(f"  home: {uh_config.uh_home()}")
    app = create_router_app(token=ensure_token(), port=port, host=host)
    print(f"uh router serve - http://{host}:{port}/v1")
    print(f"  token: {token_path()} (copy it into the client, never into a URL)")
    print("  providers: local first, then the router (consent rules apply)")
    # access_log off: our own middleware logs the path without any query string
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
    return 0
