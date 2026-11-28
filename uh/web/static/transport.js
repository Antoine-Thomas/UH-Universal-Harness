// Le transport HTTP de l'interface, sorti de `app.js` (P2-11).
//
// Frontière : ce module ne touche pas au DOM. Il ne sait qu'une chose du reste de
// la page — comment signaler un jeton périmé — et elle lui est passée. C'est ce
// qui rend la règle testable : `api()` ne peut pas décider de vider l'écran.

export function createTransport({ onAuthLost, httpErrorText }) {
  // Un 401 est un fait sur le cookie, pas un ordre de vider l'écran : la page
  // décide quoi en faire. Le module se contente de l'appeler, s'il est branché.
  function signalExpired() {
    if (typeof onAuthLost === "function") onAuthLost();
  }

  // Like api(), but a refusal keeps its body: these routes answer {error, hint,
  // kind} on purpose, and throwing away the hint would throw away the fix.
  async function apiResult(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) {
      signalExpired();
      return { ok: false, status: 401, body: {} };
    }
    return { ok: response.ok, status: response.status, body };
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (response.status === 401) {
      signalExpired();
      throw new Error("jeton périmé : cette page n'est plus reliée à UH");
    }
    if (response.status === 404) {
      // The interface knows a route this server does not: an older `uh web` is
      // still holding the port, and the browser got the newer files from disk.
      // "Not Found" told the user nothing; this tells them what to do.
      throw new Error(
        "ce serveur UH ne connaît pas cette route : une ancienne instance occupe encore le port. Ferme la fenêtre et relance `uh web`."
      );
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || httpErrorText(response.status));
    }
    return response.json();
  }

  return { api, apiResult };
}
