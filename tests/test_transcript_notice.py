"""P1-12: a desynchronised view is a state in the header, never a card in the thread.

These read the interface files the way the browser does — the only artifact a unit
test can hold (same convention as ``tests/test_web_ux.py``). The live proof — a real
Chromium, a real >200-message session, the click — is ``tests/e2e/test_p1_12_transcript_notice.py``.

What the lot fixed, measured on the previous code:

* the notice was a red ``failed`` bubble inserted **between two messages**, saying
  *« Le transcript enregistré contient moins de messages que l'écran »* — our word
  ("transcript"), not the user's, on top of looking like data loss;
* the store was read with a **fixed 200-row window** and compared against the whole
  thread, so any conversation past 200 messages reported a gap on every turn
  (Codex review on PR #22);
* it was believed on the first read, although the writer commits the turn's row
  just after ``done``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "uh" / "web" / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "uh" / "web" / "static" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "uh" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def reconcile_body() -> str:
    start = APP_JS.index("async function reconcileTranscript()")
    end = APP_JS.index("const claimed = new Set(", start)
    return APP_JS[start:end]


def test_the_notice_no_longer_lives_in_the_conversation() -> None:
    assert "Le transcript enregistré contient moins de messages" not in APP_JS
    assert "Recharger la session" not in APP_JS
    assert 'appendMessage(\n      "system"' not in reconcile_body(), (
        "rien ne doit être dessiné dans le fil"
    )


def test_the_header_carries_a_discreet_control_in_plain_french() -> None:
    assert 'id="view-stale"' in INDEX_HTML
    assert "Actualiser la vue" in INDEX_HTML
    assert "hidden" in INDEX_HTML.split('id="view-stale"', 1)[1].split(">", 1)[0]
    assert ".view-stale[hidden]" in STYLE_CSS, "un bouton caché doit vraiment disparaître"
    # L'ancien encart rouge n'a plus de raison d'être : plus aucun bouton d'alerte.
    assert "notice-action" not in reconcile_body()


def test_the_read_window_covers_the_thread_and_drops_the_fixed_200() -> None:
    body = reconcile_body()
    assert "messages?limit=200" not in body, "la fenêtre fixe est la cause du faux positif >200"
    assert "RECONCILE_WINDOW_MARGIN" in body
    assert "boxes.length + RECONCILE_WINDOW_MARGIN" in body
    assert "storedMessages(wanted)" in body


def test_a_gap_is_only_believed_after_a_second_read() -> None:
    body = reconcile_body()
    assert "RECONCILE_RETRY_MS" in body
    assert body.count("storedMessages(wanted)") >= 2, "une relecture avant de conclure"
    assert "await new Promise((resolve) => setTimeout(resolve, RECONCILE_RETRY_MS));" in body


def test_a_capped_window_never_raises_the_flag() -> None:
    body = reconcile_body()
    assert "RECONCILE_MAX_WINDOW" in body
    assert (
        "const comparable = boxes.length + RECONCILE_WINDOW_MARGIN <= RECONCILE_MAX_WINDOW;" in body
    )
    assert "const desynced = comparable && stored.length < boxes.length;" in body


def test_the_control_is_wired_and_cleared_when_the_view_is_fresh() -> None:
    assert "function wireViewStale()" in APP_JS
    assert "wireViewStale();" in APP_JS, "le bouton doit être branché au démarrage"
    # Effacé au chargement d'une session et après un rafraîchissement réussi.
    assert APP_JS.count("viewStale(false);") >= 2
    assert "viewStale(desynced);" in APP_JS


def test_no_folder_or_message_is_deleted_by_the_reconciliation() -> None:
    body = reconcile_body()
    for forbidden in (
        "deleteMessage",
        "deleteSession",
        "deleteFolder",
        "remove()",
        'innerHTML = ""',
    ):
        assert forbidden not in body, f"{forbidden} n'a rien à faire dans une réconciliation"
