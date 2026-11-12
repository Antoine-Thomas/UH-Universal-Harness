"""Free-provider router: catalogue, transport, pool and policy (no network)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest

from uh import keys as keys_mod
from uh.providers import ProviderError
from uh.router import catalog as catalog_mod
from uh.router import client as client_mod
from uh.router import policy
from uh.router.catalog import ModelSpec, ProviderSpec, load_catalog
from uh.router.client import RouterError, chat_completion, pick_model
from uh.router.pool import Pool
from uh.router.provider import RouterProvider

TODAY = "2026-11-12"


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #
def test_catalog_is_data_and_loads() -> None:
    catalog = load_catalog()
    assert {"openrouter", "cerebras", "nvidia"} <= set(catalog)
    for spec in catalog.values():
        assert spec.base_url.startswith("https://")
        assert spec.verified in catalog_mod.VERIFICATION_STATES
        assert spec.models or spec.discover


def test_catalog_rejects_bad_entries(tmp_path: Path) -> None:
    bad = tmp_path / "c.toml"
    bad.write_text('[providers.x]\nbase_url = "http://insecure"\nverified = "verified"\n', "utf-8")
    with pytest.raises(catalog_mod.CatalogError, match="https"):
        load_catalog(bad)

    bad.write_text('[providers.x]\nbase_url = "https://x"\nverified = "maybe"\n', "utf-8")
    with pytest.raises(catalog_mod.CatalogError, match="verified"):
        load_catalog(bad)

    bad.write_text('[providers.x]\nbase_url = "https://x"\nverified = "verified"\n', "utf-8")
    with pytest.raises(catalog_mod.CatalogError, match="neither models"):
        load_catalog(bad)


def test_tool_models_filter() -> None:
    spec = ProviderSpec(
        name="x",
        base_url="https://x",
        models=(ModelSpec("big", tools=True), ModelSpec("tiny", tools=False)),
    )
    assert [model.id for model in spec.tool_models] == ["big"]
    assert pick_model(spec, "", tools=True) == "big"
    assert pick_model(spec, "tiny", tools=False) == "tiny"


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def _sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return (body + "data: [DONE]\n\n").encode()


SPEC = ProviderSpec(name="groq", base_url="https://api.groq.test/v1", models=(ModelSpec("m"),))


def test_non_streaming_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": "bonjour"}}]})

    message = chat_completion(
        SPEC, "test-key", "m", [{"role": "user", "content": "hi"}], [], transport=httpx.MockTransport(handler)
    )
    assert message == {"role": "assistant", "content": "bonjour", "tool_calls": []}


@pytest.mark.parametrize(
    ("status", "kind", "retry_after"),
    [(429, "rate_limit", 2.0), (500, "server", None), (402, "payment", None), (401, "auth", None)],
)
def test_http_errors_are_classified(status: int, kind: str, retry_after: float | None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"retry-after": "2"} if status == 429 else {}
        return httpx.Response(status, json={"error": {"message": "nope"}}, headers=headers)

    with pytest.raises(RouterError) as excinfo:
        chat_completion(SPEC, "k", "m", [], [], transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == kind
    assert excinfo.value.status == status
    assert excinfo.value.retry_after == retry_after


def test_timeout_is_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(RouterError) as excinfo:
        chat_completion(SPEC, "k", "m", [], [], transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "timeout"


def test_streaming_collects_tokens_and_tool_calls() -> None:
    body = _sse(
        {"choices": [{"delta": {"content": "bon"}}]},
        {"choices": [{"delta": {"content": "jour"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_file"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"path"'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "a.txt"}'}}]}}]},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    seen: list[str] = []
    message = chat_completion(
        SPEC,
        "k",
        "m",
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "read_file"}}],
        on_token=seen.append,
        transport=httpx.MockTransport(handler),
    )
    assert "".join(seen) == "bonjour"
    assert message["content"] == "bonjour"
    assert message["tool_calls"][0]["function"] == {"name": "read_file", "arguments": '{"path": "a.txt"}'}


def test_mid_stream_error_is_flagged_as_emitted() -> None:
    body = _sse(
        {"choices": [{"delta": {"content": "debut"}}]},
        {"error": {"code": 429, "message": "rate limited"}, "choices": [{"delta": {}, "finish_reason": "error"}]},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    with pytest.raises(RouterError) as excinfo:
        chat_completion(SPEC, "k", "m", [], [], on_token=lambda piece: None,
                        transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "rate_limit"
    assert excinfo.value.emitted is True


# --------------------------------------------------------------------------- #
# pool
# --------------------------------------------------------------------------- #
def _clock(start: float = 1000.0):
    state = {"now": start}

    def tick(seconds: float = 0.0) -> float:
        state["now"] += seconds
        return state["now"]

    tick.now = lambda: state["now"]  # type: ignore[attr-defined]
    return tick


def test_priority_order_and_key_rotation() -> None:
    pool = Pool({"a": ["k1", "k2"], "b": ["k3"]}, order=["a", "b"], clock=_clock().now)
    picked = [(name, key.key) for name, key in pool.candidates()]
    assert picked == [("a", "k1"), ("a", "k2"), ("b", "k3")]


def test_round_robin_rotates_providers() -> None:
    pool = Pool({"a": ["k1"], "b": ["k2"]}, order=["a", "b"], strategy="round-robin", clock=_clock().now)
    first = [name for name, _ in pool.candidates()]
    second = [name for name, _ in pool.candidates()]
    assert first != second
    assert set(first) == set(second) == {"a", "b"}


def test_rate_limit_cools_the_key_down() -> None:
    clock = _clock()
    pool = Pool({"a": ["k1", "k2"]}, clock=clock.now)
    name, key = pool.candidates()[0]
    pool.record_failure(name, key, error="rate_limit", retry_after=30.0)
    assert [k.key for _, k in pool.candidates()] == ["k2"]  # k1 cooling
    clock(31.0)
    assert "k1" in [k.key for _, k in pool.candidates()]


def test_breaker_opens_then_recovers() -> None:
    clock = _clock()
    pool = Pool({"a": ["k1"]}, clock=clock.now, breaker_base=10.0, default_cooldown=1.0)
    name, key = pool.candidates()[0]
    for _ in range(3):
        pool.record_failure(name, key, error="server", retry_after=1.0)
    assert pool.candidates() == []  # breaker open
    assert pool.snapshot()[0]["available"] is False
    clock(11.0)
    assert pool.candidates() != []  # half-open probe
    pool.record_success(name, key)
    assert pool.snapshot()[0]["failures"] == 0


# --------------------------------------------------------------------------- #
# provider: policy, fallback, trace
# --------------------------------------------------------------------------- #
class Cfg:
    def __init__(self, **values: object) -> None:
        self.values = values

    def get(self, key: str) -> object:
        return self.values.get(key)


def _provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, handler, **kwargs):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    cfg = kwargs.pop("cfg", Cfg(allow_remote=True, remote_consent=TODAY))
    catalog = {
        "a": ProviderSpec(
            name="a", base_url="https://a.test/v1", verified="verified",
            models=(ModelSpec("ma", tools=True),),
        ),
        "b": ProviderSpec(
            name="b", base_url="https://b.test/v1", verified="verified",
            models=(ModelSpec("mb", tools=True),),
        ),
    }
    return RouterProvider(
        catalog=catalog,
        keys={"a": ["ka"], "b": ["kb"]},
        transport=httpx.MockTransport(handler),
        cfg=cfg,
        stdin=io.StringIO(),
        stream=io.StringIO(),
        **kwargs,
    )


def test_local_only_refuses_before_any_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    provider = _provider(tmp_path, monkeypatch, handler, local_only=True)
    with pytest.raises(ProviderError, match="local-only"):
        provider.chat([{"role": "user", "content": "hi"}], [])
    assert calls == []


def test_consent_required_off_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path, monkeypatch, lambda request: httpx.Response(200), cfg=Cfg(allow_remote=True))
    with pytest.raises(ProviderError, match="consent"):
        provider.chat([{"role": "user", "content": "hi"}], [])


def test_falls_back_and_traces_the_real_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "a.test" in str(request.url):
            return httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "5"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "depuis b"}}]})

    provider = _provider(tmp_path, monkeypatch, handler)
    message = provider.chat([{"role": "user", "content": "hi"}], [])
    assert message["content"] == "depuis b"
    assert provider.last_trace == {"provider": "b", "model": "mb", "strategy": "priority", "ok": True}
    rows = {row["provider"]: row for row in provider.status_rows()}
    assert rows["a"]["available"] is False  # cooled down after the 429
    assert rows["a"]["last_error"].startswith("rate_limit")


def test_everything_failing_reports_the_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "down"}})

    provider = _provider(tmp_path, monkeypatch, handler)
    with pytest.raises(ProviderError) as excinfo:
        provider.chat([{"role": "user", "content": "hi"}], [])
    assert "server" in str(excinfo.value)


def test_stream_break_after_output_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(
        {"choices": [{"delta": {"content": "debut"}}]},
        {"error": {"code": 500, "message": "boom"}, "choices": [{"delta": {}, "finish_reason": "error"}]},
    )
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider = _provider(tmp_path, monkeypatch, handler)
    with pytest.raises(ProviderError, match="mid-stream"):
        provider.chat([{"role": "user", "content": "hi"}], [], on_token=lambda piece: None)
    assert len(hits) == 1  # no silent retry that would duplicate text


def test_status_rows_never_contain_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path, monkeypatch, lambda request: httpx.Response(200))
    rendered = json.dumps(provider.status_rows())
    assert "ka" not in rendered.replace("oka", "")  # key value absent
    assert "kb" not in rendered


# --------------------------------------------------------------------------- #
# keys: several per provider, no leak
# --------------------------------------------------------------------------- #
def test_multiple_keys_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", "first-key")
    keys_mod.add_key("groq", "second-key")
    entry = keys_mod.get_provider("groq")
    assert entry is not None
    assert entry["keys"] == ["first-key", "second-key"]
    assert keys_mod.remove_key("groq", "first-key") is True
    assert keys_mod.get_provider("groq")["keys"] == ["second-key"]


def test_policy_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Cfg(allow_remote=False)
    assert policy.allow_remote(cfg) is False
    assert policy.allow_remote(cfg, env={"UH_ALLOW_REMOTE": "1"}) is True
    assert policy.allow_remote(cfg, env={"UH_ALLOW_REMOTE": "1"}, local_only=True) is False


# --------------------------------------------------------------------------- #
# OpenRouter: la capacite "tools" vient de la reponse reelle, pas d'une supposition
# --------------------------------------------------------------------------- #
def test_discovery_reads_supported_parameters() -> None:
    payload = {
        "data": [
            {"id": "vendor/avec-outils:free", "supported_parameters": ["temperature", "tools"]},
            {"id": "vendor/sans-outils:free", "supported_parameters": ["temperature"]},
            {"id": "vendor/sans-champ:free"},
            {"id": "vendor/payant", "supported_parameters": ["tools"]},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json=payload)

    spec = ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.test/api/v1",
        verified="verified",
        discover="/models",
        discover_suffix=":free",
    )
    found = client_mod.discover_models(spec, "k", transport=httpx.MockTransport(handler))
    by_id = {model.id: model.tools for model in found}
    assert by_id == {
        "vendor/avec-outils:free": True,
        "vendor/sans-outils:free": False,
        "vendor/sans-champ:free": False,  # absent field == no tools, never assumed
    }


def test_tools_filter_uses_discovered_capability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {"id": "vendor/sans-outils:free", "supported_parameters": ["temperature"]},
            {"id": "vendor/avec-outils:free", "supported_parameters": ["tools"]},
        ]
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=payload)
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    spec = ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.test/api/v1",
        verified="verified",
        discover="/models",
        discover_suffix=":free",
    )
    provider = RouterProvider(
        catalog={"openrouter": spec},
        keys={"openrouter": ["k"]},
        transport=httpx.MockTransport(handler),
        cfg=Cfg(allow_remote=True, remote_consent=TODAY),
        stdin=io.StringIO(),
        stream=io.StringIO(),
    )
    provider.chat([{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "f"}}])
    assert seen == ["vendor/avec-outils:free"]  # le modele sans outils n'est jamais choisi


def test_provider_without_tool_capable_model_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"data": [{"id": "vendor/sans-outils:free", "supported_parameters": []}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=payload)
        raise AssertionError("aucun appel de chat ne doit partir")

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    spec = ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.test/api/v1",
        verified="verified",
        discover="/models",
        discover_suffix=":free",
    )
    provider = RouterProvider(
        catalog={"openrouter": spec},
        keys={"openrouter": ["k"]},
        transport=httpx.MockTransport(handler),
        cfg=Cfg(allow_remote=True, remote_consent=TODAY),
        stdin=io.StringIO(),
        stream=io.StringIO(),
    )
    with pytest.raises(ProviderError, match="no tool-capable model"):
        provider.chat([{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "f"}}])


# --------------------------------------------------------------------------- #
# entrees "partial": etiquetees, hors chaine sauf opt-in explicite
# --------------------------------------------------------------------------- #
def _partial_provider(tmp_path, monkeypatch, handler, **cfg_values):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    catalog = {
        "sur": ProviderSpec(
            name="sur", base_url="https://sur.test/v1", verified="verified",
            models=(ModelSpec("m1", tools=True),),
        ),
        "douteux": ProviderSpec(
            name="douteux", base_url="https://douteux.test/v1", verified="partial",
            models=(ModelSpec("m2", tools=True),),
        ),
    }
    return RouterProvider(
        catalog=catalog,
        keys={"sur": ["k1"], "douteux": ["k2"]},
        transport=httpx.MockTransport(handler),
        cfg=Cfg(allow_remote=True, remote_consent=TODAY, **cfg_values),
        stdin=io.StringIO(),
        stream=io.StringIO(),
    )


def test_partial_provider_is_labelled_and_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        return httpx.Response(503, json={"error": {"message": "down"}})

    provider = _partial_provider(tmp_path, monkeypatch, handler)
    rows = {row["provider"]: row for row in provider.status_rows()}
    assert rows["sur"]["label"] == "verified" and rows["sur"]["selected"] is True
    assert rows["douteux"]["label"] == "UNVERIFIED (opt-in)"
    assert rows["douteux"]["selected"] is False

    with pytest.raises(ProviderError):
        provider.chat([{"role": "user", "content": "hi"}], [])
    assert "douteux.test" not in hits  # jamais contacte sans opt-in


def test_partial_provider_after_explicit_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "sur.test" in request.url.host:
            return httpx.Response(500, json={"error": {"message": "down"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "depuis douteux"}}]})

    provider = _partial_provider(tmp_path, monkeypatch, handler, router_extra_providers="douteux")
    message = provider.chat([{"role": "user", "content": "hi"}], [])
    assert message["content"] == "depuis douteux"
    assert provider.last_trace["provider"] == "douteux"
