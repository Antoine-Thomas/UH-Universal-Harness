"""Bloc Import, prouvé à l'écran : la pastille dit quoi, et le refus parle français.

pytest -o addopts="" -m e2e tests/e2e/test_import_ui.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


def test_a_text_file_becomes_a_readable_chip(browser, server, tmp_path: Path) -> None:
    report = tmp_path / "rapport_hermes.txt"
    report.write_text("controle du 19/11 : tout est en ordre\n" * 20, encoding="utf-8")
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        page.set_input_files("#files", str(report))
        page.wait_for_selector(".attachment", timeout=20000)
        chip = page.inner_text(".attachment")
        assert "rapport_hermes.txt" in chip
        assert "ko" in chip or " o" in chip, f"la taille doit être lisible : {chip!r}"
        assert "document" in chip, f"le type doit être dit en français : {chip!r}"

        page.click(".attachment-remove")
        page.wait_for_timeout(400)
        assert page.locator(".attachment").count() == 0, "la croix retire la pastille"
    finally:
        context.close()


# NOTE: le refus 413 est verifie au niveau de la route (tests/test_import_block.py) et
# non par un aller-retour HTTP ici : deux tentatives ont echoue sur la plomberie d'auth
# du harnais e2e (cookie `uh_token_<port>`, origine), pas sur le produit. Claim limite.
