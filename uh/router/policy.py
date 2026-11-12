"""Privacy gate: reaching a remote provider is opt-in and consented.

Two switches, both defaulting to "no":

* ``allow_remote`` (config) / ``UH_ALLOW_REMOTE`` (env) / ``--local-only`` (CLI,
  which always wins) decide whether a remote call is allowed at all;
* the first remote call additionally needs a recorded consent
  (``remote_consent`` in the config), because free tiers often keep or train on
  what they receive. Off a TTY, the answer is "no" rather than a guess.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, TextIO

from .. import config as uh_config

ALLOW_REMOTE_KEY = "allow_remote"
CONSENT_KEY = "remote_consent"

CONSENT_WARNING = (
    "This prompt will leave your machine and be sent to a third-party service.\n"
    "  Free tiers routinely keep requests and may use them for training.\n"
    "  Never send secrets, personal data or client material through them.\n"
    "  Ollama stays first: a local model answers whenever one is available."
)

_FALSE_VALUES = {"", "0", "false", "no", "off"}


class RemoteRefused(RuntimeError):
    """A remote provider was requested but the policy says no."""


def _env_flag(env: Optional[Mapping[str, str]]) -> Optional[bool]:
    source = env if env is not None else os.environ
    raw = source.get("UH_ALLOW_REMOTE")
    if raw is None:
        return None
    return raw.strip().lower() not in _FALSE_VALUES


def allow_remote(
    cfg: Any,
    *,
    local_only: bool = False,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Config < environment < ``--local-only``."""
    if local_only:
        return False
    flag = _env_flag(env)
    if flag is not None:
        return flag
    return bool(cfg.get(ALLOW_REMOTE_KEY))


def has_consent(cfg: Any) -> bool:
    return bool(str(cfg.get(CONSENT_KEY) or "").strip())


def record_consent() -> None:
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    uh_config.write_layer("user", CONSENT_KEY, stamp)


def ensure_consent(
    cfg: Any,
    *,
    local_only: bool = False,
    env: Optional[Mapping[str, str]] = None,
    stdin: Optional[TextIO] = None,
    stream: Optional[TextIO] = None,
    input_fn: Callable[[str], str] = input,
) -> bool:
    """True when a remote call may proceed. Raises :class:`RemoteRefused`."""
    if not allow_remote(cfg, local_only=local_only, env=env):
        raise RemoteRefused(
            "remote providers are disabled (local-only). "
            "Enable them with: uh config set allow_remote true"
        )
    if has_consent(cfg):
        return True

    handle_in = stdin if stdin is not None else sys.stdin
    handle_out = stream if stream is not None else sys.stderr
    if not _is_tty(handle_in):
        raise RemoteRefused(
            "first remote call needs an explicit consent, and stdin is not a terminal. "
            "Run `uh providers free` in a terminal, or set remote_consent yourself."
        )

    print(CONSENT_WARNING, file=handle_out)
    answer = input_fn("Send to a remote provider from now on? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise RemoteRefused("remote providers refused by the user")
    record_consent()
    return True


def _is_tty(handle: Any) -> bool:
    try:
        return bool(handle.isatty())
    except (AttributeError, ValueError):
        return False
