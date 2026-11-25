"""Un faux fournisseur OpenAI, pour les tests de bout en bout.

Pourquoi un serveur HTTP et non un objet injecté : le harnais e2e lance
`uh web` en **sous-processus**. Un objet en mémoire ne traverserait pas la
frontière du processus ; une adresse loopback, si.

Trois garde-fous, et ils sont volontairement dans le code plutôt que dans une
promesse : l'écoute est limitée à 127.0.0.1, le port est choisi par le système
(port 0), et la clé attendue est factice. Le serveur ne connaît rien de UH : il
répond dans le format OpenAI et rien d'autre.

La réponse contient **deux URL**, et c'est tout l'intérêt pour G8 :

* une URL que la session aura vue dans un résultat d'outil (provenance `tool`) ;
* une URL écrite par le modèle seul (provenance `model`, aucun aperçu sans clic).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple

#: La clé que le provider factice reçoit dans le `UH_HOME` temporaire.
FAKE_KEY = "test-key-not-a-credential"

#: L'URL que le test fera figurer dans un résultat d'outil.
TOOL_URL = "https://outil.test/page"

#: L'URL que le modèle écrit de lui-même.
MODEL_URL = "https://modele.test/page"

TEXT = (
    f"Voici le résultat : {TOOL_URL} (rapporté par l'outil) "
    f"et une adresse que je propose moi-même : {MODEL_URL}."
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # noqa: D401 - silence, c'est un test
        """Aucun journal : le bruit d'un faux serveur noierait celui de UH."""

    def _json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - nom imposé par http.server
        if self.path.rstrip("/").endswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": "fake-1b", "object": "model"}]})
            return
        self._json({"error": {"message": "route inconnue"}}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        owner = getattr(type(self), "owner", None)
        if owner is not None:
            owner.hits.append((self.path, 200))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        if not str(self.path).rstrip("/").endswith("/v1/chat/completions"):
            self._json({"error": {"message": "route inconnue"}}, status=404)
            return
        if payload.get("stream"):
            self._stream()
        else:
            self._json(
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "model": "fake-1b",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": TEXT}}
                    ],
                }
            )

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in (TEXT[:40], TEXT[40:]):
            event = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": "fake-1b",
                "choices": [{"index": 0, "delta": {"content": chunk}}],
            }
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class FakeOpenAI:
    """Le serveur, sur une adresse loopback et un port choisi par le système."""

    def __init__(self) -> None:
        self.hits: List[Tuple[str, int]] = []
        handler = type("CountingHandler", (_Handler,), {"owner": None})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        handler.owner = self
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> "FakeOpenAI":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request_log(self) -> List[Tuple[str, int]]:
        """Les requêtes reçues, pour prouver qu'un tour est bien passé par ici."""
        return list(self.hits)
