"""Le serveur e2e ne doit jamais être bloqué par son propre journal.

``uh web`` écrit chaque requête sur stdout — le journal d'accès d'uvicorn. Le
harnais ne lisait ce tuyau que jusqu'à la ligne qui porte l'URL de démarrage,
puis le laissait se remplir. Quand il est plein, les threads du serveur se
bloquent dans ``logging.emit`` et le serveur **cesse de répondre à tout**, sans
message.

Mesuré le 24/11 sur ce blocage : trois threads dans ``logging.emit``, 5,8 Ko en
attente dans le tuyau, ``/healthz`` en timeout, et la réponse revenue en 0,70 s
dès qu'on vide le tuyau. C'est le rouge e2e du 23/11 : `test_p3_14_role`, au
second rechargement, n'obtenait plus la réponse du GET des messages — trois
chargements de page suffisent à remplir le tuyau.

Ce test est la contre-épreuve du fil de lecture : sans lui, la rafale bloque le
serveur et la requête suivante expire ; avec lui, tout répond.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import Server

pytestmark = pytest.mark.e2e

#: Le blocage mesuré est survenu avec 5,8 Ko en attente dans le tuyau : la rafale
#: doit dépasser franchement cette taille, sinon ce test ne prouverait rien.
PIPE_BYTES = 4096
BURST = 200


def test_a_burst_of_requests_never_blocks_the_server(make_server) -> None:
    server: Server = make_server("home")
    cookies = {f"uh_token_{server.port}": server.token()}

    for index in range(BURST):
        reply = httpx.get(server.base + "/api/commands", cookies=cookies, timeout=10.0)
        assert reply.status_code == 200, f"requête {index} : HTTP {reply.status_code}"

    written = sum(len(line) for line in server.lines)
    assert written > PIPE_BYTES, (
        f"le serveur n'a écrit que {written} octets : la rafale ne dépasse pas un "
        "la taille qui a bloqué le serveur (5,8 Ko mesurés), ce test ne prouverait rien"
    )

    # La requête qui, sans fil de lecture, n'arriverait jamais.
    reply = httpx.get(server.base + "/api/commands", cookies=cookies, timeout=10.0)
    assert reply.status_code == 200
