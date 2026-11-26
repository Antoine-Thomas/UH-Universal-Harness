"""Recherche web : des moteurs configurables, DuckDuckGo en dernier recours.

Le terrain du 23/11 : ``no results parsed`` sur ``/web``, parce que l'endpoint HTML de
DuckDuckGo limite le débit — et aucun moyen de chercher une image. Ce module remplace
« un endpoint » par une **chaîne de moteurs**, dans un ordre décidé :

1. **Brave Search** (``BRAVE_API_KEY`` / ``UH_BRAVE_API_KEY``, ou ``[search] brave_key``
   dans ``keys.toml``) ;
2. **Tavily** (``TAVILY_API_KEY`` / ``UH_TAVILY_API_KEY``, ou ``[search] tavily_key``) ;
3. **SearXNG** (instance configurée : ``UH_SEARXNG_URL`` / ``SEARXNG_URL``, ou
   ``[search] searxng_url``) ;
4. **DuckDuckGo** HTML, sans clé, en dernier recours.

Règles tenues par les tests, pas par la bonne volonté :

* **aucun appel réseau sans clé** : un moteur dont la clé manque n'est même pas
  candidat, donc l'URL de Brave ne part jamais depuis une machine sans clé ;
* **échec honnête** : quand toute la chaîne échoue, le message nomme chaque moteur et
  sa raison (statut, quota, connexion) — jamais un « retry later » qui n'aide personne ;
* **images** : ``/web image`` utilise un moteur d'images **réel** (Brave Images) s'il est
  configuré, sinon il écrit qu'aucun ne l'est — et zéro requête part dans ce cas.

Les clés ne sont jamais renvoyées ni journalisées : elles ne sortent que dans l'en-tête
du moteur qui les possède.
"""

from __future__ import annotations

import os
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

import httpx

#: L'endpoint HTML historique, gardé comme dernier recours (aucune clé requise).
DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_IMAGES_ENDPOINT = "https://api.search.brave.com/res/v1/images/search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
MAX_RESULTS = 5
TIMEOUT = 10.0
#: A dropped connection is the normal failure of the DDG endpoint: DuckDuckGo (or a
#: local middlebox) closes the socket under a request it has not answered yet.
#: Seen in the field as ``ConnectError: [WinError 10054]`` on every ``/web``
#: command, which used to surface as a red "the model failed" bubble after 100 ms
#: - the model had not been called at all. One retry costs 0.4 s and turns a
#: transient close into an answer.
ATTEMPTS = 3
BACKOFF_S = 0.4
# A plain httpx UA gets an empty 202 (bot wall); a browser UA gets results.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

#: L'ordre de la chaîne, tel qu'il est annoncé et testé.
ENGINE_ORDER = ("brave", "tavily", "searxng", "duckduckgo")

#: Variables d'environnement acceptées en plus de ``keys.toml`` (les deux se lisent
#: sans jamais écrire ni afficher une clé).
ENV_KEYS = {
    "brave": ("UH_BRAVE_API_KEY", "BRAVE_API_KEY"),
    "tavily": ("UH_TAVILY_API_KEY", "TAVILY_API_KEY"),
    "searxng_url": ("UH_SEARXNG_URL", "SEARXNG_URL"),
}

#: Les noms acceptés dans la section ``[search]`` de ``keys.toml``.
STORED_KEYS = {
    "brave": ("brave_key", "brave"),
    "tavily": ("tavily_key", "tavily"),
    "searxng_url": ("searxng_url",),
}

TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web. Uses the configured engines in order (Brave, Tavily, "
                "local SearXNG) and DuckDuckGo as a last resort. Returns up to 5 "
                "results (title, url, snippet)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results, 1-5 (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_image_search",
            "description": (
                "Find a REAL photograph on the web (Openverse, Wikimedia Commons, and the "
                "configured image engine) and return image URLs with their licence and "
                "author. Use it as soon as the user asks for a photo or a picture of "
                "something that exists ('la photo d'un chat', 'une image de la tour "
                "Eiffel'): it is the only tool here that returns images. Do NOT use "
                "`execute_code` for such a request - code draws an illustration, never a "
                "photograph. `execute_code` is for charts, diagrams and drawings the user "
                "asks you to create."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to show, subject only ('chat', 'tour Eiffel'), in the "
                            "conversation's language"
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of images, 1-5 (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class _ResultsParser(HTMLParser):
    """Extract (title, url, snippet) triples from the DDG HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._capture: str | None = None  # "title" | "snippet"
        self._chunks: List[str] = []
        self._href: str = ""

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        if tag != "a":
            return
        classes = dict(attrs).get("class", "")
        if "result__a" in classes:
            self._capture = "title"
            self._chunks = []
            self._href = dict(attrs).get("href", "")
        elif "result__snippet" in classes:
            self._capture = "snippet"
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._capture:
            return
        text = " ".join("".join(self._chunks).split())
        if self._capture == "title":
            self.results.append({"title": text, "url": _decode_href(self._href), "snippet": ""})
        elif self._capture == "snippet" and self.results:
            self.results[-1]["snippet"] = text
        self._capture = None
        self._chunks = []


def _decode_href(href: str) -> str:
    """Unwrap DDG redirect links; pass through anything already absolute."""
    if href.startswith(("http://", "https://")):
        return href
    parsed = urllib.parse.urlparse(f"https:{href}" if href.startswith("//") else href)
    uddg = urllib.parse.parse_qs(parsed.query).get("uddg")
    return uddg[0] if uddg else href


def parse_results(html: str) -> List[Dict[str, str]]:
    parser = _ResultsParser()
    parser.feed(html)
    return [r for r in parser.results if r["url"].startswith(("http://", "https://"))]


class _SearchFailed(Exception):
    """Every attempt failed, or the endpoint answered with something final."""

    def __init__(self, cause: BaseException, attempts: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempts = attempts


class EngineFailed(Exception):
    """Un moteur a refusé de répondre — la raison est portée, jamais devinée."""

    def __init__(self, engine: str, reason: str, attempts: int = 1) -> None:
        super().__init__(f"{engine}: {reason}")
        self.engine = engine
        self.reason = reason
        # Ce que ce moteur a coûté en requêtes : un 403 coûte une requête, une
        # connexion coupée peut en coûter trois, et le total doit le dire.
        self.attempts = max(1, int(attempts))


# --------------------------------------------------------------------------- #
# configuration : quels moteurs sont candidats
# --------------------------------------------------------------------------- #
def _from_environ(environ: Dict[str, Any], name: str) -> str:
    for candidate in ENV_KEYS.get(name, ()):
        value = str(environ.get(candidate) or "").strip()
        if value:
            return value
    return ""


def search_settings(environ: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Clés et URL de recherche : environnement d'abord, puis ``keys.toml``.

    Une valeur vide n'est pas une configuration : « pas de clé » doit rester une
    absence, sinon un moteur sans clé partirait quand même en appel réseau.
    """
    from .. import keys as uh_keys

    environ = dict(os.environ if environ is None else environ)
    try:
        stored = uh_keys.read_search()
    except Exception:  # noqa: BLE001 - un fichier de clés illisible n'est pas une panne
        stored = {}
    settings: Dict[str, str] = {}
    for name, stored_names in STORED_KEYS.items():
        value = _from_environ(environ, name)
        if not value:
            for key in stored_names:
                value = str(stored.get(key) or "").strip()
                if value:
                    break
        if value:
            settings[name] = value
    return settings


def engine_chain(settings: Dict[str, str]) -> List[Dict[str, str]]:
    """Les moteurs candidats, dans l'ordre, **sans jamais inventer une clé**.

    Un moteur sans clé (Brave, Tavily) ou sans URL (SearXNG) n'entre pas dans la
    liste : c'est ce qui garantit qu'aucune requête ne part vers une API qu'on ne peut
    pas interroger. DuckDuckGo est toujours présent, et toujours dernier.
    """
    chain: List[Dict[str, str]] = []
    if settings.get("brave"):
        chain.append({"name": "brave", "kind": "brave", "key": settings["brave"]})
    if settings.get("tavily"):
        chain.append({"name": "tavily", "kind": "tavily", "key": settings["tavily"]})
    if settings.get("searxng_url"):
        chain.append(
            {"name": "searxng", "kind": "searxng", "url": settings["searxng_url"].rstrip("/")}
        )
    chain.append({"name": "duckduckgo", "kind": "duckduckgo"})
    return chain


class WebSearchToolset:
    name = "web_search"
    tools = TOOLS

    def __init__(
        self,
        *,
        endpoint: str = DDG_ENDPOINT,
        timeout: float = TIMEOUT,
        attempts: int = ATTEMPTS,
        backoff: float = BACKOFF_S,
        sleep: Any = None,
        get: Any = None,
        post: Any = None,
        settings: Optional[Dict[str, str]] = None,
        environ: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.attempts = max(1, int(attempts))
        self.backoff = max(0.0, float(backoff))
        self._sleep = sleep or time.sleep
        # Transport injectable : un transport scripté ne peut pas mentir sur l'URL
        # appelée, donc « aucun appel sans clé » se mesure au lieu de se lire. Un
        # transport absent est résolu à l'APPEL, pas à la construction : un test qui
        # remplace ``httpx.get`` après coup doit être entendu.
        self._get = get
        self._post = post
        self.settings = dict(search_settings(environ) if settings is None else settings)
        self.engines = engine_chain(self.settings)

    def _http(self, method: str):
        """Le transport à utiliser : injecté, sinon ``httpx`` au moment de l'appel."""
        injected = self._get if method == "get" else self._post
        return injected or getattr(httpx, method)

    # -- DuckDuckGo -------------------------------------------------------- --
    def _fetch_ddg(self, query: str) -> Any:
        """GET the DDG endpoint, retrying only what a retry can fix.

        A transport error (connect, read, protocol, timeout) and a 5xx/429 are
        hiccups: the same request may work a second later. Any other status is an
        ANSWER - retrying a 403 just pays the same refusal twice.
        """
        failure: BaseException | None = None
        for attempt in range(1, self.attempts + 1):
            if attempt > 1:
                self._sleep(self.backoff * (2 ** (attempt - 2)))
            try:
                response = self._http("get")(
                    self.endpoint,
                    params={"q": query},
                    headers={"User-Agent": USER_AGENT},
                    timeout=self.timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", 0) or 0
                if status != 429 and status < 500:
                    raise _SearchFailed(exc, attempt) from exc
                failure = exc
            except httpx.TransportError as exc:
                failure = exc
        raise _SearchFailed(failure or RuntimeError("no attempt was made"), self.attempts)

    def _search_ddg(self, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            response = self._fetch_ddg(query)
        except _SearchFailed as exc:
            raise EngineFailed(
                "duckduckgo",
                f"search failed after {exc.attempts} attempt(s): "
                f"{type(exc.cause).__name__}: {exc.cause}",
                attempts=exc.attempts,
            ) from exc
        results = parse_results(response.text)
        # Mesuré le 23/11 : un 202 avec une page sans résultat EST le mur anti-robot de
        # DuckDuckGo. Le dire (« throttled ») vaut mieux que « rien trouvé », qui ferait
        # croire à une requête trop précise.
        if not results and (getattr(response, "status_code", 200) or 200) == 202:
            raise EngineFailed(
                "duckduckgo",
                "HTTP 202: throttled (the page came back without a single result)",
            )
        return results

    # -- moteurs à clé ----------------------------------------------------- --
    def _json(self, engine: str, response: Any) -> Dict[str, Any]:
        """Une réponse JSON validée ; la clé ne peut pas fuir dans le message d'erreur."""
        status = getattr(response, "status_code", 200) or 200
        if status >= 400:
            raise EngineFailed(engine, f"HTTP {status}")
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - corps non JSON = réponse illisible
            raise EngineFailed(engine, f"unreadable answer ({type(exc).__name__})") from exc
        if not isinstance(payload, dict):
            raise EngineFailed(engine, "unexpected answer shape")
        return payload

    @staticmethod
    def _http_url(value: Any) -> str:
        url = str(value or "")
        return url if url.startswith(("http://", "https://")) else ""

    def _search_brave(self, key: str, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            response = self._http("get")(
                BRAVE_ENDPOINT,
                params={"q": query, "count": max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": key},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise EngineFailed("brave", f"{type(exc).__name__}: {exc}") from exc
        payload = self._json("brave", response)
        section = payload.get("web") if isinstance(payload.get("web"), dict) else {}
        out: List[Dict[str, str]] = []
        for item in (section or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            url = self._http_url(item.get("url"))
            if not url:
                continue
            out.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "snippet": str(item.get("description") or ""),
                }
            )
        return out

    def _search_tavily(self, key: str, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            response = self._http("post")(
                TAVILY_ENDPOINT,
                json={
                    "api_key": key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise EngineFailed("tavily", f"{type(exc).__name__}: {exc}") from exc
        payload = self._json("tavily", response)
        out: List[Dict[str, str]] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = self._http_url(item.get("url"))
            if not url:
                continue
            out.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "snippet": str(item.get("content") or ""),
                }
            )
        return out

    def _search_searxng(self, base: str, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            response = self._http("get")(
                f"{base}/search",
                params={"q": query, "format": "json"},
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise EngineFailed("searxng", f"{type(exc).__name__}: {exc}") from exc
        payload = self._json("searxng", response)
        out: List[Dict[str, str]] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = self._http_url(item.get("url"))
            if not url:
                continue
            out.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "snippet": str(item.get("content") or ""),
                }
            )
        return out

    # -- appel ------------------------------------------------------------- --
    def _run(self, engine: Dict[str, str], query: str, max_results: int) -> List[Dict[str, str]]:
        kind = engine.get("kind")
        if kind == "brave":
            return self._search_brave(engine["key"], query, max_results)
        if kind == "tavily":
            return self._search_tavily(engine["key"], query, max_results)
        if kind == "searxng":
            return self._search_searxng(engine["url"], query, max_results)
        return self._search_ddg(query, max_results)

    def call(self, name: str, args: dict | None = None) -> dict:
        if name not in {"web_search", "web_image_search"}:
            return {"error": f"unknown tool: {name}"}
        args = dict(args or {})
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        try:
            max_results = min(MAX_RESULTS, max(1, int(args.get("max_results") or MAX_RESULTS)))
        except (TypeError, ValueError):
            return {"error": "max_results must be an integer between 1 and 5"}
        if name == "web_image_search":
            return self.search_images(query, max_results)
        return self.search(query, max_results)

    def search(self, query: str, max_results: int = MAX_RESULTS) -> dict:
        """La chaîne complète : le premier moteur qui rend des résultats gagne.

        Un moteur qui échoue **n'arrête pas** la chaîne : son nom et sa raison sont
        conservés pour le message final. Un moteur qui répond zéro résultat n'est pas
        une panne, mais il n'est pas une réponse non plus — la chaîne continue.
        """
        broken: List[str] = []
        paid = 0
        answered = False
        for engine in self.engines:
            try:
                results = self._run(engine, query, max_results)
            except EngineFailed as exc:
                broken.append(str(exc))
                paid += exc.attempts
                continue
            answered = True
            if results:
                return {
                    "query": query,
                    "results": results[:max_results],
                    "count": min(len(results), max_results),
                    "engine": engine["name"],
                    # Ce qui a échoué avant ne disparaît pas : un opérateur doit savoir
                    # que Brave a refusé, même si Tavily a sauvé la recherche.
                    "failed": broken,
                }
            broken.append(f"{engine['name']}: no result parsed")
        note = "no results: " + " ; ".join(broken)
        if answered:
            return {"query": query, "results": [], "count": 0, "note": note, "failed": broken}
        # Aucun moteur n'a même répondu : le message dit lequel, et pourquoi.
        return {
            "error": "no engine answered: " + " ; ".join(broken),
            "type": "tool_error",
            "tool": "web_search",
            "attempts": max(1, paid),
            "failed": broken,
        }

    # -- images ------------------------------------------------------------ --
    def image_engines(self) -> List[str]:
        """Les moteurs d'images réellement configurables : aujourd'hui, Brave seul.

        Tavily et SearXNG n'exposent pas de recherche d'images dans le contrat utilisé
        ici : les compter comme « moteurs d'images » serait une promesse muette.
        """
        return ["brave"] if self.settings.get("brave") else []

    def _keyless_images(self, query: str, max_results: int) -> dict:
        """Les sources libres, sans clé : Openverse puis Wikimedia Commons.

        Le résultat a la même forme que celui de Brave — `title`, `url` (la page
        source), `image`, `licence`, `author` — pour que l'appelant n'ait qu'un
        seul format à lire, avec ou sans clé.
        """
        from .. import image_search

        found = image_search.search_images(query, limit=max_results, language="fr")
        results = [
            {
                "title": hit.title,
                "url": hit.page,
                "image": hit.thumb,
                "thumbnail": hit.thumb,
                "licence": hit.licence,
                "author": hit.author,
                "domain": hit.domain,
                "provider": hit.provider,
            }
            for hit in found.hits
        ]
        if not results:
            reasons = "; ".join(f"{name}: {reason}" for name, reason in found.failed.items())
            return {
                "query": query,
                "results": [],
                "count": 0,
                "engine": ", ".join(found.engines),
                "note": f"aucune image ({reasons or 'aucun résultat'})",
            }
        return {
            "query": query,
            "results": results,
            "count": len(results),
            "engine": ", ".join(found.engines) or "sans clé",
        }

    def search_images(self, query: str, max_results: int = MAX_RESULTS) -> dict:
        """``/web image`` : un vrai moteur d'images, sinon les sources sans clé.

        Sans clé Brave, Openverse et Wikimedia Commons répondent (mesuré, aucune
        clé) : le refus « aucun moteur d'images » serait faux depuis qu'ils
        existent. Ce qui reste vrai : jamais une recherche web texte à la place
        d'images, et chaque résultat porte sa licence et son auteur.
        """
        query = str(query or "").strip()
        if not query:
            return {"error": "query is required", "type": "tool_error", "tool": "web_image_search"}
        if not self.image_engines():
            return self._keyless_images(query, max_results)
        key = self.settings["brave"]
        try:
            response = self._http("get")(
                BRAVE_IMAGES_ENDPOINT,
                params={"q": query, "count": max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": key},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            return {
                "error": f"brave: {type(exc).__name__}: {exc}",
                "type": "tool_error",
                "tool": "web_image_search",
                "engine": "brave",
            }
        try:
            payload = self._json("brave", response)
        except EngineFailed as exc:
            return {
                "error": str(exc),
                "type": "tool_error",
                "tool": "web_image_search",
                "engine": "brave",
            }
        results: List[Dict[str, str]] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
            image = self._http_url((properties or {}).get("url") or item.get("url"))
            if not image:
                continue
            page = self._http_url(item.get("url"))
            thumbnail = item.get("thumbnail") if isinstance(item.get("thumbnail"), dict) else {}
            results.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": page or image,
                    "image": image,
                    "thumbnail": str((thumbnail or {}).get("src") or ""),
                }
            )
        if not results:
            return {
                "query": query,
                "results": [],
                "count": 0,
                "engine": "brave",
                "note": "brave: no image parsed",
            }
        return {
            "query": query,
            "results": results[:max_results],
            "count": min(len(results), max_results),
            "engine": "brave",
        }


__all__ = [
    "WebSearchToolset",
    "parse_results",
    "engine_chain",
    "search_settings",
    "ENGINE_ORDER",
    "DDG_ENDPOINT",
    "BRAVE_ENDPOINT",
    "BRAVE_IMAGES_ENDPOINT",
    "TAVILY_ENDPOINT",
    "MAX_RESULTS",
]
