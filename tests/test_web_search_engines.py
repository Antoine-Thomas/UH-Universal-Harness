"""La chaîne de moteurs de recherche : ordre, clés absentes, échec honnête, images.

Aucun de ces tests ne parle au réseau : le transport est injecté, et il enregistre
l'URL réellement appelée. C'est ce qui permet de **mesurer** « aucun appel sans clé »
au lieu de le lire dans le code.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from uh import keys as uh_keys
from uh.toolsets import WebSearchToolset
from uh.toolsets.web_search import (
    BRAVE_ENDPOINT,
    BRAVE_IMAGES_ENDPOINT,
    DDG_ENDPOINT,
    TAVILY_ENDPOINT,
    engine_chain,
    search_settings,
)

BRAVE_KEY = "brave-" + "k" * 40
TAVILY_KEY = "tvly-" + "t" * 40
SAMPLE_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="https://example.com/page">Example Page</a>
  <a class="result__snippet">An example snippet.</a>
</div>
</body></html>
"""


class FakeResponse:
    """Une réponse scriptée : statut, corps texte et JSON, sans réseau."""

    def __init__(self, payload=None, *, text: str = "", status: int = 200) -> None:
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("pas de JSON dans cette réponse")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", DDG_ENDPOINT)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


class Transport:
    """Un faux transport qui note chaque URL appelée, dans l'ordre."""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((str(url), kwargs))
        answer = self.routes.get(str(url))
        if answer is None:
            raise httpx.ConnectError(f"aucune route scriptée pour {url}")
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def post(self, url, **kwargs):
        return self.get(url, **kwargs)

    @property
    def urls(self) -> list[str]:
        return [url for url, _kwargs in self.calls]


def toolset(transport: Transport, settings: dict | None = None, **kwargs) -> WebSearchToolset:
    return WebSearchToolset(
        get=transport.get,
        post=transport.post,
        settings=settings if settings is not None else {},
        backoff=0.0,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# ordre et candidats
# --------------------------------------------------------------------------- #
def test_the_chain_runs_brave_then_tavily_then_searxng_then_duckduckgo() -> None:
    chain = engine_chain(
        {"brave": BRAVE_KEY, "tavily": TAVILY_KEY, "searxng_url": "http://127.0.0.1:8888/"}
    )
    assert [engine["name"] for engine in chain] == ["brave", "tavily", "searxng", "duckduckgo"]


def test_a_missing_key_removes_the_engine_instead_of_sending_a_request() -> None:
    chain = engine_chain({})
    assert [engine["name"] for engine in chain] == ["duckduckgo"]


def test_a_keyless_machine_calls_duckduckgo_and_nothing_else() -> None:
    """La règle du projet : pas de clé, pas d'appel — la preuve est la liste des URLs."""
    transport = Transport({DDG_ENDPOINT: FakeResponse(text=SAMPLE_HTML)})
    out = toolset(transport).call("web_search", {"query": "moutons"})
    assert out["count"] == 1
    assert out["engine"] == "duckduckgo"
    assert transport.urls == [DDG_ENDPOINT], transport.urls
    assert BRAVE_ENDPOINT not in transport.urls and TAVILY_ENDPOINT not in transport.urls


def test_the_first_engine_that_answers_wins_and_the_rest_is_not_called() -> None:
    transport = Transport(
        {
            BRAVE_ENDPOINT: FakeResponse(
                {
                    "web": {
                        "results": [
                            {
                                "title": "Brave title",
                                "url": "https://brave.example/1",
                                "description": "from brave",
                            }
                        ]
                    }
                }
            ),
            DDG_ENDPOINT: FakeResponse(text=SAMPLE_HTML),
        }
    )
    out = toolset(transport, {"brave": BRAVE_KEY, "tavily": TAVILY_KEY}).call(
        "web_search", {"query": "moutons"}
    )
    assert out["engine"] == "brave"
    assert out["results"][0]["title"] == "Brave title"
    assert transport.urls == [BRAVE_ENDPOINT], "les moteurs suivants n'ont pas à être payés"


def test_tavily_is_used_when_brave_is_not_configured() -> None:
    transport = Transport(
        {
            TAVILY_ENDPOINT: FakeResponse(
                {"results": [{"title": "T", "url": "https://t.example", "content": "c"}]}
            )
        }
    )
    out = toolset(transport, {"tavily": TAVILY_KEY}).call("web_search", {"query": "x"})
    assert out["engine"] == "tavily"
    assert transport.urls == [TAVILY_ENDPOINT]
    # La clé part dans le corps, jamais dans l'URL (une URL se journalise).
    body = transport.calls[0][1]["json"]
    assert body["api_key"] == TAVILY_KEY
    assert TAVILY_KEY not in transport.urls[0]


def test_a_configured_searxng_instance_is_asked_in_json() -> None:
    transport = Transport(
        {
            "http://127.0.0.1:8888/search": FakeResponse(
                {"results": [{"title": "S", "url": "https://s.example", "content": "c"}]}
            )
        }
    )
    out = toolset(transport, {"searxng_url": "http://127.0.0.1:8888"}).call(
        "web_search", {"query": "x"}
    )
    assert out["engine"] == "searxng"
    assert transport.calls[0][0] == "http://127.0.0.1:8888/search"
    assert transport.calls[0][1]["params"] == {"q": "x", "format": "json"}


# --------------------------------------------------------------------------- #
# échec honnête
# --------------------------------------------------------------------------- #
def test_a_failing_engine_does_not_stop_the_chain_and_is_reported() -> None:
    transport = Transport(
        {
            BRAVE_ENDPOINT: FakeResponse(status=401),
            DDG_ENDPOINT: FakeResponse(text=SAMPLE_HTML),
        }
    )
    out = toolset(transport, {"brave": BRAVE_KEY}).call("web_search", {"query": "x"})
    assert out["engine"] == "duckduckgo"
    assert out["failed"] == ["brave: HTTP 401"], "Brave a refusé : ça se sait"


def test_when_every_engine_fails_the_message_names_each_one_and_why() -> None:
    transport = Transport(
        {
            BRAVE_ENDPOINT: FakeResponse(status=429),
            TAVILY_ENDPOINT: httpx.ConnectError("budget épuisé"),
        }
    )
    out = toolset(transport, {"brave": BRAVE_KEY, "tavily": TAVILY_KEY}).call(
        "web_search", {"query": "x"}
    )
    assert out["type"] == "tool_error"
    assert out["error"].startswith("no engine answered:")
    assert "brave: HTTP 429" in out["error"]
    assert "tavily: ConnectError" in out["error"]
    assert "duckduckgo:" in out["error"]
    assert "retry later" not in out["error"], (
        "une phrase qui n'aide personne n'est pas un échec honnête"
    )


def test_a_202_without_results_is_reported_as_throttled(monkeypatch) -> None:
    """Mesuré le 23/11 : 202 + page sans résultat = le mur anti-robot de DuckDuckGo.

    Ce n'est pas « aucun résultat » : c'est un refus, et le message le dit — sinon
    l'opérateur croit que sa requête était trop précise.
    """
    transport = Transport(
        {
            DDG_ENDPOINT: FakeResponse(
                payload=None, text="<html><body>rien</body></html>", status=202
            )
        }
    )
    out = toolset(transport).call("web_search", {"query": "moutons"})
    assert out["type"] == "tool_error"
    assert "duckduckgo: HTTP 202" in out["error"], out["error"]
    assert "throttled" in out["error"]
    assert "retry later" not in out["error"]


def test_no_key_ever_appears_in_a_result_or_an_error() -> None:
    transport = Transport({BRAVE_ENDPOINT: FakeResponse(status=403)})
    out = toolset(transport, {"brave": BRAVE_KEY}).call("web_search", {"query": "x"})
    blob = json.dumps(out, ensure_ascii=False)
    assert BRAVE_KEY not in blob
    assert "brave: HTTP 403" in out["failed"][0]


# --------------------------------------------------------------------------- #
# réglages : keys.toml et environnement
# --------------------------------------------------------------------------- #
def test_search_keys_are_read_from_the_search_section_of_keys_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    (tmp_path / "keys.toml").write_text(
        '[search]\nbrave_key = "' + BRAVE_KEY + '"\nsearxng_url = "http://127.0.0.1:8888"\n',
        encoding="utf-8",
    )
    assert uh_keys.read_search() == {"brave_key": BRAVE_KEY, "searxng_url": "http://127.0.0.1:8888"}
    settings = search_settings({})
    assert settings == {"brave": BRAVE_KEY, "searxng_url": "http://127.0.0.1:8888"}


def test_the_environment_wins_over_the_keys_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    (tmp_path / "keys.toml").write_text('[search]\nbrave_key = "from-file-key"\n', encoding="utf-8")
    settings = search_settings({"BRAVE_API_KEY": BRAVE_KEY})
    assert settings["brave"] == BRAVE_KEY


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
def test_image_search_without_a_key_says_so_and_sends_nothing() -> None:
    transport = Transport({})
    out = toolset(transport).call("web_image_search", {"query": "moutons"})
    assert out["type"] == "tool_error"
    assert "no image engine configured" in out["error"]
    assert "keys.toml" in out["error"], "la réponse dit où mettre la clé"
    assert transport.calls == [], "aucune requête ne part sans moteur d'images"


def test_image_search_uses_brave_images_and_returns_image_urls() -> None:
    transport = Transport(
        {
            BRAVE_IMAGES_ENDPOINT: FakeResponse(
                {
                    "results": [
                        {
                            "title": "Mouton",
                            "url": "https://page.example/mouton",
                            "properties": {"url": "https://img.example/mouton.jpg"},
                            "thumbnail": {"src": "https://img.example/thumb.jpg"},
                        }
                    ]
                }
            )
        }
    )
    out = toolset(transport, {"brave": BRAVE_KEY}).call("web_image_search", {"query": "moutons"})
    assert out["engine"] == "brave"
    assert out["results"][0]["image"] == "https://img.example/mouton.jpg"
    assert out["results"][0]["url"] == "https://page.example/mouton"
    assert transport.urls == [BRAVE_IMAGES_ENDPOINT]


def test_image_engines_lists_brave_only_when_it_is_configured() -> None:
    assert toolset(Transport({})).image_engines() == []
    assert toolset(Transport({}), {"brave": BRAVE_KEY}).image_engines() == ["brave"]


# --------------------------------------------------------------------------- #
# la commande /web image
# --------------------------------------------------------------------------- #
def _session(web: WebSearchToolset):
    from types import SimpleNamespace

    return SimpleNamespace(toolsets={"web_search": web})


def test_the_web_image_command_prints_the_image_and_its_page() -> None:
    from uh import commands

    transport = Transport(
        {
            BRAVE_IMAGES_ENDPOINT: FakeResponse(
                {
                    "results": [
                        {
                            "title": "Mouton",
                            "url": "https://page.example/mouton",
                            "properties": {"url": "https://img.example/mouton.jpg"},
                        }
                    ]
                }
            )
        }
    )
    result = commands._cmd_web(_session(toolset(transport, {"brave": BRAVE_KEY})), "image moutons")
    assert not result.err, result.err
    assert "https://img.example/mouton.jpg" in result.out
    assert "https://page.example/mouton" in result.out


def test_the_web_image_command_uses_the_keyless_engines_when_no_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sans clé Brave, Openverse et Commons répondent — et aucune recherche web.

    L'ancien refus (« aucun moteur d'images configuré ») est devenu faux le jour
    où deux moteurs sans clé ont existé : le refus muet a donc disparu, pas la
    règle qui l'accompagnait (jamais une recherche texte à la place d'images).
    """
    from uh import commands, image_search

    hit = image_search.Hit(
        title="le cochon",
        thumb="https://api.openverse.org/v1/images/x/thumb/",
        page="https://www.flickr.com/photos/1",
        domain="www.flickr.com",
        licence="by 2.0",
        author="pilllpat",
        provider="flickr",
    )
    monkeypatch.setattr(
        image_search,
        "search_images",
        lambda *_args, **_kwargs: image_search.Result(
            hits=[hit], failed={}, engines=["openverse"]
        ),
    )
    transport = Transport({})
    result = commands._cmd_web(_session(toolset(transport)), "image cochons")

    assert not result.err, result.err
    assert "https://www.flickr.com/photos/1" in result.out
    assert "by 2.0" in result.out and "pilllpat" in result.out
    assert result.cards is not None and result.cards["kind"] == "images"
    assert result.cards["results"][0]["licence"] == "by 2.0"
    assert transport.calls == [], "aucune recherche web à la place des images"


def test_the_web_image_command_says_why_when_the_keyless_engines_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uh import commands, image_search

    monkeypatch.setattr(
        image_search,
        "search_images",
        lambda *_args, **_kwargs: image_search.Result(
            hits=[], failed={"openverse": "HTTP 500"}, engines=[]
        ),
    )
    result = commands._cmd_web(_session(toolset(Transport({}))), "image cochons")

    assert "aucune image" in result.out
    assert "openverse" in result.out, "la raison est nommée, pas cachée"


def test_the_web_search_command_carries_structured_cards() -> None:
    """Le terminal garde son texte ; la page reçoit les mêmes résultats en cartes."""
    from uh import commands

    transport = Transport({DDG_ENDPOINT: FakeResponse(text=SAMPLE_HTML)})
    result = commands._cmd_web(_session(toolset(transport)), "cochon")

    assert result.cards is not None
    assert result.cards["kind"] == "web"
    assert result.cards["results"], "au moins une carte"
    first = result.cards["results"][0]
    assert first["url"] == "https://example.com/page"
    assert first["domain"] == "example.com"
    assert first["title"] == "Example Page"
    assert "example snippet" in first["snippet"]


def test_the_web_image_command_alone_stays_a_web_search() -> None:
    """`/web` sans argument sert l'interface : le mot « image » ne doit rien casser."""
    from uh import commands

    transport = Transport({DDG_ENDPOINT: FakeResponse(text=SAMPLE_HTML)})
    result = commands._cmd_web(_session(toolset(transport)), "imagesque")
    assert not result.err
    assert transport.urls == [DDG_ENDPOINT], "« imagesque » est une requête, pas un préfixe"
