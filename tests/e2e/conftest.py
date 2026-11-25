"""End-to-end harness: a real ``uh web``, a real Chromium, a throwaway UH_HOME.

Nothing here touches ``~/.uh``: every server gets its own home under ``tmp_path``,
its own free port, and ``--no-uhb``. The tests stop at what the UI and the server
do between them - they never ask a model for an answer, so no key, no GPU and no
network are involved.

Playwright is a TEST dependency only; it is imported lazily so a checkout without
it still runs the rest of the suite.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
#: Which interpreter runs `uh web`, and from where. Default: this checkout's venv,
#: started inside the checkout (an editable install). A release check points them at
#: a fresh venv and a neutral folder, so the wheel - not the checkout - is under test:
#:   UH_E2E_PYTHON=/tmp/v/bin/python UH_E2E_CWD=/tmp pytest -m e2e
_CANDIDATES = [
    Path(os.environ["UH_E2E_PYTHON"]) if os.environ.get("UH_E2E_PYTHON") else None,
    ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
    ROOT / ".venv" / "bin" / "python",  # POSIX
]
PYTHON = next((path for path in _CANDIDATES if path and path.is_file()), Path(sys.executable))
CWD = Path(os.environ.get("UH_E2E_CWD") or ROOT)

pw_api = pytest.importorskip("playwright.sync_api", reason="e2e needs the test-only playwright")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Server:
    """One ``uh web`` process, started and stopped by the test."""

    def __init__(
        self,
        home: Path,
        port: int,
        workspace: Path | None = None,
        extra_env: dict[str, str] | None = None,
        seed: "Callable[[Path], None] | None" = None,
    ) -> None:
        self.home = home
        self.port = port
        #: Appelé juste après la création du `UH_HOME`, AVANT `uh web` : c'est le
        #: seul moment où l'on peut y déclarer un fournisseur, puisque la sélection
        #: se fait au démarrage et que `uh web` n'a pas d'argument `--provider`.
        self.seed = seed
        #: Le workspace du serveur, quand le test en veut un jetable (panneau Git).
        self.workspace = workspace
        #: Variables d'environnement du serveur (un UHB sur port fermé, des runs jetables).
        self.extra_env = dict(extra_env or {})
        self.url = ""
        self.lines: list[str] = []
        self.proc: subprocess.Popen | None = None
        #: Le fil qui vide le tuyau du serveur jusqu'à sa mort (voir ``_pump``).
        self._reader: threading.Thread | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "Server":
        self.home.mkdir(parents=True, exist_ok=True)
        if self.seed is not None:
            self.seed(self.home)
        env = dict(os.environ, UH_HOME=str(self.home), PYTHONUNBUFFERED="1", **self.extra_env)
        command = [
            str(PYTHON),
            "-m",
            "uh",
            "web",
            "--no-open",
            "--no-uhb",
            "--port",
            str(self.port),
        ]
        if self.workspace is not None:
            command += ["--workspace", str(self.workspace)]
        self.proc = subprocess.Popen(
            command,
            cwd=str(CWD),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert self.proc.stdout is not None
        # Vider le tuyau dès maintenant, et jusqu'à la mort du serveur : c'est le
        # seul moyen de ne pas le bloquer (voir `_pump`).
        self._reader = threading.Thread(target=self._pump, name="uh-e2e-server-output", daemon=True)
        self._reader.start()
        deadline = time.time() + 45
        scanned = 0  # index de la prochaine ligne que la boucle doit relire
        while time.time() < deadline:
            while scanned < len(self.lines):
                match = re.search(
                    rf"(http://127\.0\.0\.1:{self.port}/\?token=\S+)", self.lines[scanned]
                )
                scanned += 1
                if match:
                    self.url = match.group(1)
                    self.wait_ready()
                    return self
            time.sleep(0.05)
            # Un serveur mort avant son URL ne mérite pas 45 s d'attente.
            if self.proc.poll() is not None and scanned >= len(self.lines):
                break
        raise AssertionError("server never printed its URL:\n" + "".join(self.lines[-12:]))

    def _pump(self) -> None:
        """Lire la sortie du serveur jusqu'à EOF, en gardant les dernières lignes.

        ``start()`` ne lisait ce tuyau que jusqu'à la ligne qui porte l'URL, puis
        plus personne. Or ``uh web`` écrit chaque requête (journal d'accès
        d'uvicorn) sur ce tuyau : quand il est plein, les threads se bloquent dans
        ``logging.emit`` et le serveur cesse de répondre à **tout**.

        Mesuré le 24/11 sur le test qui échouait : trois threads bloqués dans
        ``logging.emit``, 5,8 Ko en attente dans le tuyau, ``/healthz`` (sans
        authentification) en timeout à 3,5 s, puis **0,70 s** dès qu'on vide le
        tuyau. C'était le rouge e2e du 23/11 — ``test_p3_14_role``, second
        rechargement : le GET des messages n'arrivait jamais, et le test attendait
        30 s une réponse que le serveur ne pouvait plus produire.
        """
        stream = self.proc.stdout if self.proc else None
        if stream is None:
            return
        try:
            for line in stream:
                self.lines.append(line)
                # Un serveur bavard ne doit pas faire grossir la mémoire du test.
                # Jamais avant l'URL : `start()` lit la liste par index, et couper
                # la tête décalerait cet index pendant la recherche.
                if self.url and len(self.lines) > 4000:
                    del self.lines[:2000]
        except (ValueError, OSError):  # tuyau fermé par `stop()`
            return

    def wait_ready(self, timeout: float = 30.0) -> "Server":
        """Block until uvicorn really answers, not until it prints its URL.

        The launcher prints ``UH web - http://…/?token=…`` *before* ``uvicorn.run``
        binds the port, so reading that line is not proof the server is up: a fast
        client (the e2e suite, a launcher timer) still gets ``connection refused``.
        ``/healthz`` is unauthenticated on purpose, so it is the right probe.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                httpx.get(self.base + "/healthz", timeout=2.0)
                return self
            except httpx.HTTPError:
                time.sleep(0.2)
        raise AssertionError("server never answered /healthz:\n" + "".join(self.lines[-12:]))

    def token(self) -> str:
        return self.url.split("token=", 1)[1]

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                self.proc.kill()
        # Le fil de lecture s'arrête de lui-même sur EOF ; on ferme et on l'attend
        # pour ne pas laisser un thread qui tient le tuyau d'un test à l'autre.
        try:
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        except OSError:  # pragma: no cover - already closed
            pass
        if self._reader is not None:
            self._reader.join(timeout=2)
            self._reader = None
        self.proc = None


@pytest.fixture()
def make_server(tmp_path: Path):
    started: list[Server] = []

    def factory(
        name: str = "home",
        port: int | None = None,
        workspace: Path | None = None,
        extra_env: dict[str, str] | None = None,
        seed = None,
    ) -> Server:
        server = Server(
            tmp_path / name, port or free_port(), workspace=workspace, extra_env=extra_env,
            seed=seed,
        )
        started.append(server)
        return server.start()

    yield factory
    for server in started:
        server.stop()


@pytest.fixture()
def server(make_server) -> Server:
    return make_server()


@pytest.fixture()
def browser():
    with pw_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


def open_app(browser, server: Server, *, timezone_id: str | None = None):
    """A page on the app, with its own cookie jar, waiting for the composer.

    ``timezone_id`` place le NAVIGATEUR dans un autre fuseau que le serveur. C'est la seule
    façon de distinguer « aujourd'hui » calculé sur l'horloge du serveur de « aujourd'hui »
    calculé sur celle du poste : sans cela, les deux calendriers coïncident et le défaut
    reste invisible.
    """
    context = browser.new_context(timezone_id=timezone_id) if timezone_id else browser.new_context()
    page = context.new_page()
    page.goto(server.url, wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=30000)
    # The shell wires itself after the first paint: give boot() a moment so the
    # composer is the wired one, not just the markup.
    page.wait_for_timeout(1500)
    return context, page
