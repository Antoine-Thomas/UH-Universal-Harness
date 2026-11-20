"""Isolation entre tours — version 1 : elle doit ROUGIR sur le code actuel.

Le defaut : `app.state.turn_workspace` / `turn_read_roots` sont ecrits par le fil d'un tour et
relus par `_toolsets`. Deux tours simultanes ecrivent la meme variable ; le second ecrase le
premier, qui recoit alors les racines de l'autre session - donc l'acces a ses pieces jointes.

La barriere force l'entrelacement : sans elle, le test serait sequentiel et passerait meme sur
le code fautif.
"""

from __future__ import annotations

import threading
import types
from pathlib import Path

from uh.toolsets import build_toolsets
from uh.web import app as web_app


def fake_app(tmp_path: Path) -> types.SimpleNamespace:
    (tmp_path / "workspace").mkdir(exist_ok=True)
    settings = types.SimpleNamespace(workspace=str(tmp_path / "workspace"), allow_terminal=False)
    return types.SimpleNamespace(state=types.SimpleNamespace(settings=settings))


def test_two_turns_never_borrow_each_others_context(tmp_path: Path) -> None:
    app = fake_app(tmp_path)
    session_a = tmp_path / "session-a"
    session_b = tmp_path / "session-b"
    for folder in (session_a, session_b):
        folder.mkdir()
        (folder / "note.txt").write_text(f"{folder.name}\n", encoding="utf-8")

    barrier = threading.Barrier(2, timeout=10)
    seen: dict[str, Path] = {}
    errors: dict[str, BaseException] = {}

    def turn(name: str, workspace: Path) -> None:
        try:
            # Leurre : si un tour lisait encore `app.state`, il prendrait CE contexte-la.
            app.state.turn_workspace = session_b
            app.state.turn_read_roots = [session_b]
            barrier.wait()  # les deux tours ont ecrit avant que l'un ne construise
            toolsets = web_app._toolsets(
                app,
                workspace=workspace,
                read_roots=[workspace],
                web_search=False,
                notify=None,
            )
            seen[name] = Path(toolsets["file"].workspace)
        except BaseException as exc:  # noqa: BLE001
            errors[name] = exc

    threads = [
        threading.Thread(target=turn, args=("a", session_a), daemon=True),
        threading.Thread(target=turn, args=("b", session_b), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors, f"un tour a echoue : {errors}"
    assert seen["a"] == session_a, f"A a recu le contexte de {seen['a'].name}"
    assert seen["b"] == session_b, f"B a recu le contexte de {seen['b'].name}"
    # Le leurre etait sur app.state : puisqu'il n'a pas ete suivi, l'application ne porte plus
    # le contexte d'un tour. C'est la conclusion du test, pas une assertion separee.


def test_the_file_tool_refuses_another_folder_and_every_traversal(tmp_path: Path) -> None:
    """Le confinement ne bouge pas : absolu, relatif et `..` restent refuses."""
    own = tmp_path / "session-a"
    other = tmp_path / "session-b"
    for folder in (own, other):
        folder.mkdir()
    (own / "mine.txt").write_text("a\n", encoding="utf-8")
    (other / "theirs.txt").write_text("b\n", encoding="utf-8")

    tool = build_toolsets(workspace=own, notify=None, read_roots=[own])["file"]

    assert "a" in str(tool.read_file(str(own / "mine.txt")))
    assert "refused" in str(tool.read_file(str(other / "theirs.txt")))
    assert "refused" in str(tool.read_file("../session-b/theirs.txt"))
    assert "refused" in str(tool.read_file(str(own / ".." / "session-b" / "theirs.txt")))


# ---------------------------------------------------------------------------
# Integration: the REAL turn path (sessions -> /chat -> turn thread -> _toolsets)
# ---------------------------------------------------------------------------

def test_two_real_turns_receive_their_own_upload_roots(tmp_path: Path, monkeypatch) -> None:
    """Deux tours concurrents par le vrai chemin, pas par un appel direct a `_toolsets`.

    Le point de decision est enveloppe, pas remplace : `_toolsets` d'origine construit le
    toolset, et la barriere force les deux tours a s'y trouver en meme temps. Chaque tour lit
    ensuite sa piece jointe (SHA-256 conforme) et se voit refuser celle de l'autre.
    """
    import asyncio
    import hashlib

    import httpx

    from uh import attachments

    # HOME et workspace DOIVENT differer : si le workspace est ~/.uh lui-meme, les
    # pieces jointes deviennent un sous-dossier du workspace et la lecture croisee
    # redevient permise par la regle du workspace. Cas reel, mesure ici.
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    calls: list[dict] = []
    barrier = threading.Barrier(2, timeout=20)
    original = web_app._toolsets

    def wrapped(app, **kwargs):  # noqa: ANN001 - signature de production conservee
        barrier.wait()  # les deux tours sont ici en meme temps
        toolsets = original(app, **kwargs)
        tool = toolsets["file"]
        calls.append(
            {
                "workspace": Path(tool.workspace),
                "roots": [Path(item) for item in tool.read_roots],
                "tool": tool,
            }
        )
        return toolsets

    monkeypatch.setattr(web_app, "_toolsets", wrapped)

    class Quiet:
        model = "stub:1b"

        def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            yield "ok"

        def chat(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return "ok"

    app = web_app.create_app(
        token="isolation-token",
        port=8765,
        host="127.0.0.1",
        workspace=project,
        provider_factory=lambda kind, model: Quiet(),
        kind="stub",
        model="stub:1b",
    )

    async def scenario() -> list[str]:
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": "Bearer isolation-token"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765", headers=headers, timeout=60
        ) as http:
            sessions = []
            for index in (1, 2):
                session = (await http.post("/api/sessions", json={"title": f"s{index}"})).json()
                sid = session["session"]["id"]
                folder = attachments.session_dir(sid)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "note.txt").write_text(f"contenu-{index}\n", encoding="utf-8")
                sessions.append(sid)

            async def one(sid: str) -> str:
                async with http.stream("POST", f"/api/sessions/{sid}/chat", json={"message": "x"}) as r:
                    return "".join([chunk async for chunk in r.aiter_text()])

            return await asyncio.gather(*(one(sid) for sid in sessions))

    asyncio.run(scenario())

    assert len(calls) == 2, f"les deux tours doivent construire leur toolset : {len(calls)}"
    roots = sorted(str(item[0].resolve()) for item in (call["roots"] for call in calls))
    assert len(set(roots)) == 2, f"chaque tour a ses propres racines : {roots}"

    seen: dict[str, str] = {}
    for call in calls:
        own = call["roots"][0]
        own_file = own / "note.txt"
        assert own_file.exists(), f"la piece jointe du tour est dans ses racines : {own}"
        digest = hashlib.sha256(own_file.read_bytes()).hexdigest()
        seen[str(own)] = digest

        own_text = own_file.read_text(encoding="utf-8").strip()
        result = str(call["tool"].read_file(str(own_file)))
        assert own_text in result, f"le tour doit lire sa piece jointe : {result[:80]}"
        for other in calls:
            if other is call:
                continue
            stranger = other["roots"][0] / "note.txt"
            cross = str(call["tool"].read_file(str(stranger))).lower()
            assert "refused" in cross or "outside" in cross or "error" in cross, cross
    assert len(set(seen.values())) == 2, f"les deux sessions ont des contenus distincts : {seen}"


def test_a_symlink_out_of_the_roots_is_refused(tmp_path: Path) -> None:
    """Un lien symbolique ne doit pas ouvrir une autre session."""
    own = tmp_path / "session-a"
    other = tmp_path / "session-b"
    own.mkdir()
    other.mkdir()
    (other / "secret.txt").write_text("secret\n", encoding="utf-8")
    link = own / "raccourci.txt"
    try:
        link.symlink_to(other / "secret.txt")
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("creation de lien symbolique non autorisee sur ce poste")

    tool = build_toolsets(workspace=own, notify=None, read_roots=[own])["file"]
    out = str(tool.read_file(str(link)))
    # On verifie le REFUS : le chemin cite dans le message contient « secret.txt ».
    assert "error" in out.lower() or "refused" in out.lower(), out
    assert "error" in out.lower() or "refused" in out.lower(), "le lien doit etre refuse"
    assert "outside" in out.lower() or "refused" in out.lower(), out


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    "home_rel,workspace_rel",
    [
        ("home", "project"),          # UH_HOME hors du workspace (cas normal)
        ("same", "same"),             # UH_HOME == workspace (le cas qui fuyait)
        ("home", "home/project"),     # UH_HOME parent du workspace
        ("home/project", "home"),     # workspace parent du UH_HOME
    ],
)
def test_the_uploads_folder_is_never_reachable_through_the_workspace(
    tmp_path: Path, monkeypatch, home_rel: str, workspace_rel: str
) -> None:
    """Quelle que soit l'imbrication, un upload n'est lisible que par la racine du tour.

    C'est le defaut mesure : quand le workspace contient ~/.uh, la regle « tout le workspace
    est lisible » autorisait la lecture des pieces jointes d'une autre session.
    """
    home = tmp_path / home_rel
    workspace = tmp_path / workspace_rel
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UH_HOME", str(home))

    mine = home / "uploads" / "session-a"
    other = home / "uploads" / "session-b"
    for folder in (mine, other):
        folder.mkdir(parents=True, exist_ok=True)
    (mine / "note.txt").write_text("a\n", encoding="utf-8")
    (other / "secret.txt").write_text("b\n", encoding="utf-8")

    tool = build_toolsets(workspace=workspace, notify=None, read_roots=[mine])["file"]

    assert "a" in str(tool.read_file(str(mine / "note.txt"))), "sa piece jointe est lisible"
    cross = str(tool.read_file(str(other / "secret.txt")))
    assert "refused" in cross, f"lecture croisee refusee ({home_rel} / {workspace_rel}) : {cross}"
    # On ne teste pas l'absence de la lettre « b » : le chemin cite dans le refus contient
    # « session-b ». On teste l'absence de CONTENU.
    assert "'content'" not in cross, "aucun contenu de l'autre session ne sort"
    write = str(tool.write_file(str(mine / "ajout.txt"), "x"))
    assert "refused" in write, "ecrire dans les uploads reste interdit"


def test_a_symlink_inside_the_workspace_cannot_reach_the_uploads(
    tmp_path: Path, monkeypatch
) -> None:
    """Le lien est resolu avant comparaison : il ne contourne pas l'exclusion."""
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    other = home / "uploads" / "session-b"
    other.mkdir(parents=True)
    (other / "secret.txt").write_text("b\n", encoding="utf-8")

    link = workspace / "raccourci"
    try:
        link.symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("creation de lien symbolique non autorisee sur ce poste")

    tool = build_toolsets(workspace=workspace, notify=None, read_roots=[])["file"]
    out = str(tool.read_file(str(link / "secret.txt")))
    assert "refused" in out, out


def test_a_failure_to_determine_the_uploads_root_refuses_to_build_a_toolset(
    tmp_path: Path, monkeypatch
) -> None:
    """Fail-closed : pas de racine protegee determinable => pas de toolset du tout.

    Un `except: return []` aurait rendu un outil sans protection, c'est-a-dire le defaut
    d'origine sans aucun signal. Ici l'echec remonte.
    """
    import pytest

    from uh.toolsets import build_toolsets as build
    from uh.toolsets import memory as memory_module

    def boom() -> Path:
        raise RuntimeError("UH_HOME illisible")

    monkeypatch.setattr(memory_module, "uh_home", boom)
    with pytest.raises(RuntimeError, match="UH_HOME illisible"):
        build(workspace=tmp_path)
