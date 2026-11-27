"""The machine panel's only door to UHB.

UH displays, UHB decides. This module does not know a single number about the
machine: it asks UHB for the profile and the recommendation, and it asks UHB to
start a download — with the operator's explicit consent, which is refused here
*before* anything is sent, so a client bug cannot become a download.

`resolve_url()` already refuses a non-loopback host, so the panel can never turn
UH into a proxy for a remote service.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .toolsets.uhb import resolve_url

#: Where UHB publishes the three answers the panel needs.
SCAN_PATH = "/machine/scan"
PULL_PATH = "/models/pull"
PULL_STATUS_PATH = "/models/pull/{run_id}"

#: A scan reads the machine and the catalogue: it is quick, but not instant.
SCAN_TIMEOUT = 20.0
#: Starting a download only authorises it; the transfer runs inside UHB.
PULL_TIMEOUT = 20.0


class MachineUnavailable(RuntimeError):
    """UHB is not reachable, or is not configured. Carries what to do about it."""

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


class MachineRefused(RuntimeError):
    """The request itself is not allowed: no consent, unknown model."""

    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


def _base_url() -> str:
    url = resolve_url()
    if not url:
        raise MachineUnavailable(
            "UHB n'est pas configuré : le panneau Machine lit le profil et le "
            "catalogue par son API (UH_UHB_URL, loopback seulement)"
        )
    return url


def _post(
    path: str,
    payload: dict[str, Any],
    client: Optional[httpx.Client] = None,
    timeout: float = SCAN_TIMEOUT,
) -> dict[str, Any]:
    url = _base_url()
    own = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(f"{url}{path}", json=payload)
    except httpx.HTTPError as exc:
        raise MachineUnavailable(f"UHB injoignable sur {url} : {exc}") from exc
    finally:
        if own:
            http.close()
    if response.status_code >= 400:
        detail = _detail(response)
        # 403/404/409 come from UHB's own gate: repeat its sentence, not ours.
        status = response.status_code
        if status in (403, 404, 409):
            raise MachineRefused(detail, status=status)
        raise MachineUnavailable(detail, status=502)
    try:
        payload_out = response.json()
    except ValueError as exc:
        raise MachineUnavailable("UHB a répondu autre chose que du JSON") from exc
    if not isinstance(payload_out, dict):
        raise MachineUnavailable("UHB a répondu un JSON inattendu")
    return payload_out


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"UHB a répondu {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("detail"):
            return str(body["detail"])
    return f"UHB a répondu {response.status_code}"


def scan(category: str = "chat", client: Optional[httpx.Client] = None) -> dict[str, Any]:
    """Profile + catalogue + recommendation, as UHB computed them."""
    return _post(SCAN_PATH, {"category": category}, client=client)


def start_pull(
    entry_id: str, accept: bool, client: Optional[httpx.Client] = None
) -> dict[str, Any]:
    """Ask UHB to download one model. Without ``accept`` nothing is even sent."""
    if accept is not True:
        raise MachineRefused(
            "téléchargement refusé : il faut cliquer sur « Télécharger » "
            "(accord explicite), aucun octet n'a été demandé"
        )
    if not entry_id:
        raise MachineRefused("entrée de catalogue manquante", status=400)
    return _post(
        PULL_PATH, {"entry_id": entry_id, "accept": True}, client=client, timeout=PULL_TIMEOUT
    )


def pull_status(run_id: str, client: Optional[httpx.Client] = None) -> dict[str, Any]:
    """Where UHB says that download stands."""
    url = _base_url()
    own = client is None
    http = client or httpx.Client(timeout=PULL_TIMEOUT)
    try:
        response = http.get(f"{url}{PULL_STATUS_PATH.format(run_id=run_id)}")
    except httpx.HTTPError as exc:
        raise MachineUnavailable(f"UHB injoignable sur {url} : {exc}") from exc
    finally:
        if own:
            http.close()
    if response.status_code >= 400:
        raise MachineRefused(_detail(response), status=response.status_code)
    payload = response.json()
    if not isinstance(payload, dict):
        raise MachineUnavailable("UHB a répondu un JSON inattendu")
    return payload
