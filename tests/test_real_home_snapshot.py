"""Ce que le cliché de ``~/.uh`` surveille — et ce qu'il ignore volontairement.

La garde ``real_home_untouched`` compare deux clichés du vrai ``~/.uh`` avant et
après le run : tout fichier qui apparaît ou change entre les deux fait échouer le
push gate. C'est le bon comportement pour une clé ou une mémoire, et le mauvais
pour un fichier qu'un ``uh web`` **vivant** réécrit tout seul.

Le 26/11, un push légitime a été bloqué par ``~/.uh/uhb.pid`` : ``uh web`` resté
ouvert dans une autre fenêtre créait puis supprimait ce pid pendant le run. Le pid
dit qu'un processus existe ; il ne porte ni secret ni mémoire. C'est exactement la
raison pour laquelle ``uhb.log`` est déjà exempté.

Ces tests mesurent le cliché lui-même, pas son source : un fichier volatil qui
change ne doit rien changer, une clé qui apparaît doit toujours être vue, et
l'exemption doit rester une **liste nommée** (un ``*.pid`` fourre-tout laisserait
passer n'importe quel fichier déguisé).
"""

from __future__ import annotations

from pathlib import Path


def _snapshot(directory: Path) -> dict[str, tuple[int, int]]:
    """Le cliché réel de la suite, importé depuis `tests/conftest.py`."""
    import conftest

    return conftest._snapshot(directory)


def test_a_live_pid_rewritten_mid_run_is_not_a_leak(tmp_path: Path) -> None:
    """Même chemin, nouveau numéro : `uh web` redémarre, rien n'a fuité."""
    pid = tmp_path / "uhb.pid"
    pid.write_text("4312", encoding="utf-8")
    before = _snapshot(tmp_path)

    pid.write_text("5127", encoding="utf-8")

    assert _snapshot(tmp_path) == before


def test_a_pid_appearing_mid_run_is_not_a_leak(tmp_path: Path) -> None:
    """Le sens qui a bloqué le 26/11 : le fichier naît pendant le run."""
    before = _snapshot(tmp_path)

    (tmp_path / "uhb.pid").write_text("5127", encoding="utf-8")

    assert _snapshot(tmp_path) == before


def test_a_credential_written_mid_run_still_trips_the_guard(tmp_path: Path) -> None:
    """L'exemption des pids ne doit pas ouvrir la porte aux clés."""
    before = _snapshot(tmp_path)

    (tmp_path / "keys.toml").write_text('[search]\nbrave_key = "x"\n', encoding="utf-8")

    assert _snapshot(tmp_path) != before


def test_the_exemption_is_a_named_list_not_a_wildcard(tmp_path: Path) -> None:
    """`uhb.pid.bak` ou `autre.pid` ne sont pas le pid du produit : vus."""
    before = _snapshot(tmp_path)

    (tmp_path / "uhb.pid.bak").write_text("1", encoding="utf-8")
    (tmp_path / "worker.pid").write_text("2", encoding="utf-8")

    assert _snapshot(tmp_path) != before


def test_the_snapshot_reads_top_level_files_only(tmp_path: Path) -> None:
    """Ce que la garde ne voit pas, écrit noir sur blanc plutôt que supposé.

    Un fichier déposé sous `memory/` ne change pas le cliché : il ne parcourt que
    le premier niveau. C'est un trou connu (la docstring parlait de « mémoire »),
    laissé tel quel ici : rendre le cliché récursif ferait échouer un run pendant
    qu'une session vivante écrit sa mémoire, exactement le faux positif que ce
    correctif supprime. À trancher par une mesure dédiée, pas par cette branche.
    """
    before = _snapshot(tmp_path)

    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "note.md").write_text("souvenir", encoding="utf-8")

    assert _snapshot(tmp_path) == before


def test_a_prefixed_name_is_not_the_product_file(tmp_path: Path) -> None:
    """`uhb.pid.bak` n'est pas `uhb.pid` : un préfixe n'exempte rien.

    Relevé par la revue de la PR #79 : `str.startswith(volatile)` exemptait tout
    nom qui *commence* par un nom volatil, donc une sauvegarde du pid glissait
    dans le cliché sans être vue. La liste doit être exacte.
    """
    (tmp_path / "uhb.pid").write_text("4312", encoding="utf-8")
    before = _snapshot(tmp_path)

    (tmp_path / "uhb.pid.bak").write_text("4312", encoding="utf-8")

    assert _snapshot(tmp_path) != before


def test_a_rotated_journal_stays_invisible(tmp_path: Path) -> None:
    """`uh-web.log.1` est le journal tourné par le produit, pas une fuite.

    La rotation ajoute un suffixe numérique : l'exemption doit le nommer, sans
    redevenir un `startswith` qui laisserait passer n'importe quoi.
    """
    (tmp_path / "uh-web.log").write_text("x", encoding="utf-8")
    before = _snapshot(tmp_path)

    (tmp_path / "uh-web.log.1").write_text("y" * 16, encoding="utf-8")

    assert _snapshot(tmp_path) == before


def test_the_sqlite_sidecars_of_the_store_stay_invisible(tmp_path: Path) -> None:
    """Le store est en WAL : `uh.db-wal` et `uh.db-shm` bougent avec une session."""
    (tmp_path / "uh.db").write_text("db", encoding="utf-8")
    before = _snapshot(tmp_path)

    (tmp_path / "uh.db-wal").write_text("wal", encoding="utf-8")
    (tmp_path / "uh.db-shm").write_text("shm", encoding="utf-8")

    assert _snapshot(tmp_path) == before
