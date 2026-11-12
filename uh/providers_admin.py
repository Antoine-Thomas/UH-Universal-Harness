"""Provider and key administration, shared by the CLI, the console and the web UI.

Jalon 7b.1: one implementation, three front-ends. The URL rules, the key limits,
the error kinds and the privacy switch live here; ``uh/web/providers_api.py``
only translates them into HTTP, and the console/CLI commands call the same
functions. A rule changed here changes everywhere, which is the point.

Invariants (also listed in ``docs/AUDIT_BRIEF.md``):

* no function in this module returns a **full** key: callers get counts, and
  :func:`keys_panel` returns at most a masked hint (3 leading + 4 trailing
  characters) for identification - never a value the holder could replay;
* a key is never taken from a command-line argument, only from a masked prompt,
  an environment variable or stdin;
* the loopback restriction on *writing* a key belongs to the web layer, which
  knows its bind address; this module exposes :func:`is_loopback_bind` so the two
  cannot drift.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from . import config as uh_config
from .keys import add_key, add_provider, read_providers, remove_key_at
from .router.catalog import CatalogError, load_catalog
from .router.client import classify_status
from .router.policy import CONSENT_WARNING, has_consent, record_consent

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PROBE_TIMEOUT = 10.0
MAX_KEY_LENGTH = 512
STRATEGIES = ("priority", "round-robin")

# The three priority sections of the API-key panel, in fallback order.
CLOUDFLARE_ACCOUNT_RE = re.compile(r"^[0-9a-fA-F]{32}$")
CLOUDFLARE_BASE_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
HINT_HEAD, HINT_TAIL, HINT_MIN = 3, 4, 12

# Popular OpenAI-compatible providers, as served to the web panel (a preset
# only pre-fills the custom-provider form: the same create_custom() path stores
# it, so there is no second way to write keys.toml). Base URLs and model ids
# are the vendors' documented OpenAI-compatible surfaces; a model id is only a
# default, the field stays editable.
PRESETS: tuple[dict[str, str], ...] = (
    {
        "name": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    {
        "name": "mistral",
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
    },
    {
        "name": "kimi",
        "label": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k2",
    },
    {
        "name": "gemini",
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.0-flash",
    },
    {
        "name": "groq",
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
    },
    {
        "name": "together",
        "label": "Together",
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
)

# Vendors that do NOT speak the OpenAI wire format. They get an explanation
# instead of a preset: a prefilled form would send the user into a 404 on
# /v1/chat/completions and look like a UH bug.
GATEWAY_ONLY: tuple[dict[str, str], ...] = (
    {
        "name": "anthropic",
        "label": "Anthropic Claude",
        "note": (
            "Claude requires a gateway (LiteLLM, OpenRouter, or OmniRoute) - it is "
            "not natively OpenAI-compatible. Enter the gateway URL below in the "
            "custom-provider form."
        ),
    },
)


class AdminError(RuntimeError):
    """A refusal a front-end can translate without guessing.

    ``kind`` is one of ``invalid`` (400), ``unknown`` (404) and ``forbidden``
    (403). The message is shown to a user: it never contains a key.
    """

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def is_loopback_bind(host: str) -> bool:
    raw = (host or "").strip().lower()
    if raw in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(raw.strip("[]")).is_loopback
    except ValueError:
        return False


def validate_base_url(raw: str) -> str:
    """Normalised base URL, or :class:`AdminError`. Never echoes credentials."""
    value = (raw or "").strip()
    if not value:
        raise AdminError("base_url is required")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise AdminError("base_url must use http or https")
    if parts.username or parts.password:
        raise AdminError("base_url must not embed credentials")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise AdminError("base_url has no host")
    if parts.query or parts.fragment:
        raise AdminError("base_url must not carry a query or a fragment")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    # Link-local first: 169.254.169.254 is the cloud metadata endpoint, and the
    # refusal must say so even when the scheme would already be refused.
    if address is not None and address in LINK_LOCAL:
        raise AdminError("link-local addresses are refused (cloud metadata endpoint)")

    if not is_loopback_bind(host) and parts.scheme != "https":
        raise AdminError("base_url must use https (http is allowed on loopback only)")
    return value.rstrip("/")


def validate_key(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise AdminError("key is required")
    value = raw.strip()
    if len(value) > MAX_KEY_LENGTH:
        raise AdminError(f"key is too long (max {MAX_KEY_LENGTH} characters)")
    if any(ord(character) < 0x20 for character in value):
        raise AdminError("key contains control characters")
    return value


def validate_name(raw: str) -> str:
    name = (raw or "").strip().lower()
    if not NAME_RE.match(name):
        raise AdminError(
            "name must be lowercase letters, digits, '-' or '_' (max 32 characters)"
        )
    return name


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
def catalog() -> dict[str, Any]:
    try:
        return load_catalog()
    except CatalogError:
        return {}


def _in_chain() -> set[str]:
    from .router.provider import RouterProvider

    try:
        router = RouterProvider(cfg=uh_config.load())
    except Exception:  # noqa: BLE001 - the panel must render even when the router cannot
        return set()
    return set(router.pool.states)


def provider_rows() -> list[dict[str, Any]]:
    """Every known provider with a **count** of keys - never a value."""
    specs = catalog()
    configured = read_providers()
    active = _in_chain()

    names = list(specs) + sorted(name for name in configured if name not in specs)
    rows: list[dict[str, Any]] = []
    for name in names:
        spec = specs.get(name)
        entry = configured.get(name, {})
        keys = [item for item in (entry.get("keys") or []) if item]
        first_model = spec.models[0].id if spec and spec.models else ""
        rows.append(
            {
                "name": name,
                "base_url": entry.get("base_url") or (spec.base_url if spec else ""),
                "model": entry.get("model") or first_model,
                "keys": len(keys),
                "verified": spec.verified if spec else "custom",
                "custom": spec is None,
                "in_chain": name in active,
            }
        )
    return rows


def key_hint(raw: Any) -> str:
    """Display-only mask: 3 leading + 4 trailing characters, or fully hidden.

    Below ``HINT_MIN`` characters the value stays fully masked: four characters
    out of eight is half the secret, not a hint.
    """
    value = str(raw or "").strip()
    if len(value) < HINT_MIN:
        return "••••"
    return f"{value[:HINT_HEAD]}…{value[-HINT_TAIL:]}"


def _stored_keys(name: str, configured: dict[str, Any]) -> list[str]:
    return [item for item in ((configured.get(name) or {}).get("keys") or []) if item]


def presets_panel() -> dict[str, Any]:
    """Popular-provider presets, plus the vendors that need a gateway.

    A preset is only a form default: nothing is written to ``keys.toml`` until
    the user submits it through ``create_custom``, so the panel keeps exactly
    one write path and a preset can never look like a configured provider.
    """
    return {
        "presets": [dict(entry) for entry in PRESETS],
        "gateway_only": [dict(entry) for entry in GATEWAY_ONLY],
    }


def keys_panel() -> dict[str, Any]:
    """State of the three priority sections of the API-key panel.

    Never returns a full key: a section carries ``configured``, the ``source``
    of the credential, and at most a masked hint per stored key.
    """
    configured = read_providers()
    cfg = uh_config.load()

    account = os.environ.get("UH_CLOUDFLARE_ACCOUNT_ID", "").strip() or str(
        cfg.get("cloudflare_account_id") or ""
    ).strip()
    env_token = os.environ.get("UH_CLOUDFLARE_API_TOKEN", "").strip()
    stored_token = _stored_keys("cloudflare", configured)
    if env_token:
        # UH never wrote this one: report its presence, never a fragment of it.
        cloudflare = {
            "configured": True,
            "source": "environment",
            "account_id": account,
            "key_hints": [],
        }
    elif stored_token:
        cloudflare = {
            "configured": True,
            "source": "keys.toml",
            "account_id": account,
            "key_hints": [key_hint(item) for item in stored_token],
        }
    else:
        cloudflare = {
            "configured": False,
            "source": "none",
            "account_id": account,
            "key_hints": [],
        }

    deepseek_keys = _stored_keys("deepseek", configured)
    deepseek = {
        "configured": bool(deepseek_keys),
        "key_hints": [key_hint(item) for item in deepseek_keys],
    }

    # Imported lazily: providers.omnirouter is a provider, not administration.
    from .providers.omnirouter import config_path, resolve_api_key, resolve_base_url

    path = config_path()
    omniroute = {
        "detected": path is not None,
        "base_url": str(resolve_base_url() or "") if path else "",
        "has_key": bool(resolve_api_key()) if path else False,
        "config_path": str(path) if path else "",
    }
    return {"cloudflare": cloudflare, "deepseek": deepseek, "omniroute": omniroute}


def _require_known(name: str) -> tuple[Optional[Any], Optional[dict[str, Any]]]:
    specs = catalog()
    configured = read_providers()
    spec = specs.get(name)
    entry = configured.get(name)
    if spec is None and entry is None:
        raise AdminError(f"unknown provider '{name}'", kind="unknown")
    return spec, entry


def settings() -> dict[str, Any]:
    cfg = uh_config.load()
    return {
        "allow_remote": bool(cfg.get("allow_remote")),
        "consent": has_consent(cfg),
        "consent_date": str(cfg.get("remote_consent") or ""),
        "strategy": str(cfg.get("router_strategy") or "priority"),
        "extra_providers": str(cfg.get("router_extra_providers") or ""),
        "warning": CONSENT_WARNING,
    }


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
def add_key_to(name: str, raw_key: Any) -> int:
    """Store a key for an existing provider; return the new count."""
    clean = validate_name(name)
    key = validate_key(raw_key)
    specs = catalog()
    configured = read_providers()
    spec = specs.get(clean)
    entry = configured.get(clean)
    if spec is None and entry is None:
        raise AdminError(f"unknown provider '{clean}'", kind="unknown")
    try:
        if entry is not None:
            add_key(clean, key)
        else:
            model = spec.models[0].id if spec and spec.models else ""
            add_provider(clean, spec.base_url, model, key)  # type: ignore[union-attr]
    except (OSError, ValueError) as exc:
        raise AdminError(f"cannot store the key: {exc}") from exc
    return len(read_providers().get(clean, {}).get("keys") or [])


def remove_key_index(name: str, index: int) -> int:
    clean = validate_name(name)
    if not isinstance(index, int) or index < 1:
        raise AdminError("index must be a positive integer")
    if not remove_key_at(clean, index):
        raise AdminError(f"no key at index {index} for '{clean}'", kind="unknown")
    return len(read_providers().get(clean, {}).get("keys") or [])


def save_provider(
    name: str, base_url: str, model: str = "", raw_key: str = ""
) -> dict[str, Any]:
    """Store or update a provider; used by ``uh providers add`` and by the panel."""
    clean = validate_name(name)
    url = validate_base_url(base_url)
    key = validate_key(raw_key) if raw_key else ""
    try:
        add_provider(clean, url, (model or "").strip(), key)
    except (OSError, ValueError) as exc:
        raise AdminError(f"cannot store the provider: {exc}") from exc
    return {"name": clean, "base_url": url, "model": (model or "").strip()}


def create_custom(name: str, base_url: str, model: str = "", key: str = "") -> dict[str, Any]:
    clean = validate_name(name)
    if clean in catalog():
        raise AdminError(f"'{clean}' is a catalogue provider")
    return save_provider(clean, base_url, model, key)


def save_cloudflare(account_id: str, raw_token: Any) -> dict[str, Any]:
    """Store the account id in ``config.toml`` and the token in ``keys.toml``.

    Two files, two purposes: an account id is not a credential and belongs to
    the flat configuration; the token is one and follows the 0600 key store.
    """
    account = (account_id or "").strip()
    if not CLOUDFLARE_ACCOUNT_RE.match(account):
        raise AdminError(
            "account_id must be the 32 hexadecimal characters of the Cloudflare "
            "account id (dashboard: Workers & Pages, Account ID)"
        )
    token = validate_key(raw_token)
    from .providers.cloudflare import DEFAULT_MODEL as cloudflare_model

    try:
        add_provider(
            "cloudflare",
            CLOUDFLARE_BASE_URL.format(account_id=account),
            cloudflare_model,
            token,
        )
        uh_config.write_layer("user", "cloudflare_account_id", account)
    except (OSError, ValueError) as exc:
        raise AdminError(f"cannot store the credentials: {exc}") from exc
    return {"account_id": account, "key_hint": key_hint(token), "source": "keys.toml"}


def save_deepseek_key(raw_key: Any) -> dict[str, Any]:
    """Store a DeepSeek key; a later call appends it (key rotation)."""
    key = validate_key(raw_key)
    try:
        if "deepseek" in read_providers():
            add_key("deepseek", key)
        else:
            add_provider("deepseek", DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, key)
    except (OSError, ValueError) as exc:
        raise AdminError(f"cannot store the key: {exc}") from exc
    keys = _stored_keys("deepseek", read_providers())
    return {"keys": len(keys), "key_hints": [key_hint(item) for item in keys]}


def import_from_omnirouter() -> dict[str, Any]:
    """Copy the OmniRoute gateway configuration into ``keys.toml`` (0600).

    The imported entry is named ``omnirouter`` so the providers panel shows it;
    the built-in provider keeps working from the environment and the dotenv
    either way.
    """
    from .providers.omnirouter import DEFAULT_MODEL, config_path, resolve_api_key, resolve_base_url

    path = config_path()
    if path is None:
        raise AdminError(
            "no OmniRoute configuration detected (~/.omniroute/.env)", kind="unknown"
        )
    base_url = resolve_base_url() or ""
    key = resolve_api_key() or ""
    if not base_url or not key:
        raise AdminError("the OmniRoute configuration has no base URL or no API key")
    save_provider("omnirouter", base_url, DEFAULT_MODEL, key)
    return {
        "imported": True,
        "name": "omnirouter",
        "base_url": base_url,
        "key_hints": [key_hint(key)],
    }


def read_secret(prompt: str = "API key (not echoed): ", env_var: str = "UH_PROVIDER_KEY") -> str:
    """Read a key from the environment, from stdin when piped, or masked.

    Never from a command-line argument: ``argv`` is visible in the process list
    and kept in the shell history. Returns ``""`` when nothing is available.
    """
    import getpass
    import os
    import sys

    from_env = (os.environ.get(env_var) or "").strip()
    if from_env:
        return from_env
    stdin = sys.stdin
    if stdin is not None and not stdin.isatty():
        try:
            line = stdin.readline().strip()
        except (OSError, ValueError):  # closed or non-readable stdin (pytest, a pipe)
            line = ""
        if line:
            return line
    if stdin is not None and stdin.isatty():
        return getpass.getpass(prompt).strip()
    return ""


def update_settings(
    *, allow_remote: bool, consent: bool, strategy: str = ""
) -> dict[str, Any]:
    current = uh_config.load()
    if allow_remote and not has_consent(current) and not consent:
        raise AdminError(
            "enabling remote providers requires consent: true in the same request"
        )
    wanted = (strategy or "").strip().lower()
    if wanted and wanted not in STRATEGIES:
        raise AdminError("strategy must be priority or round-robin")

    uh_config.write_layer("user", "allow_remote", bool(allow_remote))
    if consent:
        record_consent()
    if wanted:
        uh_config.write_layer("user", "router_strategy", wanted)
    return settings()


def test_connection(
    name: str,
    *,
    transport: Optional[httpx.BaseTransport] = None,
    timeout: float = PROBE_TIMEOUT,
) -> dict[str, Any]:
    """One ``ping`` to the provider. No session content ever leaves this function."""
    clean = validate_name(name)
    spec, entry = _require_known(clean)

    base_url = (entry or {}).get("base_url") or (spec.base_url if spec else "")
    if not base_url:
        return {"ok": False, "status": None, "error_kind": "no_base_url"}
    model = (entry or {}).get("model") or (spec.models[0].id if spec and spec.models else "")
    keys = [item for item in ((entry or {}).get("keys") or []) if item]
    headers = {"Content-Type": "application/json"}
    if keys:
        headers["Authorization"] = f"Bearer {keys[0]}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, transport=transport) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                headers=headers,
            )
    except httpx.TimeoutException:
        return {"ok": False, "status": None, "error_kind": "timeout"}
    except httpx.TransportError:
        return {"ok": False, "status": None, "error_kind": "transport"}

    if 300 <= response.status_code < 400:
        # following it would send the key wherever the redirect points
        return {"ok": False, "status": response.status_code, "error_kind": "redirect_refused"}
    if response.status_code >= 400:
        return {
            "ok": False,
            "status": response.status_code,
            "error_kind": classify_status(response.status_code),
        }
    return {"ok": True, "status": response.status_code, "error_kind": None}
