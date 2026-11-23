"""Faille d'autofill (23/11) : aucun formulaire ne doit ressembler à une connexion.

Le gestionnaire de mots de passe d'Edge remplissait `model` et `account` avec une URL
et les champs secrets avec des points, parce que le panneau Fournisseurs était bâti sur
des `<form>` contenant un `input type=password` — la forme exacte d'une page de login.

Chromium ne peut pas être forcé à autofill dans un test : ce qui se mesure ici, ce sont
les **conditions** qui le déclenchent (ou non), plus l'invariant qui les rend inopérantes :

- aucun `<form>` ne contient de champ secret ;
- tout champ secret est vide au rendu et déclare `autocomplete="new-password"` ;
- les noms et ids ne sont pas ceux d'un formulaire de connexion ;
- « Enregistrer » reste désactivé tant que la clé est vide.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.e2e

from .conftest import Server, open_app  # noqa: E402

LOGIN_NAMES = {
    "key",
    "token",
    "password",
    "passwd",
    "secret",
    "model",
    "account",
    "username",
    "user",
    "email",
    "login",
}


def open_keys_tab(browser, server: Server):
    context, page = open_app(browser, server)
    page.evaluate("document.getElementById('tab-keys').click()")
    page.wait_for_selector("#keys-view", timeout=20000)
    page.wait_for_selector("#providers-list .provider-card", timeout=20000)
    return context, page


def test_no_form_holds_a_secret_field(browser, server: Server) -> None:
    context, page = open_keys_tab(browser, server)
    try:
        count = page.evaluate("document.querySelectorAll('form input[type=password]').length")
        assert count == 0, "un formulaire contenant un mot de passe est une page de connexion"
    finally:
        context.close()


def test_secret_fields_are_empty_and_opt_out(browser, server: Server) -> None:
    context, page = open_keys_tab(browser, server)
    try:
        audit = page.evaluate(
            """() => {
                const secrets = [...document.querySelectorAll('#keys-view input[type=password]',
                                                            '#conn-view input[type=password]')];
                return {
                  total: secrets.length,
                  filled: secrets.filter((el) => el.value !== '').length,
                  wrong: secrets.filter((el) => el.autocomplete !== 'new-password').length,
                  forms: [...new Set(secrets.map((el) => el.closest('form') ? 'in-form' : 'none'))],
                  names: secrets.map((el) => (el.name || el.id || '').toLowerCase()),
                };
            }"""
        )
        assert audit["total"] >= 1, "au moins un fournisseur a un champ secret"
        assert audit["filled"] == 0, f"{audit['filled']} champ(s) secret(s) non vides au rendu"
        assert audit["wrong"] == 0, f"{audit['wrong']} champ(s) sans autocomplete=new-password"
        assert audit["forms"] == ["none"], audit["forms"]
    finally:
        context.close()


def test_no_field_named_like_a_login_form(browser, server: Server) -> None:
    context, page = open_keys_tab(browser, server)
    try:
        names = page.evaluate(
            """() => [...document.querySelectorAll('#keys-view input, #conn-view input')]
                     .map((el) => (el.name || '').toLowerCase())
                     .filter(Boolean)"""
        )
        offenders = sorted({name for name in names if name in LOGIN_NAMES})
        assert offenders == [], f"noms de champs qui invitent le navigateur à remplir : {offenders}"
        # Les noms sont uniques : deux champs « key » identiques, et le navigateur
        # réutilise la valeur enregistrée pour l'un sur l'autre.
        assert len(names) == len(set(names)), "des noms de champs se répètent"
    finally:
        context.close()


def test_save_stays_disabled_until_a_key_is_typed(browser, server: Server) -> None:
    context, page = open_keys_tab(browser, server)
    try:
        before = page.evaluate(
            """() => {
                const block = [...document.querySelectorAll('#keys-view .custom-form')]
                  .find((node) => node.querySelector('input[type=password]'));
                if (!block) return null;
                const button = block.querySelector('.key-actions button');
                return { disabled: button.disabled, value: block.querySelector('input[type=password]').value };
            }"""
        )
        assert before is not None, "aucun bloc de saisie de clé trouvé"
        assert before["value"] == ""
        assert before["disabled"] is True, "« Enregistrer » est actif alors que la clé est vide"

        # On tape une valeur : le bouton s'active. La route est interceptée pour que
        # rien de faux n'entre dans la configuration du serveur de test.
        page.route(
            "**/api/providers/**",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"ok": True})
            ),
        )
        page.evaluate(
            """() => {
                const block = [...document.querySelectorAll('#keys-view .custom-form')]
                  .find((node) => node.querySelector('input[type=password]'));
                const input = block.querySelector('input[type=password]');
                input.value = 'sk-test-123';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            }"""
        )
        after = page.evaluate(
            """() => {
                const block = [...document.querySelectorAll('#keys-view .custom-form')]
                  .find((node) => node.querySelector('input[type=password]'));
                return block.querySelector('.key-actions button').disabled;
            }"""
        )
        assert after is False, "« Enregistrer » reste inactif après une saisie"
    finally:
        context.close()


def test_enter_in_a_text_field_still_saves(browser, server: Server) -> None:
    """Sans <form>, Entrée doit continuer d'envoyer : c'est un geste acquis."""
    context, page = open_keys_tab(browser, server)
    sent: list[dict] = []

    def capture(route):
        sent.append({"url": route.request.url, "body": route.request.post_data})
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    page.route("**/api/providers/**", capture)
    try:
        page.click("#keys-view .provider-custom summary")
        page.wait_for_selector("#keys-view .provider-custom input", timeout=10000)
        first = page.locator("#keys-view .provider-custom input").first
        first.fill("monserveur")
        first.press("Enter")
        page.wait_for_timeout(600)
        posts = [entry for entry in sent if entry["url"].endswith("/api/providers/custom")]
        assert posts, f"Entrée n'a rien envoyé (requêtes vues : {[e['url'] for e in sent]})"
        assert "monserveur" in (posts[0]["body"] or "")
    finally:
        context.close()


def test_tab_follows_the_visible_order(browser, server: Server) -> None:
    context, page = open_keys_tab(browser, server)
    try:
        page.click("#keys-view .provider-custom summary")
        page.wait_for_selector("#keys-view .provider-custom input", timeout=10000)
        inputs = page.locator("#keys-view .provider-custom input")
        inputs.first.focus()
        page.keyboard.press("Tab")
        focused = page.evaluate(
            """() => {
                const el = document.activeElement;
                return { tag: el.tagName, placeholder: el.placeholder || '' };
            }"""
        )
        assert focused["tag"] == "INPUT", focused
        assert "https" in focused["placeholder"], f"Tab saute ailleurs : {focused}"
        page.keyboard.press("Shift+Tab")
        back = page.evaluate("() => document.activeElement.placeholder || ''")
        assert back == inputs.first.get_attribute("placeholder"), f"Maj+Tab revient sur {back!r}"
    finally:
        context.close()


def test_every_field_keeps_an_accessible_name(browser, server: Server) -> None:
    """Un champ sans nom accessible est un champ muet pour un lecteur d'écran."""
    context, page = open_keys_tab(browser, server)
    try:
        audit = page.evaluate(
            """() => {
                const nodes = [...document.querySelectorAll('#keys-view input')];
                return nodes.map((el) => ({
                  label: (el.labels && el.labels[0] ? el.labels[0].textContent : '').trim(),
                  aria: (el.getAttribute('aria-label') || '').trim(),
                  placeholder: el.placeholder || '',
                }));
            }"""
        )
        assert audit, "aucun champ trouvé dans le panneau"
        mute = [entry for entry in audit if not (entry["label"] or entry["aria"])]
        assert mute == [], f"champs sans nom accessible : {mute}"
    finally:
        context.close()
