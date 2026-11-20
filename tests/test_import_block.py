"""Bloc Import : ce qui est lu, ce qui ne l'est pas, et la frontiere qui tient.

Trois promesses, chacune avec son test :

* une pièce jointe de la session est **lisible** par l'outil `file`, alors qu'elle vit hors
  du workspace — mais `~/.uh/keys.toml`, `mcp-secrets.toml` et `web-token` restent refusés ;
* un PDF sans `pypdf` **n'est pas** annoncé comme lu : le message nomme l'extra à installer ;
* un fichier trop lourd renvoie un message français qui donne la limite et quoi faire.
"""

from __future__ import annotations

from pathlib import Path

from uh import attachments
from uh.toolsets.file import FileToolset


def test_an_upload_of_the_session_is_readable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    uploads = tmp_path / "uploads" / "s1"
    uploads.mkdir(parents=True)
    note = uploads / "rapport.txt"
    note.write_text("contenu du rapport\n", encoding="utf-8")

    toolset = FileToolset(workspace=workspace, read_roots=[uploads])
    result = toolset.read_file(str(note))

    assert "contenu du rapport" in str(result), result


def test_the_uploads_root_does_not_open_the_rest_of_uh_home(tmp_path: Path) -> None:
    """La frontiere : ce dossier, et rien d'autre dans ~/.uh."""
    home = tmp_path / "uh-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    uploads = home / "uploads" / "s1"
    uploads.mkdir(parents=True)
    (home / "keys.toml").write_text('mistral = "sk-secret"\n', encoding="utf-8")
    (uploads / "ok.txt").write_text("lisible\n", encoding="utf-8")

    toolset = FileToolset(workspace=workspace, read_roots=[uploads])

    assert "lisible" in str(toolset.read_file(str(uploads / "ok.txt")))
    refused = str(toolset.read_file(str(home / "keys.toml")))
    assert "refused" in refused and "keys.toml" in refused
    assert "sk-secret" not in refused, "un refus ne recite jamais le secret"


def test_without_read_roots_the_workspace_boundary_is_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "ailleurs.txt"
    outside.write_text("x", encoding="utf-8")

    toolset = FileToolset(workspace=workspace)
    assert "refused" in str(toolset.read_file(str(outside)))


def test_a_pdf_without_pypdf_is_not_reported_as_read(tmp_path: Path) -> None:
    """La limite est dite, pas contournee : « je ne l'ai pas lu » et voici comment."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    text, note = attachments._pdf_text(pdf)

    if text:  # pypdf est installe : on ne teste alors que l'absence de mensonge
        assert note == ""
    else:
        assert "pypdf" in note.lower(), note
        assert "pdf" in note.lower(), "le message nomme l'extra à installer"


def test_the_pdf_extra_is_declared_in_the_project() -> None:
    from uh._compat import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert any("pypdf" in item for item in extras.get("pdf", [])), extras.get("pdf")


def test_the_too_large_message_is_french_and_names_the_limit() -> None:
    source = (Path(__file__).resolve().parents[1] / "uh" / "web" / "app.py").read_text(
        encoding="utf-8"
    )
    assert "fichier trop lourd" in source
    assert "the file is larger than" not in source, "l'ancien message anglais doit avoir disparu"
    assert "human_size(attachments.MAX_BYTES)" in source, "la limite est lue, pas inventée"


def test_the_thread_lists_the_attached_files_in_french() -> None:
    app_js = (Path(__file__).resolve().parents[1] / "uh" / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    import re

    call = re.search(r'appendMessage\(\s*"user",[\s\S]{0,400}?fichier\(s\) joint', app_js)
    assert call, "la bulle doit lister les fichiers joints, dans le même appel"
    assert "file(s) attached" not in call.group(0), (
        "l'anglais générique doit avoir disparu de la bulle"
    )
    assert "fichier(s) joint(s)" in app_js
    assert "humanSize(" in app_js and "kindLabel(" in app_js
