"""Gestionnaire d'extensions (P3-16d / D-8) : trois familles, un état, un effet à chaud.

Ce que ces tests protègent :
- l'inventaire dit la vérité (rien d'inventé : une extension cassée porte sa raison) ;
- éteindre une extension la retire de ce que le modèle reçoit **au tour suivant**, sans
  redémarrer quoi que ce soit — c'est la promesse la plus facile à casser sans le voir ;
- une demande invalide est refusée avant tout effet (famille inconnue, nom inconnu, valeur
  qui n'est pas un booléen) ;
- une épreuve de MCP cassé explique « binaire introuvable », elle ne dit pas « erreur ».
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import extensions as uh_extensions  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-extensions"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def run(coro):
    return asyncio.run(coro)


class _Stub:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    uh_home = tmp_path / "uh-home"
    (uh_home / "skills").mkdir(parents=True)
    monkeypatch.setenv("UH_HOME", str(uh_home))
    monkeypatch.setenv("UH_UHB_URL", "")  # le second cerveau n'est pas là : c'est un cas à afficher
    return uh_home


@pytest.fixture()
def skill(home: Path) -> str:
    (home / "skills" / "veille.md").write_text(
        "# Veille\n\nRésume les flux RSS du matin en trois lignes.\n", encoding="utf-8"
    )
    return "veille"


@pytest.fixture()
def app(home: Path, tmp_path: Path):
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: _Stub(),
        kind="stub",
        model="stub:1b",
    )


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


# --------------------------------------------------------------------------- #
# l'inventaire
# --------------------------------------------------------------------------- #
def test_the_inventory_covers_the_three_families(home: Path, skill: str) -> None:
    data = uh_extensions.inventory()
    assert set(data["groups"]) == {"mcp", "skills", "uhb"}
    assert data["kinds"] == ["mcp", "skills", "uhb"]
    names = [entry["name"] for entry in data["groups"]["skills"]]
    assert skill in names
    assert all("reason" in entry and "enabled" in entry for entry in data["groups"]["skills"])
    counts = data["counts"]["skills"]
    assert counts["total"] >= 1 and counts["enabled"] == counts["total"]


def test_a_malformed_mcp_entry_is_listed_broken_with_its_reason(home: Path) -> None:
    """Une entrée MCP mal écrite s'affiche cassée, elle ne fait pas tomber l'inventaire.

    `mcp.status()` publie ses refus sous forme de phrases. Les lire comme des dicts
    levait un AttributeError : le panneau Extensions renvoyait une erreur au lieu de
    nommer le refus, et c'est exactement le chemin que D-8 demande de prouver.
    """
    (home / "config.toml").write_text(
        '# `command` manque volontairement.\n[[mcp.servers]]\nname = "casse"\nargs = ["-y"]\n',
        encoding="utf-8",
        newline="\n",
    )

    data = uh_extensions.inventory()

    casse = [entry for entry in data["groups"]["mcp"] if entry["name"] == "casse"]
    assert len(casse) == 1, data["groups"]["mcp"]
    assert casse[0]["state"] == "broken" and casse[0]["enabled"] is False
    assert "'command' is required" in casse[0]["reason"], casse[0]["reason"]
    assert "refusée" in casse[0]["detail"]
    assert data["counts"]["mcp"]["broken"] == 1


def test_the_uhb_catalogue_lists_its_tools(home: Path) -> None:
    tools = [entry["name"] for entry in uh_extensions.inventory()["groups"]["uhb"]]
    assert tools, "le catalogue UHB n'est pas vide"
    assert all(
        entry["state"] == "unreachable" for entry in uh_extensions.inventory()["groups"]["uhb"]
    )


# --------------------------------------------------------------------------- #
# l'état
# --------------------------------------------------------------------------- #
def test_a_corrupt_registry_means_everything_is_on(home: Path, skill: str) -> None:
    (home / uh_extensions.FILE_NAME).write_text("{ pas du json", encoding="utf-8")
    assert uh_extensions.disabled("skills") == set()
    assert uh_extensions.is_enabled("skills", skill) is True


def test_disabling_writes_the_registry_atomically(home: Path) -> None:
    uh_extensions.set_enabled("uhb", "uhb_status", False)
    registry = json.loads((home / uh_extensions.FILE_NAME).read_text(encoding="utf-8"))
    assert registry["disabled"]["uhb"] == ["uhb_status"]
    assert not list(home.glob(".extensions-*.tmp")), "aucun fichier temporaire ne reste"


def test_turning_it_back_on_removes_the_entry(home: Path) -> None:
    uh_extensions.set_enabled("uhb", "uhb_status", False)
    uh_extensions.set_enabled("uhb", "uhb_status", True)
    assert uh_extensions.disabled("uhb") == set()


def test_a_skill_preference_goes_through_skill_prefs(home: Path, skill: str) -> None:
    """Un seul registre par décision : les skills restent dans `skill_prefs.json`."""
    uh_extensions.set_enabled("skills", skill, False)
    assert uh_extensions.is_enabled("skills", skill) is False
    assert (home / "skill_prefs.json").is_file()
    assert not (home / uh_extensions.FILE_NAME).exists()
    listed = {entry["name"]: entry for entry in uh_extensions.inventory()["groups"]["skills"]}
    assert listed[skill]["enabled"] is False


def test_an_unknown_extension_is_refused(home: Path) -> None:
    with pytest.raises(uh_extensions.ExtensionsError):
        uh_extensions.set_enabled("uhb", "pas_un_outil", False)


def test_an_unknown_family_is_refused(home: Path) -> None:
    with pytest.raises(uh_extensions.ExtensionsError):
        uh_extensions.set_enabled("plugins", "x", False)
    with pytest.raises(uh_extensions.ExtensionsError):
        uh_extensions.disabled("plugins")


# --------------------------------------------------------------------------- #
# l'effet à chaud (aucun redémarrage)
# --------------------------------------------------------------------------- #
class _FakeServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.command = "python"
        self.args: list[str] = []

    def key(self) -> str:
        return f"stdio:{self.name}"


class _FakeToolset:
    def __init__(self, client) -> None:
        self.tools = [{"function": {"name": "echo", "description": "répond"}}]


def test_a_disabled_mcp_server_leaves_the_tool_list_without_a_restart(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uh import mcp
    from uh.toolsets import build_toolsets

    monkeypatch.setattr(
        mcp,
        "servers_from_config",
        lambda config=None: ([_FakeServer("alpha"), _FakeServer("beta")], []),
    )
    started: list[str] = []
    monkeypatch.setattr(mcp, "client_for", lambda server: started.append(server.name) or object())
    monkeypatch.setattr(mcp, "Toolset", _FakeToolset)

    first = build_toolsets()
    assert set(first) >= {"mcp:alpha", "mcp:beta"}
    assert started == ["alpha", "beta"]

    uh_extensions.set_enabled("mcp", "alpha", False)

    started.clear()
    second = build_toolsets()
    assert "mcp:alpha" not in second, "un serveur éteint ne doit plus être proposé"
    assert "mcp:beta" in second
    assert "alpha" not in started, "un serveur éteint ne doit même pas être démarré"


def test_a_disabled_uhb_tool_is_not_offered(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uh import toolsets as toolsets_module

    class _FakeUhb:
        def __init__(self) -> None:
            self.tools = [
                {"function": {"name": "uhb_status", "description": "état"}},
                {"function": {"name": "uhb_route", "description": "exécute"}},
            ]

    monkeypatch.setattr(
        toolsets_module.Uhbtoolset,
        "from_environment",
        classmethod(lambda cls, **kwargs: _FakeUhb()),
    )
    tools = {spec["function"]["name"] for spec in toolsets_module.build_toolsets()["uhb"].tools}
    assert tools == {"uhb_status", "uhb_route"}

    uh_extensions.set_enabled("uhb", "uhb_route", False)
    tools = {spec["function"]["name"] for spec in toolsets_module.build_toolsets()["uhb"].tools}
    assert tools == {"uhb_status"}

    uh_extensions.set_enabled("uhb", "uhb_status", False)
    assert "uhb" not in toolsets_module.build_toolsets(), "tous éteints : le toolset disparaît"


# --------------------------------------------------------------------------- #
# l'épreuve
# --------------------------------------------------------------------------- #
def test_testing_a_skill_says_what_it_found(home: Path, skill: str) -> None:
    report = uh_extensions.test("skills", skill)
    assert report["ok"] is True
    assert "Veille" in report["detail"] or "caractères" in report["detail"]


def test_testing_an_empty_skill_is_a_refusal_with_a_reason(home: Path) -> None:
    (home / "skills" / "vide.md").write_text("# Vide\n", encoding="utf-8")
    report = uh_extensions.test("skills", "vide")
    assert report["ok"] is False
    assert "instruction" in report["detail"]


def test_testing_the_second_brain_without_a_second_brain_says_so(home: Path) -> None:
    tool = uh_extensions.inventory()["groups"]["uhb"][0]["name"]
    report = uh_extensions.test("uhb", tool)
    assert report["ok"] is False
    assert "configuré" in report["detail"] or "répond" in report["detail"]


def test_a_missing_binary_is_named_as_such() -> None:
    import uh.mcp as mcp

    explained = uh_extensions._explain("FileNotFoundError", "[WinError 2] introuvable")
    assert "binaire introuvable" in explained
    assert "binaire introuvable" in uh_extensions._explain(
        "MCPError", "no such file or directory: 'npx'"
    )
    assert "handshake" in uh_extensions._explain("TimeoutError", "the read operation timed out")
    assert mcp is not None


# --------------------------------------------------------------------------- #
# la route
# --------------------------------------------------------------------------- #
def test_the_route_requires_the_token(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/extensions")

    assert run(go()).status_code == 401


def test_the_listing_gives_the_three_groups(app, skill: str) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/extensions", headers=AUTH)

    body = run(go()).json()
    assert body["ok"] is True
    assert set(body["groups"]) == {"mcp", "skills", "uhb"}
    assert body["counts"]["skills"]["total"] >= 1


def test_the_toggle_route_refuses_a_value_that_is_not_a_boolean(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.patch(
                "/api/extensions/skills/x/enabled", json={"enabled": "oui"}, headers=AUTH
            )

    assert run(go()).status_code == 400


def test_the_toggle_route_refuses_an_unknown_name(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.patch(
                "/api/extensions/skills/fantome/enabled", json={"enabled": False}, headers=AUTH
            )

    response = run(go())
    assert response.status_code == 404
    assert "inconnue" in response.json()["detail"]


def test_the_toggle_route_turns_a_skill_off_and_says_it_is_hot(app, skill: str) -> None:
    async def go():
        async with client(app) as http:
            toggled = await http.patch(
                f"/api/extensions/skills/{skill}/enabled", json={"enabled": False}, headers=AUTH
            )
            listed = await http.get("/api/extensions", headers=AUTH)
            return toggled, listed

    toggled, listed = run(go())
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False
    assert toggled.json()["hot"] is True
    entries = {entry["name"]: entry for entry in listed.json()["groups"]["skills"]}
    assert entries[skill]["enabled"] is False


def test_the_test_route_runs_the_real_probe(app, skill: str) -> None:
    async def go():
        async with client(app) as http:
            return await http.post(f"/api/extensions/skills/{skill}/test", headers=AUTH)

    response = run(go())
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["detail"]
