"""Tâches planifiées (P3-16c lot 4) : liste blanche, cadence, déclencheurs, exécution.

Ce que ces tests protègent en priorité : **aucune chaîne venue du web n'atteint une ligne
de commande**. Une commande est une clé du catalogue, le nom n'est qu'une étiquette, et le
déclencheur Windows ne transporte que l'identifiant de la tâche.

Les déclencheurs sont neutralisés (`UH_SCHEDULE_DISABLE_SYSTEM`) ou redirigés vers un
dossier temporaire (`UH_SCHEDULE_STARTUP_DIR`) : aucun test n'écrit dans le vrai dossier
Démarrage de l'opérateur.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import schedule as uh_schedule  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-schedule"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def run(coro):
    return asyncio.run(coro)


class _Stub:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


@pytest.fixture(autouse=True)
def isolated_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Registre isolé, aucun déclencheur système, repli dans un dossier jetable."""
    monkeypatch.setenv("UH_SCHEDULE_DISABLE_SYSTEM", "1")
    startup = tmp_path / "startup"
    startup.mkdir()
    monkeypatch.setenv("UH_SCHEDULE_STARTUP_DIR", str(startup))
    return startup


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: _Stub(),
        kind="stub",
        model="stub:1b",
    )


# --------------------------------------------------------------------------- #
# la liste blanche
# --------------------------------------------------------------------------- #
def test_the_catalog_is_the_only_source_of_commands() -> None:
    keys = {entry["key"] for entry in uh_schedule.catalog()}
    assert keys == {"doctor", "dream", "runs-prune"}
    for entry in uh_schedule.catalog():
        assert entry["label"] and entry["description"]


def test_a_command_outside_the_catalog_is_refused() -> None:
    for hostile in ("rm -rf /", "powershell -c calc", "doctor; rm -rf /", "", "  "):
        with pytest.raises(uh_schedule.ScheduleError):
            uh_schedule.run_command(hostile)
    with pytest.raises(uh_schedule.ScheduleError):
        uh_schedule.create("nom", "shell", 60)


def test_the_trigger_carries_only_the_task_id() -> None:
    """La ligne de commande du déclencheur ne contient jamais la commande elle-même."""
    task = {"id": "sch-abc12345", "command": "doctor", "every_minutes": 60}
    line = uh_schedule.command_line(task)
    assert "sch-abc12345" in line
    assert "doctor" not in line
    assert "-m uh.cli schedule run" in line


def test_runs_prune_is_a_simulation_not_a_deletion() -> None:
    entry = next(item for item in uh_schedule.catalog() if item["key"] == "runs-prune")
    assert entry["mutating"] is False
    # Le module appelle UHB en `apply=False` : on le vérifie sur la source de l'action.
    assert "apply=False" not in uh_schedule._run_prune.__doc__.replace("apply=False", "")
    source = Path(uh_schedule.__file__).read_text(encoding="utf-8")
    assert "uhb_runs.prune(apply=False)" in source


# --------------------------------------------------------------------------- #
# cadence et création
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("every", [1, 4, 0, -10, 10081, "abc", None])
def test_a_cadence_outside_the_bounds_is_refused(every) -> None:
    with pytest.raises(uh_schedule.ScheduleError):
        uh_schedule.create("diagnostic", "doctor", every)


def test_a_task_without_a_name_is_refused() -> None:
    with pytest.raises(uh_schedule.ScheduleError):
        uh_schedule.create("   ", "doctor", 60)


def test_a_long_name_is_refused() -> None:
    with pytest.raises(uh_schedule.ScheduleError):
        uh_schedule.create("x" * 61, "doctor", 60)


def test_creating_a_task_writes_the_registry(isolated_schedule: Path) -> None:
    created = uh_schedule.create("Diagnostic du matin", "doctor", 60)
    task = created["task"]
    assert task["id"].startswith("sch-")
    assert task["enabled"] is True
    assert task["every_minutes"] == 60
    assert created["trigger"]["mechanism"] == "registre", "déclencheurs désactivés par l'environnement"
    registry = json.loads(uh_schedule.registry_path().read_text(encoding="utf-8"))
    assert len(registry["tasks"]) == 1
    assert registry["tasks"][0]["name"] == "Diagnostic du matin"


def test_the_same_name_replaces_the_previous_task(isolated_schedule: Path) -> None:
    first = uh_schedule.create("Veille", "doctor", 60)["task"]
    second = uh_schedule.create("Veille", "doctor", 120)["task"]
    assert first["id"] != second["id"]
    data = uh_schedule.listing()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["every_minutes"] == 120


# --------------------------------------------------------------------------- #
# le repli Windows
# --------------------------------------------------------------------------- #
def test_the_startup_fallback_writes_a_vbs(isolated_schedule: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_SCHEDULE_DISABLE_SYSTEM", raising=False)
    monkeypatch.setattr(uh_schedule, "_install_schtasks", lambda task: False)
    created = uh_schedule.create("Diagnostic", "doctor", 15)
    task = created["task"]
    assert created["trigger"]["mechanism"] == "startup"
    script = Path(created["trigger"]["path"])
    assert script.is_file() and script.parent == isolated_schedule
    body = script.read_text(encoding="utf-8")
    assert "WScript.Sleep" in body and "15 * 60 * 1000" in body
    assert "schedule run" in body and task["id"] in body
    assert "uh_schedule" not in body, "le script appelle la CLI, pas le module interne"


def test_disabling_removes_the_trigger_and_enabling_puts_it_back(
    isolated_schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UH_SCHEDULE_DISABLE_SYSTEM", raising=False)
    monkeypatch.setattr(uh_schedule, "_install_schtasks", lambda task: False)
    task = uh_schedule.create("Diagnostic", "doctor", 15)["task"]
    script = Path(task["trigger_path"])
    assert script.is_file()

    off = uh_schedule.toggle(task["id"])["task"]
    assert off["enabled"] is False
    assert not script.exists(), "un déclencheur désactivé ne reste pas sur le disque"

    on = uh_schedule.toggle(task["id"])["task"]
    assert on["enabled"] is True
    assert script.is_file()


def test_schtasks_is_tried_first_and_a_refusal_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_SCHEDULE_DISABLE_SYSTEM", raising=False)
    attempted: list[str] = []

    def fake_schtasks(task):
        attempted.append(task["id"])
        return False  # « Accès refusé » d'un compte non administrateur

    monkeypatch.setattr(uh_schedule, "_install_schtasks", fake_schtasks)
    created = uh_schedule.create("Diagnostic", "doctor", 30)
    assert attempted, "le Planificateur Windows doit être tenté d'abord"
    assert created["trigger"]["mechanism"] == "startup"


def test_deleting_a_task_removes_its_script(isolated_schedule: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_SCHEDULE_DISABLE_SYSTEM", raising=False)
    monkeypatch.setattr(uh_schedule, "_install_schtasks", lambda task: False)
    task = uh_schedule.create("Diagnostic", "doctor", 15)["task"]
    script = Path(task["trigger_path"])
    removed = uh_schedule.remove(task["id"])
    assert removed["removed"] == task["id"]
    assert not script.exists()
    assert uh_schedule.listing()["tasks"] == []


def test_we_never_delete_a_script_we_did_not_write(isolated_schedule: Path) -> None:
    stranger = isolated_schedule / "autre-chose.vbs"
    stranger.write_text("' pas à nous", encoding="utf-8")
    task = uh_schedule.create("Diagnostic", "doctor", 15)["task"]
    uh_schedule.remove(task["id"])
    assert stranger.is_file(), "seuls nos uh-schedule-*.vbs sont supprimés"


# --------------------------------------------------------------------------- #
# exécution
# --------------------------------------------------------------------------- #
def test_running_a_task_records_the_time_and_the_status() -> None:
    task = uh_schedule.create("Diagnostic", "doctor", 60)["task"]
    outcome = uh_schedule.run_now(task["id"])
    assert outcome["ok"] is True, outcome["detail"]
    stored = next(item for item in uh_schedule.listing()["tasks"] if item["id"] == task["id"])
    assert stored["last_run"], "l'horodatage du dernier passage est enregistré"
    assert stored["last_status"] == "ok"
    assert stored["last_detail"]


def test_the_cli_entry_point_runs_the_same_whitelist(isolated_schedule: Path) -> None:
    task = uh_schedule.create("Diagnostic", "doctor", 60)["task"]
    assert uh_schedule.run_by_id(task["id"]) == 0
    assert uh_schedule.run_by_id("sch-inconnu") == 2


# --------------------------------------------------------------------------- #
# la route
# --------------------------------------------------------------------------- #
def test_the_route_requires_the_token(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/schedules")

    assert run(go()).status_code == 401


def test_the_listing_exposes_the_catalog_and_the_mode(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.get("/api/schedules", headers=AUTH)

    body = run(go()).json()
    assert body["ok"] is True
    assert {entry["key"] for entry in body["catalog"]} == {"doctor", "dream", "runs-prune"}
    assert body["mechanism_label"]
    assert body["tasks"] == []


def test_a_hostile_command_is_refused_by_the_route(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.post(
                "/api/schedules",
                json={"name": "méchant", "command": "powershell -c calc", "every_minutes": 60},
                headers=AUTH,
            )

    response = run(go())
    assert response.status_code == 400
    assert "non autorisée" in response.json()["detail"]
    assert uh_schedule.listing()["tasks"] == [], "rien n'a été enregistré"


def test_a_bad_cadence_is_refused_by_the_route(app) -> None:
    async def go():
        async with client(app) as http:
            return await http.post(
                "/api/schedules",
                json={"name": "trop souvent", "command": "doctor", "every_minutes": 1},
                headers=AUTH,
            )

    response = run(go())
    assert response.status_code == 400
    assert "cadence" in response.json()["detail"]


def test_the_full_lifecycle_through_the_routes(app) -> None:
    async def go():
        async with client(app) as http:
            created = await http.post(
                "/api/schedules",
                json={"name": "Diagnostic du matin", "command": "doctor", "every_minutes": 120},
                headers=AUTH,
            )
            task_id = created.json()["task"]["id"]
            listed = await http.get("/api/schedules", headers=AUTH)
            ran = await http.post(f"/api/schedules/{task_id}/run", headers=AUTH)
            toggled = await http.post(f"/api/schedules/{task_id}/toggle", headers=AUTH)
            deleted = await http.delete(f"/api/schedules/{task_id}", headers=AUTH)
            after = await http.get("/api/schedules", headers=AUTH)
            missing = await http.delete(f"/api/schedules/{task_id}", headers=AUTH)
            return created, listed, ran, toggled, deleted, after, missing

    created, listed, ran, toggled, deleted, after, missing = run(go())
    assert created.status_code == 200, created.text
    assert len(listed.json()["tasks"]) == 1
    assert ran.status_code == 200 and ran.json()["ok"] is True
    assert ran.json()["task"]["last_status"] == "ok"
    assert toggled.json()["task"]["enabled"] is False
    assert deleted.status_code == 200 and deleted.json()["removed"]
    assert after.json()["tasks"] == []
    assert missing.status_code == 404
