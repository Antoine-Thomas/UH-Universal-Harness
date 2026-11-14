"""Hugging Face: TGI in front, the router behind - and never the legacy task API."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from uh.providers import PROVIDERS, ProviderError
from uh.providers import huggingface as hf


@pytest.fixture()
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (hf.ENV_API_KEY, *hf.VENDOR_ENV_KEYS, hf.ENV_BASE_URL):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UH_HOME", str(Path.cwd() / "no-such-home"))


def test_the_provider_is_registered() -> None:
    assert "huggingface" in PROVIDERS


def test_a_remote_provider_without_a_token_refuses_before_the_first_turn(
    no_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hf, "_reachable", lambda *_a, **_k: False)

    with pytest.raises(ProviderError) as caught:
        hf.HuggingFaceProvider()

    assert "HUGGINGFACE_API_KEY" in str(caught.value)
    assert "TGI" in str(caught.value), "name the way out that needs no account"


def test_a_local_tgi_needs_no_token(no_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of running locally: no account, no key."""
    monkeypatch.setattr(hf, "_reachable", lambda *_a, **_k: True)

    provider = hf.HuggingFaceProvider(model="local/model")

    assert provider.base_url == hf.BASE_URL_TGI
    # The contract is "builds without an account". A token already on this machine
    # is still sent - a TGI behind an auth proxy is a real deployment.
    assert provider.local is True


def test_the_default_remote_base_is_the_router_not_the_legacy_task_api(
    no_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hf, "_reachable", lambda *_a, **_k: False)
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf_test")

    provider = hf.HuggingFaceProvider()

    assert provider.base_url == "https://router.huggingface.co/v1"
    assert "api-inference.huggingface.co" not in provider.base_url, (
        "the legacy per-task API has no chat roles: a chat loop on it fails at turn one"
    )
    assert provider.chat_path == "/chat/completions"
    assert provider.api_key == "hf_test"


def test_the_environment_can_point_anywhere(no_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_HUGGINGFACE_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("HF_TOKEN", "hf_from_hf_token")

    provider = hf.HuggingFaceProvider()

    assert provider.base_url == "http://127.0.0.1:9999/v1"
    assert provider.api_key == "hf_from_hf_token"


def test_an_explicit_base_url_wins(no_env: None) -> None:
    provider = hf.HuggingFaceProvider(base_url="http://127.0.0.1:8080/v1", api_key="")

    assert provider.base_url == "http://127.0.0.1:8080/v1"


# --------------------------------------------------------------------------- #
# a real turn against a fake TGI: the transport, not the URL
# --------------------------------------------------------------------------- #
class _Tgi(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        payload = json.dumps(
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", ""),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "bonjour depuis TGI"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:  # silence the test output
        return


def test_a_turn_reaches_v1_chat_completions(no_env: None) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Tgi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        provider = hf.HuggingFaceProvider(model="tiny/model", base_url=base)

        answer = provider.chat([{"role": "user", "content": "bonjour"}], [])
    finally:
        server.shutdown()
        server.server_close()

    assert _Tgi.seen, "the provider never called the server"
    call = _Tgi.seen[-1]
    assert call["path"] == "/v1/chat/completions"
    assert call["body"]["model"] == "tiny/model"
    assert call["body"]["messages"][0]["content"] == "bonjour"
    assert "bonjour depuis TGI" in json.dumps(answer, ensure_ascii=False)
