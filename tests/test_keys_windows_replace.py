"""W1 — sous Windows, remplacer keys.toml echoue si un autre handle l'a ouvert.

Ce n'est pas une boucle de tirage au sort : sous Windows, `open()` en Python
n'accorde pas le partage de suppression, donc un fichier ouvert interdit
`os.replace` sur ce meme fichier, de facon deterministe. Le test tient donc le
fichier ouvert dans un thread pendant l'ecriture, et attend le refus.

Portable : un stress multi-threads lecture/ecriture, pour verifier que le verrou
suffit quand seul le processus serveur touche au fichier.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from uh import keys

WINDOWS = pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="le partage de suppression est propre a Windows"
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    keys.add_provider("deepseek", "https://example.com", "fake-1b", "canari-CLEF-0001")
    return tmp_path


@WINDOWS
def test_a_handle_held_open_makes_the_write_fail_cleanly(home: Path) -> None:
    """Le mecanisme, sans hasard : un handle ouvert, puis une ecriture.

    Apres correctif, l'echec n'est plus un `PermissionError` brut : la reprise bornee
    s'epuise (quelques dizaines de ms) puis rend un `KeysBusy` lisible, sans laisser de
    temporaire derriere elle. Le handle est libere trop tard pour elle, jamais pour lui.
    """

    path = keys.keys_path()
    assert path.is_file()

    started = time.monotonic()
    with open(path, "rb") as held:
        held.read(1)  # le handle reste ouvert, sans partage de suppression
        with pytest.raises(keys.KeysBusy) as failure:
            keys.add_provider("other", "https://example.com", "fake-1b", "x")
    elapsed = time.monotonic() - started

    assert "occupé" in str(failure.value), failure.value
    assert elapsed < 2.0, f"la reprise doit rester bornee : {elapsed:.2f}s"
    assert path.is_file(), "le fichier d'origine doit rester en place"
    assert sorted(home.glob(".keys-*.tmp")) == [], "aucun temporaire ne doit rester"


@WINDOWS
def test_a_handle_released_during_the_retries_lets_the_write_through(home: Path) -> None:
    """Le handle se libere pendant la reprise : l'ecriture aboutit sans erreur."""
    path = keys.keys_path()
    holder = open(path, "rb")  # noqa: SIM115 - ferme par le thread ci-dessous

    def release_soon() -> None:
        time.sleep(0.03)
        holder.close()

    releaser = threading.Thread(target=release_soon)
    releaser.start()
    try:
        keys.add_provider("other", "https://example.com", "fake-1b", "y")
    finally:
        releaser.join(timeout=5)
        if not holder.closed:
            holder.close()

    assert "other" in keys.read_providers()
    assert sorted(home.glob(".keys-*.tmp")) == []


def test_concurrent_readers_and_writers_do_not_corrupt_the_file(home: Path) -> None:
    """Portable : plusieurs threads lisent pendant qu'un autre ecrit."""
    errors: list[BaseException] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                keys.read_providers()
            except BaseException as exc:  # noqa: BLE001 - on collecte, on ne masque pas
                errors.append(exc)
                return
            time.sleep(0.001)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    try:
        for index in range(20):
            keys.add_provider("deepseek", "https://example.com", "fake-1b", f"canari-{index:04d}")
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

    assert not errors, f"{len(errors)} erreur(s) de lecture concurrente : {errors[:2]}"
    stored = keys.read_providers()
    assert "deepseek" in stored, "l'ecriture finale doit etre visible"


class _NoLock:
    """Un verrou qui ne verrouille rien : sert a montrer ce que le vrai empeche."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return False


def _add_each(count: int) -> dict[str, list[str]]:
    """N threads ecrivent chacun une cle differente, tous en meme temps."""
    import threading

    start = threading.Barrier(count)

    def add(index: int) -> None:
        start.wait(timeout=10)
        keys.add_key("deepseek", f"perdue-{index:03d}")

    threads = [threading.Thread(target=add, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    return keys.read_providers()


def test_no_key_is_lost_when_writers_race(home: Path) -> None:
    """Avec le verrou englobant : les 16 cles sont toutes la."""
    stored = _add_each(16)
    keeps = stored.get("deepseek", {}).get("keys") or []
    missing = [f"perdue-{index:03d}" for index in range(16) if f"perdue-{index:03d}" not in keeps]
    assert not missing, f"mise a jour perdue malgre le verrou : {missing}"


def test_the_busy_message_is_readable_french(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le 503 porte une phrase accentuee, pas de l'ASCII de fortune."""
    monkeypatch.setattr(keys, "sys", type("S", (), {"platform": "win32"}))
    with pytest.raises(keys.KeysBusy) as failure:
        with patch_replace_error():
            keys._replace_with_retry("source-inexistante", keys.keys_path())
    assert str(failure.value) == "fichier de clés occupé, réessayez", str(failure.value)


def patch_replace_error():
    """Fait echouer os.replace par PermissionError, sans toucher au vrai fichier."""
    import contextlib
    from unittest import mock

    @contextlib.contextmanager
    def _context():
        with mock.patch.object(keys.os, "replace", side_effect=PermissionError("partage")):
            yield

    return _context()
