// UH web front-end — original code, ES module, no build step, no third party.
// Served under a strict CSP: no inline script, no inline style, no eval.

import { renderMarkdown } from "./markdown.js";

const state = {
  sessionId: null,
  streaming: false,
  abort: null,
  commands: [],
  paletteIndex: 0,
  filtered: [],
  attachments: [],
  activeTab: "keys",
  keyEntryAllowed: false,
  //: In-flight providers request, shared by the ⚙ button, the keys tab and
  //: `?panel=keys`: the three entry points used to render the same list three
  //: times, last answer winning.
  providersPromise: null,
  presets: null,
  skill: null,
  llm: { enabled: false, values: {} },
  llmDefaults: { enabled: false, values: {} },
  llmSpec: null,
  //: The model that actually answered (point 5): one value, read by the answer's
  //: footer, the header's tooltip and the local-model tab.
  servedModel: "",
  requestedModel: "",
  //: The active provider/model, as the server reports them: the capability check
  //: must ask about the model that will actually receive the image.
  kind: "",
  model: "",
};

const $ = (id) => document.getElementById(id);

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
    showSessionBanner();
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
    showSessionBanner();
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

// A 401 is a fact about the cookie, not an order to delete the interface. An older
// tab talking to a restarted (or a second) `uh web` used to lose the whole page -
// transcript, composer and draft included - on any api() call. It now says so in a
// banner and leaves everything in place.
function showSessionBanner() {
  const box = $("app-banner");
  if (!box || !box.hidden) return;
  box.textContent = "";
  box.appendChild(el("strong", null, "Jeton périmé. "));
  box.appendChild(
    document.createTextNode(
      "Cette page n'est plus authentifiée : UH a redémarré, ou une autre instance a " +
        "pris le cookie. Ton brouillon est gardé. Ouvre l'URL imprimée par `uh web` " +
        "dans un autre onglet, ou relance `uh web` : cette page n'est plus reliée."
    )
  );
  box.hidden = false;
}

function showAppError(text) {
  const box = $("app-banner");
  if (!box) return;
  box.textContent = text;
  box.hidden = false;
}

// --------------------------------------------------------------- rendering --
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function deleteMessage(box) {
  const messageId = box.dataset.messageId;
  if (!state.sessionId || !messageId) return;
  if (!window.confirm("Supprimer ce message ? Le reste de la conversation est conservé.")) return;
  await api(
    `/api/sessions/${encodeURIComponent(state.sessionId)}/messages/${encodeURIComponent(messageId)}`,
    { method: "DELETE" }
  );
  box.remove();
  // The gauge is an estimate of what the next turn will send; it refreshes with
  // the next message. Zeroing it here would be a second, wrong estimate.
}

// A message id is optional: live tokens arrive before the row exists in the
// store, and a bubble you cannot name is a bubble you cannot delete.
// Two thumbs on a text answer: same route, same journal, same honest sentence as an
// image. The vote carries the message id so the journal can point at what was read.
function textFeedback(box) {
  const bar = el("div", "text-feedback");
  const status = el("span", "tool-artifact-status");
  const buttons = [];
  for (const [vote, label, title] of [
    ["up", "Utile", "réponse utile : notée comme exemple à reproduire"],
    ["down", "Pas utile", "réponse à éviter : notée comme contre-exemple"],
  ]) {
    const button = el("button", "tool-artifact-btn", label);
    button.type = "button";
    button.title = title;
    button.setAttribute("aria-label", title);
    button.dataset.vote = vote;
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", async () => {
      const current = bar.dataset.vote || "";
      const wanted = current === vote ? "none" : vote;
      status.textContent = "…";
      try {
        const response = await fetch("/api/artifacts/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "rate",
            vote: wanted,
            session_id: state.sessionId || "",
            message_id: box.dataset.messageId || null,
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || httpErrorText(response.status));
        bar.dataset.vote = wanted === "none" ? "" : wanted;
        for (const node of buttons) {
          const active = Boolean(bar.dataset.vote) && node.dataset.vote === bar.dataset.vote;
          node.setAttribute("aria-pressed", active ? "true" : "false");
          node.classList.toggle("voted", active);
        }
        status.textContent = wanted === "none" ? "vote retiré" : VOTE_NOTE;
      } catch (error) {
        status.textContent = `refusé : ${error.message}`;
      }
    });
    buttons.push(button);
    bar.appendChild(button);
  }
  bar.appendChild(status);
  box.appendChild(bar);
  return bar;
}

// Read an answer aloud. The synthesis is what creates the sound, so the player only
// appears once a file really exists - the conditional rule, applied to its origin.
function speakBox(box, text) {
  const bar = el("div", "answer-audio");
  const button = el("button", "tool-artifact-btn", "Écouter");
  button.type = "button";
  button.title = "lire cette réponse à voix haute";
  button.setAttribute("aria-label", "lire cette réponse à voix haute");
  button.addEventListener("click", async () => {
    if (button.disabled) return;
    button.disabled = true;
    const status = el("span", "tool-artifact-status", "synthèse…");
    bar.appendChild(status);
    try {
      const payload = await api("/api/audio/speak", {
        method: "POST",
        body: JSON.stringify({
          text: String(text || "").slice(0, 2000),
          session_id: state.sessionId || "",
        }),
      });
      status.remove();
      drawAudio(box, [payload], { sessionId: state.sessionId });
      bar.remove();
    } catch (error) {
      status.textContent = `voix impossible : ${error.message}`;
      button.disabled = false;
    }
  });
  bar.appendChild(button);
  box.appendChild(bar);
}

function appendMessage(role, text, messageId) {
  const box = el("div", `msg ${role}`);
  if (messageId !== undefined && messageId !== null) {
    box.dataset.messageId = messageId;
    const kill = el("button", "message-delete", "×");
    kill.title = "Supprimer ce message";
    kill.setAttribute("aria-label", "Supprimer le message");
    kill.addEventListener("click", () => deleteMessage(box));
    box.appendChild(kill);
  }
  box.appendChild(el("span", "role", role));
  const content = el("div", "content");
  // Assistant answers are Markdown-rendered (escape-first, see markdown.js);
  // user and system text stays literal so it always shows exactly what was typed.
  if (role === "assistant") {
    content.classList.add("md");
    content.innerHTML = renderMarkdown(text);
  } else {
    content.textContent = text;
  }
  box.appendChild(content);
  if (role === "assistant") {
    // Point 5 generalised: a text answer deserves the same two thumbs as an image,
    // and the same honest sentence about what a vote does today.
    textFeedback(box);
    // P1-15: read the answer aloud. The player appears only once a sound exists.
    speakBox(box, text);
  }
  $("messages").appendChild(box);
  $("messages").scrollTop = $("messages").scrollHeight;
  return box;
}

// --- erreurs lisibles ---------------------------------------------------------- //
// A raw code ("HTTP 422", "WinError 10054") tells a non-developer nothing and offers
// no way out. Every failure shown in the thread goes through here: a sentence in
// French, the technical text kept in a collapsible detail, and the one button that
// fixes the usual cause. Messages already written for people pass through untouched.
//
// Two unrelated failures both arrive as 401, and the repair is opposite for each:
// UH's own /api routes answer 401 when the page's token is stale (older tab, server
// restarted on another port) - reloading is the fix; a model provider answers 401
// inside a turn's `error` frame when it refuses the API key - reloading changes
// nothing, the key has to be checked. `origin` is what tells them apart: "ui" for a
// request the browser made to UH, "provider" for a failure reported by the turn.
const HTTP_TEXTS = {
  400: "La demande n'a pas été comprise par UH. Reformulez-la ou rechargez la page.",
  401: "Cette page n'est plus reliée à UH (jeton périmé). Rechargez la page.",
  403: "UH a refusé cette action pour des raisons de sécurité. Rechargez la page, puis réessayez.",
  404: "Cet élément n'existe plus ou le serveur UH est une ancienne version. Rechargez la page.",
  408: "Le serveur a mis trop de temps à répondre. Réessayez dans un instant.",
  413: "Le contenu envoyé est trop volumineux. Réduisez-le ou joignez un fichier plus petit.",
  422: "UH n'a pas pu traiter cette demande : la conversation est peut-être dans un état invalide. Une nouvelle session règle généralement le problème.",
  429: "Trop de demandes ou quota du fournisseur atteint. Patientez un peu ou changez de modèle.",
  500: "UH ou le fournisseur de modèle a rencontré un problème interne. Réessayez ; sinon changez de modèle.",
  502: "Le fournisseur de modèle ne répond pas correctement. Réessayez dans un instant.",
  503: "Le service est momentanément indisponible. Réessayez dans un instant.",
  504: "Le fournisseur de modèle a mis trop de temps à répondre. Réessayez dans un instant.",
};

// A provider refusal of the key is not UH refusing the page: same code, other text.
const HTTP_TEXTS_PROVIDER = {
  401: "Le fournisseur a refusé la clé. Vérifiez la clé de ce fournisseur dans le panneau « Fournisseurs », puis réessayez : recharger la page ne changerait rien.",
};

function httpErrorText(status, origin = "ui") {
  const code = Number(status) || 0;
  if (origin === "provider" && HTTP_TEXTS_PROVIDER[code]) return HTTP_TEXTS_PROVIDER[code];
  const known = HTTP_TEXTS[code];
  if (known) return known;
  if (code >= 500) return HTTP_TEXTS[500];
  return "UH n'a pas pu terminer cette action. Rechargez la page, puis réessayez.";
}

// -> { text, actions[] } ; actions among "reload", "new", "allow", "keys".
// `origin` is "ui" for a failure of a browser request to UH, "provider" when the
// turn itself reported it (SSE `error` frame: the model or a service it called).
function humanizeError(raw, origin = "ui") {
  const message = String(raw || "").trim();
  const http = message.match(/\bHTTP\s*(\d{3})\b/i);
  if (/WinError\s*100(53|54|60|61)|Failed to fetch|NetworkError|Load failed|ECONNRESET|connection (reset|refused|aborted)|socket|BrokenPipe/i.test(message)) {
    return {
      text: "La connexion a été coupée avant la fin de la réponse. UH ou le fournisseur est peut-être redémarré ou injoignable.",
      actions: ["reload"],
    };
  }
  if (/timed? ?out|timeout|délai/i.test(message) && !http) {
    return { text: "Le fournisseur a mis trop de temps à répondre. Réessayez dans un instant.", actions: ["reload"] };
  }
  if (/read-?only|lecture seule|not available in this mode|mode (chat|review|observe)/i.test(message)) {
    return {
      text: "Cet outil exige le mode Atelier, mais le second cerveau est en lecture seule. Rien n'a été exécuté.",
      actions: ["allow"],
    };
  }
  if (http) {
    const code = Number(http[1]);
    if (origin === "provider" && code === 401) {
      // The key itself was refused: a reload would only replay the same key. The
      // repair is a button to the panel that holds it, not a page refresh.
      return { text: httpErrorText(code, "provider"), actions: ["keys"] };
    }
    const actions = code === 422 ? ["new", "reload"] : ["reload"];
    return { text: httpErrorText(code, origin), actions };
  }
  // api() throws this wording on a local 401, before any HTTP code is visible.
  if (origin === "ui" && /jeton p[ée]rim[ée]|invalid token/i.test(message)) {
    return { text: HTTP_TEXTS[401], actions: ["reload"] };
  }
  return { text: message || "erreur inconnue", actions: [] };
}

function errorButton(label, title, onClick) {
  const button = el("button", "notice-action", label);
  button.type = "button";
  button.title = title;
  button.addEventListener("click", onClick);
  return button;
}

// Adds the technical detail (collapsed) and the action buttons under a failed bubble.
function decorateError(box, raw, info) {
  const details = el("details", "error-detail");
  details.appendChild(el("summary", null, "Détails techniques"));
  details.appendChild(el("code", null, String(raw || "")));
  box.appendChild(details);
  const row = el("div", "error-actions");
  for (const action of info.actions || []) {
    if (action === "reload") {
      row.appendChild(errorButton("Recharger", "Recharger la page : la conversation est conservée", () => window.location.reload()));
    } else if (action === "new") {
      row.appendChild(
        errorButton("Nouvelle session", "Repartir d'une conversation vide", () => {
          newSession().catch((error) => modelNote(error.message, "warn"));
        })
      );
    } else if (action === "keys") {
      // The provider refused the key: the only useful gesture is opening the panel
      // that holds it, and it must exist as a button - not as advice to type a URL.
      row.appendChild(
        errorButton("Vérifier la clé", "Ouvrir « Fournisseurs » pour vérifier la clé refusée", () =>
          showPanelTab("keys")
        )
      );
    } else if (action === "allow") {
      row.appendChild(
        errorButton("Autoriser l'outil", "Passer en mode Atelier (exécution de code dans le dossier du run)", async (event) => {
          event.currentTarget.disabled = true;
          try {
            await applyMode("dev");
            event.currentTarget.textContent = "Mode Atelier actif : renvoyez votre message";
          } catch (error) {
            event.currentTarget.disabled = false;
            modelNote(error.message, "warn");
          }
        })
      );
    }
  }
  if (row.childNodes.length) box.appendChild(row);
}

// --- cartes d'outils : quatre états distincts ---------------------------------- //
const TOOL_STATE_TEXT = { loading: "en cours…", success: "✓ terminé", refused: "✕ refusé", error: "⚠ erreur" };
const TOOL_SLOW_MS = 3000;

function setToolState(card, toolState, raw) {
  card.dataset.state = toolState;
  card.setAttribute("aria-busy", String(toolState === "loading"));
  const label = card.querySelector(".state");
  if (label) label.textContent = TOOL_STATE_TEXT[toolState] || toolState;
  card.classList.toggle("error", toolState === "error" || toolState === "refused");
  card.classList.toggle("refused", toolState === "refused");
  if (card._slowTimer) {
    window.clearTimeout(card._slowTimer);
    card._slowTimer = null;
  }
  for (const old of card.querySelectorAll(".tool-slow, .tool-why")) old.remove();
  if (toolState === "loading") {
    card._slowTimer = window.setTimeout(() => {
      if (card.dataset.state !== "loading") return;
      const slow = el("span", "tool-slow", "Toujours en cours… certains outils prennent quelques secondes.");
      slow.setAttribute("role", "status");
      card.insertBefore(slow, card.querySelector("pre"));
    }, TOOL_SLOW_MS);
  } else if (toolState === "refused") {
    const why = el("div", "tool-why");
    why.appendChild(el("span", "tool-why-badge", "Mode lecture seule (chat)"));
    why.appendChild(
      el("span", null, " Ce mode bloque l'exécution de code. Passez en mode Atelier pour autoriser cet outil.")
    );
    const link = el("button", "tool-why-link", "Changer de mode");
    link.type = "button";
    link.addEventListener("click", () => {
      const badge = $("mode-badge");
      if (badge) {
        badge.scrollIntoView({ block: "nearest" });
        badge.focus();
      }
    });
    why.appendChild(link);
    card.insertBefore(why, card.querySelector("pre"));
  }
}

function classifyToolResult(content) {
  const text = String(content || "");
  if (!/"error"\s*:/.test(text)) return "success";
  return /not available in this mode|read-?only|lecture seule|refus|denied|not allowed|mode (chat|review|observe)/i.test(text)
    ? "refused"
    : "error";
}

function toolCard(name, args, result) {
  const card = el("details", "tool-card");
  const summary = el("summary");
  summary.appendChild(document.createTextNode(name || "tool"));
  const stateLabel = el("span", "state");
  stateLabel.setAttribute("aria-live", "polite");
  summary.appendChild(stateLabel);
  card.appendChild(summary);
  const payload = el("pre", null, JSON.stringify({ args, result }, null, 2));
  card.appendChild(payload);
  setToolState(card, result === undefined ? "loading" : classifyToolResult(result));
  $("messages").appendChild(card);
  $("messages").scrollTop = $("messages").scrollHeight;
  return card;
}

// A remote MCP tool never runs on its own: the turn pauses here until a person
// answers. No answer inside `timeout` is a refusal, so a closed window cannot
// turn into a silent execution.
function approvalCard(data) {
  const card = el("div", "tool-card approval-card");
  card.setAttribute("role", "group");
  card.setAttribute("aria-label", `Autorisation demandée pour ${data.tool}`);
  const head = el("p", null, "");
  head.appendChild(el("strong", null, "Autorisation demandée. "));
  head.appendChild(
    document.createTextNode(
      `Le modèle veut appeler « ${data.tool} » sur le serveur distant « ${data.server} ». Rien n'a encore été exécuté.`
    )
  );
  card.appendChild(head);
  const payload = el("pre", null, JSON.stringify(data.arguments || {}, null, 2));
  card.appendChild(payload);

  const actions = el("div", "tool-actions");
  const allow = el("button", "data-button primary", "Autoriser");
  const always = el("button", "data-button", "Toujours autoriser ce serveur");
  const deny = el("button", "data-button", "Refuser");
  allow.type = "button";
  always.type = "button";
  deny.type = "button";
  always.title = "Vaut pour cette session : redémarrer UH redemande l'accord";
  actions.appendChild(allow);
  actions.appendChild(always);
  actions.appendChild(deny);
  card.appendChild(actions);
  $("messages").appendChild(card);
  $("messages").scrollTop = $("messages").scrollHeight;

  const answer = async (granted, forever = false) => {
    allow.disabled = true;
    always.disabled = true;
    deny.disabled = true;
    try {
      await api(`/api/mcp/approvals/${encodeURIComponent(data.id)}`, {
        method: "POST",
        body: JSON.stringify({ allow: granted, always: forever }),
      });
      actions.textContent = !granted
        ? "Refusé : rien n'a été exécuté."
        : forever
          ? `Autorisé pour cette session : « ${data.server} » ne redemandera plus.`
          : "Autorisé.";
    } catch (error) {
      actions.textContent = `Cette demande n'est plus valable (${error.message}).`;
    }
  };
  allow.addEventListener("click", () => answer(true));
  always.addEventListener("click", () => answer(true, true));
  deny.addEventListener("click", () => answer(false));
  if (data.timeout) {
    setTimeout(() => {
      if (!allow.disabled) {
        allow.disabled = true;
        always.disabled = true;
        deny.disabled = true;
        actions.textContent = "Délai dépassé : la demande a expiré, rien n'a été exécuté.";
      }
    }, Number(data.timeout) * 1000);
  }
  return card;
}

//: What an `<img>` may be pointed at. `svg` is absent on purpose: it is a document
//: with scripts, not a bitmap (same list as `uh/mcp.py::ARTIFACT_MIME`).
const ARTIFACT_MIME = ["image/png", "image/jpeg", "image/gif", "image/webp"];

// The bytes arrive with the message (tool payload or answer), so a reloaded
// session renders them with no second request. Nothing here builds markup from
// data: a data URL is data, and the only sink that cannot execute it is img.src,
// after a whitelist check on the prefix - the MIME in the payload is a claim,
// not a proof. The server refuses remote URLs; the page never fetches one.
function drawArtifacts(container, artifacts, context) {
  const list = Array.isArray(artifacts) ? artifacts : [];
  for (const artifact of list) {
    const mime = String((artifact && artifact.mime) || "");
    const url = String((artifact && artifact.data_url) || "");
    if (!ARTIFACT_MIME.includes(mime) || !url.startsWith(`data:${mime};base64,`)) continue;
    const figure = el("figure", "tool-artifact");
    const img = el("img", "tool-artifact-img");
    img.src = url;
    img.alt = `${mime}, ${Number(artifact.bytes || 0)} octets`;
    img.loading = "lazy";
    // A real loading state: a placeholder frame until the bytes decode, a sentence
    // if they never do - never a collapsed, silent hole in the thread.
    figure.classList.add("loading");
    figure.setAttribute("aria-busy", "true");
    img.addEventListener("load", () => {
      figure.classList.remove("loading");
      figure.setAttribute("aria-busy", "false");
    });
    img.addEventListener("error", () => {
      figure.classList.remove("loading");
      figure.classList.add("broken");
      figure.setAttribute("aria-busy", "false");
      figure.appendChild(el("div", "tool-artifact-note", "Cette image n'a pas pu être affichée."));
    });
    figure.appendChild(img);
    if (artifact.note && !String(artifact.note).startsWith("http")) {
      figure.appendChild(el("figcaption", "tool-artifact-note", String(artifact.note)));
    }
    artifactToolbar(figure, artifact, context);
    if (!figure.querySelector(".tool-artifact-actions")) {
      const solo = artifactOpen(artifact);
      if (solo) {
        const bar = el("div", "tool-artifact-actions");
        bar.appendChild(solo);
        figure.appendChild(bar);
      }
    }
    container.appendChild(figure);
  }
  if (list.some((artifact) => artifact && artifact.omitted)) {
    container.appendChild(
      el("div", "tool-artifact-note", "image non conservée : au-delà du plafond de taille")
    );
  }
}

// ------------------------------------------------------------------ figures --
//: How long « Annuler » stays clickable after a trash: long enough to catch a
//: misclick, short enough that the thread keeps no ghost row for ever.
const TRASH_UNDO_MS = 8000;
//: What a vote means, in the operator's words (point 5). Until P1-13 connects the
//: journal to the brain, "the model learns from this" would be a lie.
const VOTE_NOTE = "noté (pas encore utilisé pour s'améliorer)";

// One status line per figure. The bug this replaces was two inline <span> notes
// glued into one word - "notée utilegardée" - because every action appended its
// own note and nothing separated them.
function figureStatus(figure) {
  let line = figure.querySelector(".tool-artifact-status");
  if (!line) {
    line = el("span", "tool-artifact-status");
    figure.appendChild(line);
  }
  return line;
}

function say(figure, text) {
  figureStatus(figure).textContent = text;
}

// The four things a figure accepts. The page sends the artifact's own provenance;
// the SERVER decides what may be touched (uh/artifacts.py: the file must resolve
// inside UHB's runs root). A refusal is shown verbatim, never swallowed.
async function artifactAction(button, figure, body) {
  if (button) button.disabled = true;
  say(figure, "…");
  try {
    const response = await fetch("/api/artifacts/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) throw new Error(data.detail || httpErrorText(response.status));
    if (button) button.disabled = false;
    return data;
  } catch (error) {
    say(figure, `refusé : ${error.message}`);
    if (button) button.disabled = false;
    return null;
  }
}

// Votes are exclusive and reversible: one image carries one opinion. Clicking the
// active thumb retracts it (`vote: "none"`, journalled) instead of recording the
// same vote twice, and the other thumb is unmarked in the same gesture.
async function voteOn(button, figure, base, vote) {
  const current = figure.dataset.vote || "";
  const wanted = current === vote ? "none" : vote;
  const data = await artifactAction(button, figure, { ...base, action: "rate", vote: wanted });
  if (!data) return;
  figure.dataset.vote = wanted === "none" ? "" : wanted;
  for (const node of figure.querySelectorAll(".tool-artifact-btn[data-vote]")) {
    const active = Boolean(figure.dataset.vote) && node.dataset.vote === figure.dataset.vote;
    node.setAttribute("aria-pressed", active ? "true" : "false");
    node.classList.toggle("voted", active);
  }
  say(figure, wanted === "none" ? "vote retiré" : VOTE_NOTE);
}

// Trash takes the image out of the thread at once, with a way back for a few
// seconds: the file is in the bin (already reversible), and « Annuler » restores
// it where it came from before the window closes.
async function trashWithUndo(button, figure, base) {
  const data = await artifactAction(button, figure, { ...base, action: "trash" });
  if (!data) return;
  figure.classList.add("trashed");
  const line = figureStatus(figure);
  line.textContent = "dans la corbeille";
  const undo = el("button", "tool-artifact-undo", "Annuler");
  undo.type = "button";
  line.appendChild(document.createTextNode(" "));
  line.appendChild(undo);
  let closed = false;
  const timer = window.setTimeout(() => {
    closed = true;
    line.textContent = "dans la corbeille (récupérable jusqu'au vidage)";
  }, TRASH_UNDO_MS);
  undo.addEventListener("click", async () => {
    if (closed) return;
    closed = true;
    window.clearTimeout(timer);
    undo.disabled = true;
    const restored = await artifactAction(null, figure, { ...base, action: "restore" });
    if (!restored) return;
    figure.classList.remove("trashed");
    line.textContent = "restaurée";
  });
}

// Icons drawn inline: an emoji renders at whatever size the platform decides and
// reads as text next to a real button. `currentColor` keeps them on theme.
const ARTIFACT_ICONS = {
  keep: ["M6 3h12v18l-6-4.2L6 21z"],
  trash: ["M4 7h16", "M9.5 7V4.8h5V7", "M6.5 7l1 13.2h9L17.5 7"],
  up: ["M7 21V10l4.5-7L13.8 4v6H19a2 2 0 0 1 2 2.3l-1.4 8.7z"],
  down: ["M17 3v11l-4.5 7L10.2 20v-6H5a2 2 0 0 1-2-2.3L4.4 3z"],
  download: ["M12 3.5v11", "M12 14.5l4-4", "M12 14.5l-4-4", "M4.5 20.5h15"],
};

function artifactIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add("icon");
  // Real nodes rather than parsed markup: the page renders server data
  // elsewhere, and one HTML-parsing sink here is how a data URL becomes markup.
  for (const d of ARTIFACT_ICONS[name] || []) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  return svg;
}

// One button: icon, short French label, tooltip. The label is the accessible name,
// so an icon-only button never becomes a guess for a screen reader or a newcomer.
function artifactButton(icon, label, title, onClick, extraClass) {
  const node = el("button", "tool-artifact-btn" + (extraClass ? " " + extraClass : ""));
  node.type = "button";
  node.title = title;
  node.setAttribute("aria-label", title);
  node.appendChild(artifactIcon(icon));
  node.appendChild(el("span", "tool-artifact-label", label));
  if (onClick) node.addEventListener("click", onClick);
  return node;
}

// Opens the picture full size in a new tab. A data: URL cannot be navigated to by
// the browser (top-level data: is blocked), so the bytes become a blob first; the
// prefix was already whitelisted by drawArtifacts. Nothing is fetched.
function artifactOpen(artifact) {
  const url = String((artifact && artifact.data_url) || "");
  const mime = String((artifact && artifact.mime) || "");
  const marker = `data:${mime};base64,`;
  if (!ARTIFACT_MIME.includes(mime) || !url.startsWith(marker)) return null;
  const node = artifactButton("download", "Ouvrir", "ouvrir l'image en grand dans un nouvel onglet", null);
  node.addEventListener("click", () => {
    try {
      const raw = atob(url.slice(marker.length));
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
      const blobUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
      window.open(blobUrl, "_blank", "noopener");
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
    } catch (error) {
      node.disabled = true;
      node.title = "image illisible";
    }
  });
  return node;
}
function artifactDownload(artifact) {
  const source = artifact && artifact.source;
  if (!source || !artifact.data_url) return null;
  const link = document.createElement("a");
  link.className = "tool-artifact-btn";
  link.href = artifact.data_url;
  link.download = String(source.relative || "image").split(/[\\/]/).pop() || "image.png";
  link.title = "télécharger cette image";
  link.setAttribute("aria-label", `télécharger ${link.download}`);
  link.appendChild(artifactIcon("download"));
  link.appendChild(el("span", "tool-artifact-label", "Télécharger"));
  return link;
}

function artifactToolbar(figure, artifact, context) {
  const source = artifact && artifact.source;
  // No provenance, no button: an image whose file is unknown gets no action that
  // would promise to delete something.
  if (!source || (!source.path && !source.relative)) return;
  const bar = el("div", "tool-artifact-actions");
  const base = {
    source,
    session_id: (context && context.sessionId) || "",
    // P1-13 needs to know what it is keeping: a voice clip is copied to FLAC, a
    // picture to PNG. The artifact already carries its kind; this only forwards it.
    kind: String(artifact.kind || "").toLowerCase() === "audio" ? "audio" : "image",
  };
  const add = (icon, label, title, extraClass) => {
    const node = artifactButton(icon, label, title, null, extraClass);
    bar.appendChild(node);
    return node;
  };
  const keep = add("keep", "Garder", "garder cette image : elle échappe au nettoyage", "safe");
  keep.addEventListener("click", async () => {
    const data = await artifactAction(keep, figure, { ...base, action: "keep" });
    if (data) say(figure, "gardée");
  });
  for (const [vote, label, title] of [
    ["up", "Utile", "rendu réussi : noté comme exemple à reproduire"],
    ["down", "Pas utile", "rendu raté : noté comme contre-exemple à éviter"],
  ]) {
    const node = add(vote, label, title);
    node.dataset.vote = vote;
    node.setAttribute("aria-pressed", "false");
    node.addEventListener("click", () => voteOn(node, figure, base, vote));
  }
  const opener = artifactOpen(artifact);
  if (opener) bar.appendChild(opener);
  const link = artifactDownload(artifact);
  if (link) bar.appendChild(link);
  const trash = add("trash", "Corbeille", "retirer cette image du fil (récupérable quelques secondes)", "danger");
  trash.addEventListener("click", () => trashWithUndo(trash, figure, base));
  figure.appendChild(bar);
}

function renderArtifacts(card, content) {
  let payload;
  try {
    payload = JSON.parse(content || "");
  } catch (error) {
    return;
  }
  drawArtifacts(card, payload && payload.artifacts);
}

function fillToolCard(card, content) {
  card.querySelector("pre").textContent = content;
  setToolState(card, classifyToolResult(content));
  renderArtifacts(card, content);
}

function renderHistory(messages) {
  $("messages").textContent = "";
  // A tool call and its result are two rows in the store: pair them, or the
  // replay shows "running…" followed by a second card holding the result.
  let pending = [];
  for (const message of messages) {
    const role = message.role || "assistant";
    if (role === "assistant" && (message.tool_calls || []).length) {
      pending = message.tool_calls.map((call) => toolCard(call.function?.name, call.function?.arguments, undefined));
      if (message.content) appendMessage("assistant", message.content);
    } else if (role === "tool") {
      const card = pending.shift();
      if (card) fillToolCard(card, message.content || "");
      else toolCard(message.name || "tool", null, message.content || "");
    } else {
      const box = appendMessage(role, message.content || "", message.id);
      if (role === "user" && message.turn) turnBadge(box, message.turn);
      if (Array.isArray(message.artifacts) && message.artifacts.length) {
        drawArtifacts(box, message.artifacts);
        drawAudio(box, message.artifacts, { sessionId: state.sessionId });
      }
    }
  }
}

function updateGauge(messages) {
  const characters = messages.reduce((sum, m) => sum + (m.content || "").length, 0);
  const tokens = Math.max(1, Math.round(characters / 4));
  const percent = Math.min(100, (tokens / 8000) * 100);
  $("gauge-fill").style.width = `${percent}%`;
  $("gauge-label").textContent = `contexte ~${tokens} tok`;
}

// ------------------------------------------------------------------ status --
async function loadStatus() {
  // The session's own pair when there is one: a session can run on a different model
  // than the global default, and the header must not announce the other one.
  const query = state.sessionId ? `?session_id=${encodeURIComponent(state.sessionId)}` : "";
  const status = await api(`/api/status${query}`);
  // Version only. "ollama · v1.5.1" read as "Ollama is required", which is false:
  // the provider is a choice, made in Configuration, and a user who runs a cloud
  // key should never be told a local model server is part of the deal.
  const provider = String(status.provider || "").trim();
  state.kind = provider;
  state.model = String(status.model || "").trim();
  const label = $("status-provider");
  label.textContent = `v${status.version}`;
  label.title = provider
    ? `UH ${status.version} · fournisseur : ${provider}${status.model ? ` · modèle : ${status.model}` : ""}`
    : `UH ${status.version} · aucun fournisseur configuré`;
  $("terminal-warning").hidden = !status.terminal;
  // Which copy of UH is answering. "The fixes never reach me" was a two-install
  // problem, and the interface said nothing about it.
  const install = status.install || {};
  const badge = $("install-badge");
  if (badge) {
    const kind = install.origin || (install.editable ? "dépôt" : "paquet installé");
    // Origine, commit et salissure en clair: c'est ce qui distingue « le correctif est
    // dans le depot mais ce n'est pas lui qui tourne » de « le correctif n'existe pas ».
    const sha = install.commit ? ` · ${install.commit}` : "";
    const dirty = install.dirty ? " · modifié" : "";
    badge.textContent = `${kind}${sha}${dirty} · ${status.version}`;
    badge.title = install.package
      ? `${kind} : ${install.package}\npython : ${install.python || "?"}\nracine : ${install.root || "?"}`
      : "installation inconnue";
    badge.classList.toggle("warn", Boolean(install.editable) === false);
    badge.classList.toggle("dirty", Boolean(install.dirty));
  }
  // The interface is read from disk on every request, the routes are fixed at
  // import time: two versions can meet, and that is exactly how a button ends up
  // calling a route its server has never heard of.
  const pageVersion = String(document.body.dataset.uhVersion || "");
  const servedVersion = String(status.version || "");
  // What this script was served with. The page and the bundle can disagree after a
  // restart (a cached app.js keeps running code the server no longer serves), and
  // that is the state where a button calls a route that is not there.
  let bundleBuild = "";
  try {
    bundleBuild = new URL(import.meta.url).searchParams.get("v") || "";
  } catch (error) {
    bundleBuild = "";
  }
  const warning = $("version-warning");
  if (warning) {
    const stalePage = Boolean(pageVersion && servedVersion && pageVersion !== servedVersion);
    const staleBundle = Boolean(bundleBuild && servedVersion && bundleBuild !== servedVersion);
    warning.hidden = !stalePage && !staleBundle;
    if (stalePage) {
      warning.textContent =
        `Interface ${pageVersion} servie par un serveur ${servedVersion} : une ancienne instance ` +
        "occupe encore le port. Ferme cette fenêtre et relance « uh web ».";
    } else if (staleBundle) {
      warning.textContent =
        `UH a été mis à jour (interface ${bundleBuild}, serveur ${servedVersion}) : ` +
        "recharge la page pour charger le code du serveur.";
    }
  }
  if (status.provider_error) {
    // Say it once, in the tooltip: a missing provider is a configuration fact,
    // not a permanent red banner over a UI that works without one.
    label.title += " · fournisseur indisponible";
  }
}

// Which models the header offers. Both categories are visible by default: the one
// distinction that matters (a paid API vs a local model) and the provider name were
// hidden behind a flat list of ids. The toggle now REDUCES the menu to the current
// model instead of being the only way to see the rest.
//: Which boot steps failed: shown once, so a partial start is visible instead of
//: looking like a working app with empty panels.
const bootFailures = [];
const MODELS_ADVANCED_KEY = "uh.models.advanced";
//: The header's last choice, kept in the browser as well as in config.toml: the
//: server write can be refused (a hand-edited config), and a restart that forgets
//: the model is the bug this pair exists to prevent.
const MODEL_CHOICE_KEY = "uh.selected_model";

function storedModelChoice() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(MODEL_CHOICE_KEY) || "null");
    if (!raw || !raw.provider || !raw.model) return null;
    return { provider: String(raw.provider), model: String(raw.model) };
  } catch (error) {
    return null;
  }
}

function storeModelChoice(provider, model) {
  try {
    window.localStorage.setItem(
      MODEL_CHOICE_KEY,
      JSON.stringify({ provider, model, at: new Date().toISOString() })
    );
  } catch (error) {
    /* a private window has no storage: config.toml still carries the choice */
  }
}

function modelsAdvanced() {
  try {
    const stored = window.localStorage.getItem(MODELS_ADVANCED_KEY);
    // Unset means the full list: the menu is a chooser, and a chooser that shows
    // one option is a label with a chevron.
    return stored === null ? true : stored === "1";
  } catch (error) {
    return false;
  }
}

// Which engine serves this id: the header has to say it, because the same name
// can exist on two providers and "faster" is not "smarter".
const PROVIDER_LABEL = {
  ollama: "local (Ollama)",
  deepseek: "API · DeepSeek",
  huggingface: "API · Hugging Face",
  cloudflare: "API · Cloudflare",
  omnirouter: "API · OmniRoute",
  router: "API · router",
};

function providerLabel(provider, local) {
  if (provider === "ollama" || local) return PROVIDER_LABEL.ollama;
  return PROVIDER_LABEL[provider] || `API · ${provider}`;
}

function modelNote(text, tone = "info") {
  const box = $("model-note");
  if (!box) return;
  box.textContent = text;
  box.className = `model-note ${tone}`;
  box.hidden = !text;
  if (box._timer) window.clearTimeout(box._timer);
  if (text) box._timer = window.setTimeout(() => (box.hidden = true), 7000);
}

async function loadModels() {
  const data = await api("/api/models");
  const select = $("model-select");
  select.textContent = "";

  // `choices` carries the provider; an older backend only had `ollama` + `model`.
  const choices =
    Array.isArray(data.choices) && data.choices.length
      ? data.choices
      : [...new Set([...(data.ollama || []), data.model].filter(Boolean))].map((id) => ({
          id,
          provider: "ollama",
          local: true,
          note: "",
        }));
  const current = data.current || { id: data.model, provider: data.provider || "ollama" };
  const advanced = modelsAdvanced();

  // A browser that remembers a choice the server does not (a refused config write,
  // a config.toml edited by hand) restores it once, then shows what the server
  // says. Guarded by state.modelRestored: one attempt, never a loop.
  const saved = storedModelChoice();
  if (
    saved &&
    !state.modelRestored &&
    (saved.provider !== current.provider || saved.model !== current.id)
  ) {
    state.modelRestored = true;
    try {
      await api("/api/model", {
        method: "POST",
        body: JSON.stringify({ provider: saved.provider, model: saved.model }),
      });
      await loadModels();
      return;
    } catch (error) {
      modelNote(`modèle enregistré indisponible : ${error.message}`, "warn");
    }
  }

  // Two categories, in a fixed order: the cloud models first (they are the ones a
  // user pays for and picks deliberately), then the local ones by weight - the
  // biggest is the one worth trying, and alphabetical order said nothing about it.
  // `apiChoices`, not `api`: `api()` is the module's fetch helper, and shadowing it
  // here threw "Cannot access 'api' before initialization" at boot.
  const apiChoices = choices
    .filter((choice) => !choice.local)
    .sort((a, b) => `${a.provider}${a.id}`.localeCompare(`${b.provider}${b.id}`));
  const localChoices = choices
    .filter((choice) => choice.local)
    .sort((a, b) => Number(b.size || 0) - Number(a.size || 0) || a.id.localeCompare(b.id));
  const groups = [
    ["API (cloud)", apiChoices],
    ["Local (Ollama)", localChoices],
  ];

  for (const [label, items] of groups) {
    const visible = items.filter(
      (choice) =>
        advanced || (choice.id === current.id && choice.provider === current.provider)
    );
    if (!visible.length) continue;
    const group = document.createElement("optgroup");
    group.label = label;
    for (const choice of visible) {
      const mark = choice.local ? "💻 Local" : "☁️ API";
      // The provider is part of the name: "deepseek-flash" alone does not say who
      // serves it, and the same id can exist on two providers.
      const providerName = choice.local
        ? ""
        : ` · ${providerLabel(choice.provider, false).replace("API · ", "")}`;
      const size = choice.local && choice.size
        ? ` · ${(Number(choice.size) / 1_000_000_000).toFixed(1)} Go`
        : "";
      // Only when the provider says so: /api/models answers null when it cannot
      // tell, and a badge on a working model is worse than no badge.
      const warn = choice.tools_ok === false ? " · outils non fiables" : "";
      const option = el("option", null, `${mark} ${choice.id}${providerName}${size}${warn}`);
      if (choice.tools_ok === false) {
        option.title = "Ce modèle ne déclare pas le support des outils : il risque de répondre par du code.";
      }
      option.value = `${choice.provider}/${choice.id}`;
      if (choice.id === current.id && choice.provider === current.provider) option.selected = true;
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  select.title = `${providerLabel(current.provider, current.provider === "ollama")} · ${current.id}`;

  const toggle = $("model-advanced");
  if (toggle) {
    // An icon: the header is one row, and a word here costs the context gauge.
    toggle.textContent = advanced ? "▾" : "⋯";
    toggle.setAttribute("aria-label", advanced ? "Réduire la liste" : "Tous les modèles");
    toggle.title = advanced
      ? "N'afficher que le modèle courant"
      : `Voir les ${choices.length} modèles disponibles (local et API)`;
  }

  // onchange, not addEventListener: loadModels runs more than once.
  select.onchange = async () => {
    const cut = select.value.indexOf("/");
    const provider = select.value.slice(0, cut);
    const model = select.value.slice(cut + 1);
    const payload = { provider, model };
    if (state.sessionId) payload.session_id = state.sessionId;
    try {
      const result = await api("/api/model", { method: "POST", body: JSON.stringify(payload) });
      // The select already shows the model: the note only confirms the engine.
      storeModelChoice(provider, model);
      const where = providerLabel(provider, provider === "ollama");
      modelNote(
        result && result.persisted === false
          ? `${where} · choix non enregistré (config.toml non modifiable)`
          : where,
        result && result.persisted === false ? "warn" : "ok"
      );
      // The header names the model of THIS session (gate B: it kept announcing the
      // global default while the session ran on another one).
      await loadStatus().catch(() => {});
      checkMediaFit();
    } catch (error) {
      // A refused switch changed nothing: put the selector back where it was.
      select.value = `${current.provider}/${current.id}`;
      modelNote(error.message, "warn");
    }
  };
}

function wireModelToggle() {
  const toggle = $("model-advanced");
  if (!toggle) return;
  toggle.addEventListener("click", async () => {
    try {
      window.localStorage.setItem(MODELS_ADVANCED_KEY, modelsAdvanced() ? "0" : "1");
    } catch (error) {
      /* a private window has no storage: the list simply stays simple */
    }
    await loadModels();
  });
}

// ------------------------------------------------------------- attachments --
// Files are uploaded as they are dropped, not when Send is pressed: the server
// decides inlined vs referenced at upload time, and the chip shows that decision
// before the user commits the turn.
function humanSize(bytes) {
  const size = Number(bytes) || 0;
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} Mo`;
  if (size >= 1024) return `${Math.round(size / 1024)} ko`;
  return `${size} o`;
}

function kindLabel(attachment) {
  const name = String(attachment.name || "").toLowerCase();
  const mime = String(attachment.mime || attachment.type || "");
  if (mime.startsWith("image/") || /\.(png|jpe?g|gif|svg|webp|bmp|heic)$/.test(name)) return "image";
  if (mime.startsWith("video/") || name.endsWith(".mp4")) return "vidéo";
  if (mime.startsWith("audio/") || /\.(mp3|wav|flac|ogg)$/.test(name)) return "son";
  if (name.endsWith(".pdf")) return "PDF";
  return "document";
}

function attachmentChip(attachment) {
  const chip = el("span", "attachment");
  chip.dataset.path = attachment.path;
  chip.appendChild(el("span", "attachment-name", attachment.name));
  chip.appendChild(
    el("span", "attachment-meta", `${humanSize(attachment.size)} · ${kindLabel(attachment)}`)
  );
  chip.title = attachment.note || attachment.path;
  const drop = el("button", "attachment-remove", "×");
  drop.setAttribute("aria-label", `Retirer ${attachment.name}`);
  drop.addEventListener("click", () => {
    state.attachments = state.attachments.filter((item) => item.path !== attachment.path);
    renderAttachments();
  });
  chip.appendChild(drop);
  return chip;
}

function renderAttachments() {
  const box = $("attachments");
  box.textContent = "";
  for (const attachment of state.attachments) box.appendChild(attachmentChip(attachment));
  box.hidden = state.attachments.length === 0;
  checkMediaFit();
}

// ------------------------------------------------------------------ media -- P1-14
// One "+" instead of a row of icons: the menu is grouped by nature, and each group
// sets the picker's own filter. Drag, drop and paste keep working on the whole area.
const ACCEPT = {
  image: ".png,.jpg,.jpeg,.gif,.svg,.heic,.webp,.bmp",
  video: ".mp4,.mov,.mkv,.webm,.m4v",
  audio: ".mp3,.wav,.flac,.ogg,.m4a,.opus",
  document: ".pdf,.csv,.html,.htm,.zip,.txt,.md,.json,.yaml,.yml,.toml,.py,.js,.ts,.sh",
};

//: La porte qui a ouvert la modale : le focus y revient a la fermeture.
let plusOpener = null;

function plusOpen(open) {
  // P3-16b : la modale d'attachement. Elle s'ouvre au centre, assombrit le reste, et
  // se referme d'elle-meme une fois les fichiers joints : le champ de saisie redevient
  // visible et utilisable, ce que le popover en bas a gauche empechait.
  const menu = $("plus-menu");
  const button = $("attach");
  const backdrop = $("plus-backdrop");
  if (!menu) return;
  const next = open === undefined ? menu.hidden : Boolean(open);
  menu.hidden = !next;
  if (backdrop) backdrop.hidden = !next;
  document.body.classList.toggle("modal-open", next);
  if (button) button.setAttribute("aria-expanded", next ? "true" : "false");
  if (next) {
    const first = menu.querySelector(".plus-item") || menu.querySelector("button");
    if (first) first.focus();
  } else if (plusOpener) {
    // Le focus revient a la porte qui l'a ouvert : sans cela il retombait sur <body>,
    // et la tabulation repartait du haut de la page.
    plusOpener.focus();
    plusOpener = null;
  }
}

//: Le clavier ne sort pas de la modale tant qu'elle est ouverte (piège de tabulation).
function plusFocusables() {
  const menu = $("plus-menu");
  if (!menu) return [];
  return [...menu.querySelectorAll("button, [href], input, select, textarea")]
    .filter((node) => !node.hidden && !node.disabled && node.offsetParent !== null);
}

function plusTrapTab(event) {
  const menu = $("plus-menu");
  if (!menu || menu.hidden || event.key !== "Tab") return;
  const nodes = plusFocusables();
  if (!nodes.length) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !menu.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || !menu.contains(active))) {
    event.preventDefault();
    first.focus();
  }
}

const FILTER_EMPTY = {
  all: "Aucun fichier proposé.",
  image: "Aucune image proposée par cette liste.",
  document: "Aucun document proposé par cette liste.",
  media: "Aucun média (vidéo ou son) proposé par cette liste.",
};

function plusFilter(name) {
  for (const tab of document.querySelectorAll(".plus-filter")) {
    const active = tab.dataset.filter === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  let visible = 0;
  for (const item of document.querySelectorAll(".plus-item[data-kind]")) {
    const kind = item.dataset.kind || "";
    const shown = name === "all" || kind === name;
    item.hidden = !shown;
    if (shown) visible += 1;
  }
  // Un filtre vide le dit : une grille vide se lit comme un bug de chargement.
  const empty = $("plus-empty");
  if (empty) {
    empty.hidden = visible > 0;
    if (!visible) empty.textContent = FILTER_EMPTY[name] || FILTER_EMPTY.all;
  }
}

// The warning is informative, never a door: it says what will be lost and offers a
// model that would not lose it, then gets out of the way.
function mediaWarning(text, offer) {
  const box = $("media-warning");
  if (!box) return;
  box.textContent = "";
  if (!text) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.appendChild(el("span", null, text));
  if (offer) {
    const switchTo = el("button", "media-switch", `Passer à ${offer.model}`);
    switchTo.type = "button";
    switchTo.title = `${offer.provider} · ${offer.model} (${(offer.modalities || []).join(", ")})`;
    switchTo.addEventListener("click", async () => {
      switchTo.disabled = true;
      try {
        await api("/api/model", {
          method: "POST",
          body: JSON.stringify({ provider: offer.provider, model: offer.model }),
        });
        await loadStatus();
        await loadModels().catch(() => {});
        await checkMediaFit();
        await loadAudioCapabilities().catch(() => {});
      } catch (error) {
        switchTo.disabled = false;
        box.appendChild(el("span", "media-warning-error", `bascule impossible : ${error.message}`));
      }
    });
    box.appendChild(switchTo);
  }
}

function attachmentKinds() {
  const kinds = new Set();
  for (const item of state.attachments) {
    const name = String(item.name || "").toLowerCase();
    if (/\.(png|jpe?g|gif|svg|heic|webp|bmp)$/.test(name)) kinds.add("image");
    else if (/\.(mp4|mov|mkv|webm|m4v)$/.test(name)) kinds.add("video");
    else if (/\.(mp3|wav|flac|ogg|m4a|opus)$/.test(name)) kinds.add("audio");
  }
  return [...kinds];
}

let mediaFitToken = 0;

async function checkMediaFit() {
  const kinds = attachmentKinds();
  if (!kinds.length) {
    mediaWarning("");
    return;
  }
  const token = ++mediaFitToken;
  try {
    const kind = kinds.includes("image") ? "image" : kinds[0];
    const data = await api(
      `/api/media/capabilities?kind=${encodeURIComponent(kind)}&provider=${encodeURIComponent(
        state.kind || ""
      )}&model=${encodeURIComponent(state.model || "")}`
    );
    if (token !== mediaFitToken) return;
    const model = data.model || {};
    if (model.capable) {
      mediaWarning("");
      return;
    }
    const candidates = data.vision_candidates || [];
    const verified = model.verified === false;
    const head = verified
      ? `capacité non vérifiée pour ${model.model || "ce modèle"} : l'image pourrait être ignorée.`
      : model.loss || "ce modèle ne lira pas cette pièce jointe.";
    mediaWarning(
      candidates.length
        ? `${head} Un modèle qui la lit est déjà configuré.`
        : `${head} Aucun modèle capable n'est configuré : ajoute une clé dans « Connexions ».`,
      candidates[0] || null
    );
  } catch (error) {
    /* a capability table that cannot be read must not block a send */
  }
}

// One session-creation path, memoised: two concurrent callers used to POST two
// sessions and the second answer re-pointed `state.sessionId` in the middle of a
// turn, which is how a message ended up in a session nobody was reading.
let sessionPending = null;

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  if (sessionPending) return sessionPending;
  sessionPending = (async () => {
    try {
      const data = await api("/api/sessions", { method: "POST", body: JSON.stringify({}) });
      state.sessionId = data.session.id;
      return state.sessionId;
    } finally {
      sessionPending = null;
    }
  })();
  return sessionPending;
}

async function uploadFile(file) {
  const sessionId = await ensureSession();
  // Raw body, not FormData: the server needs no multipart parser, and a File is
  // already a Blob. The name travels in the query string.
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/upload?name=${encodeURIComponent(file.name)}`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    }
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || httpErrorText(response.status));
  }
  const data = await response.json();
  state.attachments.push(data.attachment);
  renderAttachments();
}

async function uploadFiles(files) {
  for (const file of files) {
    try {
      await uploadFile(file);
    } catch (error) {
      appendMessage("system", `attachment refused: ${file.name} — ${error.message}`);
    }
  }
}

function wireAttachments() {
  // One "+" opening a grouped menu: the picker's filter changes with the group, so
  // the same button reaches images, video, sound and documents without a row of
  // icons. Drag, drop and paste on the whole area are untouched.
  $("attach").addEventListener("click", (event) => {
    event.stopPropagation();
    plusOpener = event.currentTarget;
    plusOpen();
  });
  for (const item of document.querySelectorAll(".plus-item[data-accept]")) {
    item.addEventListener("click", () => {
      const group = item.dataset.accept || "document";
      $("files").setAttribute("accept", ACCEPT[group] || "");
      plusOpen(false);
      $("files").click();
    });
  }
  for (const tab of document.querySelectorAll(".plus-filter")) {
    tab.addEventListener("click", () => plusFilter(tab.dataset.filter || "all"));
  }
  if ($("plus-close")) $("plus-close").addEventListener("click", () => plusOpen(false));
  if ($("plus-backdrop")) $("plus-backdrop").addEventListener("click", () => plusOpen(false));
  if ($("plus-import")) {
    $("plus-import").addEventListener("click", () => {
      // Toutes les familles : le filtre est un confort, pas une restriction.
      $("files").setAttribute("accept", Object.values(ACCEPT).join(","));
      plusOpen(false);
      $("files").click();
    });
  }
  const dictate = $("plus-dictate");
  if (dictate) {
    dictate.addEventListener("click", () => {
      plusOpen(false);
      toggleMic().catch(() => {});
    });
  }
  document.addEventListener("click", (event) => {
    // La modale est centree : un clic dans le vide autour d'elle la ferme, un clic
    // dans le bouton + la bascule. Le fond assombri fait le reste.
    const menu = $("plus-menu");
    if (!menu || menu.hidden) return;
    if (!menu.contains(event.target) && event.target !== $("attach")) plusOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const menu = $("plus-menu");
      if (menu && !menu.hidden) {
        // Échap appartient à la modale tant qu'elle est ouverte. Sans l'arrêt de
        // propagation, le MÊME événement atteignait le gestionnaire du panneau (enregistré
        // plus bas) qui ajoutait `right-collapsed` à #layout : la modale se fermait et le
        // panneau disparaissait (mesuré : #group-tools passait de 139 px à 0).
        event.stopImmediatePropagation();
        plusOpen(false);
        return;
      }
    }
    plusTrapTab(event);
  });
  $("files").addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";  // same file twice must upload twice
    plusOpen(false);
    await uploadFiles(files);
  });
  const zone = $("messages").parentElement;
  let depth = 0;
  zone.addEventListener("dragover", (event) => {
    if (!event.dataTransfer || ![...event.dataTransfer.types].includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  zone.addEventListener("dragenter", () => {
    depth += 1;
    zone.classList.add("dropzone");
  });
  zone.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0) zone.classList.remove("dropzone");
  });
  zone.addEventListener("drop", async (event) => {
    if (!event.dataTransfer || !event.dataTransfer.files.length) return;
    event.preventDefault();
    depth = 0;
    zone.classList.remove("dropzone");
    await uploadFiles([...event.dataTransfer.files]);
  });
  renderAttachments();
}

// ---------------------------------------------------------------- sessions --
const LAYOUT_KEY = "uh.layout.v1";
// null = "follow the window". A column width in px only exists once the user has
// dragged the splitter; until then the stylesheet clamps it, so a 1280 px laptop
// does not lose 600 px of conversation to two fixed columns.
const LAYOUT_DEFAULTS = { sidebar: null, right: null, sidebarCollapsed: false };
const LAYOUT_LIMITS = { sidebar: [180, 460], right: [200, 560] };

function layoutPrefs() {
  try {
    return { ...LAYOUT_DEFAULTS, ...JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}") };
  } catch {
    return { ...LAYOUT_DEFAULTS };
  }
}

function saveLayoutPrefs(patch) {
  const next = { ...layoutPrefs(), ...patch };
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(next));
  } catch {
    /* private mode: the layout still works, it just will not survive a reload */
  }
  applyLayoutPrefs(next);
}

function applyLayoutPrefs(prefs = layoutPrefs()) {
  const style = $("layout").style;
  // A stored (or dragged) width wins; no width means the CSS clamp decides.
  for (const [key, property] of [
    ["sidebar", "--sidebar-w"],
    ["right", "--right-w"],
  ]) {
    if (typeof prefs[key] === "number") style.setProperty(property, `${prefs[key]}px`);
    else style.removeProperty(property);
  }
  $("layout").classList.toggle("sidebar-collapsed", Boolean(prefs.sidebarCollapsed));
  const toggle = $("sidebar-toggle");
  toggle.textContent = prefs.sidebarCollapsed ? "»" : "«";
  toggle.title = prefs.sidebarCollapsed ? "Afficher la colonne des sessions" : "Masquer la colonne des sessions";
}

function clamp(value, [low, high]) {
  return Math.min(high, Math.max(low, Math.round(value)));
}

// One drag handle per inner edge. Pointer capture keeps the drag alive when the
// cursor runs over the conversation, which is what every other app does and what
// a naive mousemove gets wrong.
function wireSplitter(id, side) {
  const handle = $(id);
  let startX = 0;
  let startWidth = 0;
  let live = false;

  const widthOf = () =>
    side === "sidebar"
      ? $("sidebar").getBoundingClientRect().width
      : $("providers-panel").getBoundingClientRect().width;

  const finish = () => {
    if (!live) return;
    live = false;
    handle.classList.remove("dragging");
    const width = Math.round(widthOf());
    saveLayoutPrefs(side === "sidebar" ? { sidebar: width } : { right: width });
  };

  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    live = true;
    startX = event.clientX;
    startWidth = widthOf();
    handle.classList.add("dragging");
    handle.setPointerCapture(event.pointerId);
  });

  handle.addEventListener("pointermove", (event) => {
    if (!live) return;
    const delta = side === "sidebar" ? event.clientX - startX : startX - event.clientX;
    const width = clamp(startWidth + delta, LAYOUT_LIMITS[side]);
    $("layout").style.setProperty(side === "sidebar" ? "--sidebar-w" : "--right-w", `${width}px`);
  });

  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);

  // Double-click resets that column, like a table column edge in a spreadsheet.
  handle.addEventListener("dblclick", () => {
    saveLayoutPrefs(side === "sidebar" ? { sidebar: LAYOUT_DEFAULTS.sidebar } : { right: LAYOUT_DEFAULTS.right });
  });

  handle.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 48 : 16;
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const grow = side === "sidebar" ? event.key === "ArrowRight" : event.key === "ArrowLeft";
    const width = clamp(widthOf() + (grow ? step : -step), LAYOUT_LIMITS[side]);
    saveLayoutPrefs(side === "sidebar" ? { sidebar: width } : { right: width });
  });
}

function wireLayout() {
  applyLayoutPrefs();
  wireSplitter("splitter-left", "sidebar");
  wireSplitter("splitter-right", "right");
  $("sidebar-toggle").addEventListener("click", () => {
    saveLayoutPrefs({ sidebarCollapsed: !layoutPrefs().sidebarCollapsed });
  });
}

// The session being dragged: kept here because HTML5 drag events carry data but
// no reliable source element across browsers.
let dragSession = null;

async function fileInto(sessionId, folder) {
  await api(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({ folder }),
  });
  await loadSessions();
}

// A folder header is the drop target. The empty name means "Sessions" - dropping
// there takes a session out of its folder, which is the only way to undo a filing
// without opening a prompt.
function wireFolderDrop(head) {
  const target = head.dataset.folder || "";
  head.addEventListener("dragover", (event) => {
    if (!dragSession) return;
    event.preventDefault();  // a drop only fires when dragover is cancelled
    event.dataTransfer.dropEffect = "move";
    head.classList.add("drop-target");
  });
  head.addEventListener("dragleave", () => head.classList.remove("drop-target"));
  head.addEventListener("drop", async (event) => {
    event.preventDefault();
    head.classList.remove("drop-target");
    const id = event.dataTransfer.getData("text/plain") || dragSession;
    if (!id) return;
    await fileInto(id, target);
  });
}

function sessionRow(session) {
  const item = el("li", "session");
  item.dataset.id = session.id;
  if (session.id === state.sessionId) item.classList.add("active");
  const row1 = el("div", "row1");
  row1.appendChild(el("span", "sid", session.short_id));
  row1.appendChild(el("span", "meta", `${session.message_count} msg`));
  item.appendChild(row1);
  item.appendChild(el("div", "title", session.title || "(untitled)"));
  item.appendChild(el("div", "meta", session.provider || "-"));

  const actions = el("div", "session-actions");
  const folder = el("button", null, "📁");
  folder.title = "Classer cette session dans un dossier";
  folder.setAttribute("aria-label", "Classer la session");
  folder.addEventListener("click", (event) => {
    event.stopPropagation();
    fileSession(session);
  });
  const remove = el("button", "danger", "🗑");
  remove.title = "Supprimer cette session";
  remove.setAttribute("aria-label", "Supprimer la session");
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteSession(session);
  });
  actions.appendChild(folder);
  actions.appendChild(remove);
  item.appendChild(actions);

  item.draggable = true;
  item.addEventListener("dragstart", (event) => {
    dragSession = session.id;
    event.dataTransfer.setData("text/plain", session.id);
    event.dataTransfer.effectAllowed = "move";
    item.classList.add("dragging");
  });
  item.addEventListener("dragend", () => {
    dragSession = null;
    item.classList.remove("dragging");
  });

  item.addEventListener("click", () => openSession(session.id));
  return item;
}

// Folders are metadata, not a schema: sessions without a folder stay at the top
// under "Sessions", the named ones follow in alphabetical order.
function groupSessions(sessions) {
  const groups = new Map();
  for (const session of sessions) {
    const name = (session.metadata && session.metadata.folder) || "";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(session);
  }
  // Named folders first, alphabetical, and the unfiled sessions last: the work
  // you organised is the work you meant to find.
  const names = [...groups.keys()].sort((a, b) => {
    if (a === "") return 1;
    if (b === "") return -1;
    return a.localeCompare(b);
  });
  return names.map((name) => ({ name, sessions: groups.get(name) }));
}

async function loadSessions() {
  const data = await api("/api/sessions");
  const list = $("session-list");
  list.textContent = "";
  for (const group of groupSessions(data.sessions)) {
    if (group.name || data.sessions.some((s) => s.metadata && s.metadata.folder)) {
      const head = el("li", "folder-head");
      head.dataset.folder = group.name;
      head.appendChild(el("span", null, group.name || "Sessions"));
      head.appendChild(el("span", "count", String(group.sessions.length)));
      if (group.name) {  // the ungrouped header has nothing to rename or delete
        const actions = el("div", "folder-actions");
        const edit = el("button", null, "✏");
        edit.title = "Rename this folder";
        edit.setAttribute("aria-label", "Rename folder");
        edit.addEventListener("click", () => renameFolder(head));
        const kill = el("button", "danger", "🗑");
        kill.title = "Delete this folder (its sessions are kept)";
        kill.setAttribute("aria-label", "Delete folder");
        kill.addEventListener("click", () => removeFolder(head));
        actions.appendChild(edit);
        actions.appendChild(kill);
        head.appendChild(actions);
      }
      wireFolderDrop(head);
      list.appendChild(head);
    }
    for (const session of group.sessions) list.appendChild(sessionRow(session));
  }
  if (!data.sessions.length) {
    list.appendChild(el("li", "meta", "no sessions yet"));
  }
}

async function renameFolder(head) {
  const previous = head.dataset.folder || "";
  const answer = window.prompt("New folder name:", previous);
  if (answer === null) return;
  const name = answer.trim();
  if (!name || name === previous) return;
  await api(`/api/folders/${encodeURIComponent(previous)}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
  await loadSessions();
}

async function removeFolder(head) {
  const name = head.dataset.folder || "";
  if (!name) return;
  const count = head.querySelector(".count")?.textContent || "?";
  if (!window.confirm(`Delete the folder "${name}"? Its ${count} session(s) are kept, unfiled.`)) {
    return;
  }
  await api(`/api/folders/${encodeURIComponent(name)}`, { method: "DELETE" });
  await loadSessions();
}

async function fileSession(session) {
  const current = (session.metadata && session.metadata.folder) || "";
  const answer = window.prompt("Folder name (leave empty to remove it):", current);
  if (answer === null) return;
  await api(`/api/sessions/${encodeURIComponent(session.id)}`, {
    method: "PATCH",
    body: JSON.stringify({ folder: answer.trim() }),
  });
  await loadSessions();
}

async function deleteSession(session) {
  const label = session.title || session.short_id;
  if (!window.confirm(`Delete "${label}" and its messages?`)) return;
  await api(`/api/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
  if (state.sessionId === session.id) {
    state.sessionId = null;
    $("messages").textContent = "";
    $("session-title").textContent = "No session";
    updateGauge([]);
  }
  await loadSessions();
}

// "+ Folder" files the *open* session: a folder that contains nothing is not a
// thing the store can hold, and inventing empty folders would mean a second
// source of truth for the sidebar.
async function newFolder() {
  if (!state.sessionId) {
    $("folder-hint").textContent = "open a session first";
    setTimeout(() => { $("folder-hint").textContent = ""; }, 4000);
    return;
  }
  const answer = window.prompt("Folder name for this session:");
  if (answer === null) return;
  await api(`/api/sessions/${encodeURIComponent(state.sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({ folder: answer.trim() }),
  });
  await loadSessions();
}


async function openSession(id) {
  state.sessionId = id;
  state.attachments = [];
  renderAttachments();
  const data = await api(`/api/sessions/${encodeURIComponent(id)}/messages?limit=200`);
  $("session-title").textContent = data.session.title || data.session.short_id;
  if (data.model) {
    const select = $("model-select");
    if (![...select.options].some((o) => o.value === data.model)) {
      const option = el("option", null, data.model);
      option.value = data.model;
      select.appendChild(option);
    }
    select.value = data.model;
  }
  // The conversation's own character (P3-14): reopening a project restores the
  // voice it was left in, or no role at all.
  fillRoleSelect((data.session.metadata || {}).role || null);
  renderHistory(data.messages);
  updateGauge(data.messages);
  // Le compteur local continue à partir de ce que le serveur a numéroté : un tour envoyé
  // après un rechargement porte le bon numéro.
  state.turns = Number(data.turns || 0);
  // A fresh read is the fix the header control performs: the stale flag it carried
  // belongs to the previous render, not to this one (P1-12).
  viewStale(false);
  // The conversation's own settings, stored with it: opening a session restores
  // the temperature it was written at, not the last one used in another tab.
  const stored = (data.session.metadata || {}).settings;
  state.llm = {
    enabled: Boolean(stored && stored.enabled),
    values: { ...state.llmDefaults.values, ...((stored && stored.values) || {}) },
  };
  renderLlmSummary();
  if ($("llm-panel")) loadLlmPanel().catch(() => {});
  // A tab opened from a deep link loads before any session exists: its loader
  // then ran with no id and the panel kept the "send a message" hint for ever.
  // Opening a session is what it was waiting for.
  if (state.activeTab === "decision") loadDecision().catch(() => {});
  await loadSessions();
  $("sidebar").classList.remove("open");
}

// --- modales : rôle, focus piégé, retour du focus ---------------------------------- //
// Every `.modal-backdrop` the page builds (help, new skill, fix, ...) is announced as
// a dialog, takes the focus, keeps Tab inside, and gives the focus back to whatever
// opened it when it disappears.
function installModalAccessibility() {
  let counter = 0;
  const openers = new WeakMap();
  const focusables = (root) =>
    [...root.querySelectorAll("button, [href], input, select, textarea, summary, [tabindex]")].filter(
      (node) => !node.disabled && !node.hidden && node.tabIndex >= 0 && node.offsetParent !== null
    );
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      record.addedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement) || !node.classList.contains("modal-backdrop")) return;
        const dialog = node.querySelector(".modal");
        if (!dialog) return;
        dialog.setAttribute("role", "dialog");
        dialog.setAttribute("aria-modal", "true");
        const title = dialog.querySelector(".modal-title");
        if (title) {
          if (!title.id) title.id = `modal-title-${++counter}`;
          dialog.setAttribute("aria-labelledby", title.id);
        }
        openers.set(node, document.activeElement);
        const first = focusables(dialog)[0];
        if (first) first.focus();
      });
      record.removedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement) || !node.classList.contains("modal-backdrop")) return;
        const opener = openers.get(node);
        if (opener && document.contains(opener) && typeof opener.focus === "function") opener.focus();
      });
    }
  });
  observer.observe(document.body, { childList: true });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const open = document.querySelectorAll(".modal-backdrop .modal");
    const dialog = open[open.length - 1];
    if (!dialog) return;
    const nodes = focusables(dialog);
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  });
}

async function newSession() {
  const data = await api("/api/sessions", { method: "POST", body: JSON.stringify({}) });
  state.sessionId = data.session.id;
  state.attachments = [];
  // Une nouvelle session repart de zéro : sans ça, la première bulle héritait du numéro
  // de tour de la session précédente (« Tour #7 » sur une conversation vide).
  state.turns = 0;
  renderAttachments();
  $("messages").textContent = "";
  $("session-title").textContent = "(untitled)";
  updateGauge([]);
  await loadSessions();
  $("input").focus();
}

// ------------------------------------------------------------------ audio -- P1-15
// Three rules from the roadmap, and they are visible here:
//   * the microphone exists only if an engine can dictate (`stt`), and it says so
//     when it cannot - a button that fails silently is worse than no button;
//   * the player is CONDITIONAL: a bubble shows one only when a sound really exists
//     for that message (recorded, uploaded, synthesised). No empty player ever;
//   * an audio render carries the same action bar as an image, with the same
//     exclusive votes and the same bin-with-undo, because it is the same code.
function audioArtifacts(list) {
  return (Array.isArray(list) ? list : []).filter(
    (item) => item && String(item.kind || "").toLowerCase() === "audio" && item.url
  );
}

// A real waveform, drawn from the decoded samples: a decorative bar chart would be
// a lie about what the sound contains.
function drawWaveform(canvas, url) {
  const context = canvas.getContext("2d");
  if (!context) return;
  const paint = (peaks) => {
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.fillStyle = getComputedStyle(document.body).getPropertyValue("--accent") || "#0c9f93";
    const middle = height / 2;
    const step = Math.max(1, Math.floor(peaks.length / width));
    for (let x = 0; x < width; x += 1) {
      const peak = peaks[Math.min(peaks.length - 1, x * step)] || 0;
      const bar = Math.max(1, Math.round(peak * (height - 2)));
      context.fillRect(x, middle - bar / 2, 1, bar);
    }
  };
  const flat = () => paint(new Array(120).fill(0.08));
  flat();
  fetch(url, { credentials: "same-origin" })
    .then((response) => response.arrayBuffer())
    .then((buffer) => {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return flat();
      return new Ctx().decodeAudioData(buffer).then((decoded) => {
        const channel = decoded.getChannelData(0);
        const buckets = 240;
        const size = Math.floor(channel.length / buckets) || 1;
        const peaks = [];
        for (let index = 0; index < buckets; index += 1) {
          let peak = 0;
          for (let cursor = 0; cursor < size; cursor += 1) {
            const value = Math.abs(channel[index * size + cursor] || 0);
            if (value > peak) peak = value;
          }
          peaks.push(peak);
        }
        paint(peaks);
      });
    })
    .catch(() => flat());
}

function drawAudio(container, artifacts, context) {
  for (const artifact of audioArtifacts(artifacts)) {
    const figure = el("figure", "tool-artifact audio");
    const player = el("audio", "audio-player");
    player.controls = true;
    player.preload = "none";
    player.src = artifact.url;
    figure.appendChild(player);
    const canvas = el("canvas", "audio-wave");
    canvas.width = 320;
    canvas.height = 40;
    canvas.setAttribute("aria-hidden", "true");
    figure.appendChild(canvas);
    drawWaveform(canvas, artifact.url);
    const engine = String(artifact.engine || "");
    const seconds = Number(artifact.seconds || 0);
    const caption = [`${seconds ? seconds.toFixed(1) + " s" : "audio"}`];
    if (engine) caption.push(engine === "voxtral" ? "Voxtral 3" : engine === "xtts" ? "XTTS-v2 (local)" : engine);
    figure.appendChild(el("figcaption", "tool-artifact-note", caption.join(" · ")));
    artifactToolbar(figure, artifact, context);
    container.appendChild(figure);
  }
}

// The mic: record, ask the transcribe route, insert PLAIN TEXT. Nothing about the
// audio reaches the turn - that is the anti-regression promise of this lot.
let recorder = null;
let recorderChunks = [];

function micStatus(text) {
  const box = $("mic-status");
  if (box) {
    box.textContent = text || "";
    box.hidden = !text;
  }
}

async function toggleMic() {
  const button = $("mic");
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    micStatus("Ce navigateur ne sait pas enregistrer le micro.");
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    micStatus(`Micro refusé : ${error.message}`);
    return;
  }
  recorderChunks = [];
  recorder = new MediaRecorder(stream);
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data && event.data.size) recorderChunks.push(event.data);
  });
  recorder.addEventListener("stop", async () => {
    stream.getTracks().forEach((track) => track.stop());
    button.classList.remove("recording");
    micStatus("transcription…");
    const blob = new Blob(recorderChunks, { type: recorder.mimeType || "audio/webm" });
    recorderChunks = [];
    try {
      const suffix = (recorder.mimeType || "").includes("ogg") ? ".ogg" : ".webm";
      const url = `/api/audio/transcribe?session_id=${encodeURIComponent(state.sessionId || "")}` +
        `&name=dictation${suffix}`;
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": blob.type || "application/octet-stream" },
        body: blob,
        credentials: "same-origin",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || httpErrorText(response.status));
      const text = String(payload.text || "").trim();
      if (!text) throw new Error("transcription vide");
      const input = $("input");
      input.value = input.value ? `${input.value} ${text}` : text;
      input.focus();
      micStatus(`dicté (${payload.engine || "local"})`);
      window.setTimeout(() => micStatus(""), 4000);
    } catch (error) {
      micStatus(`dictée impossible : ${error.message}`);
    }
  });
  recorder.start();
  button.classList.add("recording");
  micStatus("enregistrement… cliquer pour arrêter");
}

async function loadAudioCapabilities() {
  const button = $("mic");
  if (!button) return;
  try {
    const data = await api("/api/audio/capabilities");
    state.audio = data;
    button.hidden = !data.stt;
    if (!data.stt) {
      const reasons = (data.engines || [])
        .filter((engine) => engine.stt === false && engine.reason)
        .map((engine) => engine.reason)
        .join(" · ");
      button.title = `Dicter — indisponible : ${reasons || "aucun moteur de dictée"}`;
    } else {
      button.title = "Dicter (micro)";
    }
  } catch (error) {
    button.hidden = true;
  }
}

function wireMic() {
  const button = $("mic");
  if (button) button.addEventListener("click", () => toggleMic().catch(() => {}));
  loadAudioCapabilities().catch(() => {});
}

// -------------------------------------------------------------------- chat --
// The model that answered, named once (point 5). The header's tooltip, the
// answer's footer and the local-model tab read this same value: before, the header
// named the configured model (`auto/best-free`) while the answer came from another
// one, and nothing on screen said so.
function markServedModel(meta, bubble) {
  const model = String((meta && meta.model) || "").trim();
  if (!model) return;
  const requested = String((meta && meta.requested) || "").trim();
  const substituted = Boolean(meta && meta.substituted) || Boolean(requested && requested !== model);
  const provider = String((meta && meta.provider) || "").trim();
  state.servedModel = model;
  state.requestedModel = requested;
  const label = $("status-provider");
  if (label) {
    const version = String(label.textContent || "").trim() || "UH";
    const tail = substituted
      ? `modèle qui a répondu : ${model} (demandé : ${requested})`
      : `modèle : ${model}`;
    label.title = `${version} · fournisseur : ${provider} · ${tail}`;
  }
  if (bubble) {
    let footer = bubble.querySelector(".answer-model");
    if (!footer) {
      footer = el("span", "answer-model");
      bubble.appendChild(footer);
    }
    footer.textContent = substituted
      ? `modèle qui a répondu : ${model} (demandé : ${requested})`
      : `modèle : ${model}`;
  }
  markLocalModelTab();
}

// Same value in the local-model tab: the row that answered, not merely the default.
function markLocalModelTab() {
  const list = $("local-models");
  if (!list) return;
  for (const name of list.querySelectorAll(".local-model-name")) {
    const label = String(name.textContent || "").trim();
    name.classList.toggle("served", Boolean(state.servedModel) && label === state.servedModel);
  }
}

function parseFrames(buffer, onEvent) {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop();
  for (const block of blocks) {
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    try {
      onEvent(event, JSON.parse(dataLines.join("\n")));
    } catch {
      onEvent(event, { raw: dataLines.join("\n") });
    }
  }
  return rest;
}

// One stream frame must never take the whole turn down. A single bad payload
// (a node that is not there, an unexpected shape) used to throw inside
// parseFrames, which caught it, replayed the event as `{raw}`, and left the
// answer half-rendered with no visible reason.
function guardedStep(label, step) {
  try {
    step();
  } catch (error) {
    console.error(`chat: "${label}" handler failed`, error);
    const bubble = appendMessage("system", `Évènement « ${label} » illisible : ${error.message}`);
    bubble.classList.add("failed");
  }
}

// The store is written by the turn's worker thread, so right after `done` it can
// still be one message behind the DOM. Rebuilding the thread from it - what the
// finally block used to do after every turn - is exactly how an answer the user
// had just read disappeared. Reconcile instead: adopt the ids the store does know
// about, and flag a desynchronised view in the HEADER when the two still
// disagree. Nothing is ever removed here, and nothing is drawn in the thread.
//: The API answers the LAST `limit` rows (`store.get_messages` orders by id DESC
//: and reverses), so the window must be at least as large as the thread on
//: screen. A fixed 200-row window compared against a 250-bubble conversation
//: reported a gap on every turn of a long session (Codex review on PR #22).
const RECONCILE_MIN_WINDOW = 200;
//: Room for the bubbles a turn adds while it runs, so the window still covers them.
const RECONCILE_WINDOW_MARGIN = 25;
//: Past this, a single read costs more than the check is worth; the comparison is
//: skipped rather than made between two windows of different sizes.
const RECONCILE_MAX_WINDOW = 2000;
//: The worker commits the turn's row just after `done`: re-read once before
//: believing a gap, instead of surfacing a write that had not landed yet.
const RECONCILE_RETRY_MS = 400;

//: Bubbles that belong to the conversation. `system` (notices from a turn that
//: ended badly, "stopped") and `failed` (the model-error bubble, drawn
//: client-side) are the interface's own voice and are never stored; counting them
//: made the DOM look "ahead" of the store and raised the false warning this
//: function exists to prevent.
function threadBubbles() {
  return [...$("messages").querySelectorAll(".msg")].filter(
    (box) => !box.classList.contains("system") && !box.classList.contains("failed")
  );
}

async function storedMessages(count) {
  const data = await api(
    `/api/sessions/${encodeURIComponent(state.sessionId)}/messages?limit=${count}`
  );
  return (data.messages || []).filter((m) => (m.role || "assistant") !== "system");
}

// The control lives in the header, out of the conversation: a red card between
// two messages read as data loss, and the button asked the user to reload
// something they had not been told was out of date.
function viewStale(stale) {
  const button = $("view-stale");
  if (button) button.hidden = !stale;
}

function wireViewStale() {
  const button = $("view-stale");
  if (!button) return;
  button.addEventListener("click", () => {
    button.disabled = true;
    button.textContent = "Actualisation…";
    openSession(state.sessionId)
      .then(() => {
        button.textContent = "Actualiser la vue";
        viewStale(false);
      })
      .catch((error) => {
        button.textContent = "Réessayer";
        console.warn("transcript: refresh failed", error);
      })
      .finally(() => {
        button.disabled = false;
      });
  });
}

async function reconcileTranscript() {
  const boxes = threadBubbles();
  const wanted = Math.max(
    RECONCILE_MIN_WINDOW,
    Math.min(boxes.length + RECONCILE_WINDOW_MARGIN, RECONCILE_MAX_WINDOW)
  );
  let stored = await storedMessages(wanted);
  if (stored.length < boxes.length) {
    // One more read after the writer's usual delay: a single missing row is a
    // race with the worker, not a loss, and must never surface as an alert.
    await new Promise((resolve) => setTimeout(resolve, RECONCILE_RETRY_MS));
    stored = await storedMessages(wanted);
  }
  updateGauge(stored);
  // Only comparable when the window really covers the thread: a capped window
  // would make any long conversation look desynchronised again.
  const comparable = boxes.length + RECONCILE_WINDOW_MARGIN <= RECONCILE_MAX_WINDOW;
  const desynced = comparable && stored.length < boxes.length;
  if (desynced) {
    console.warn(
      `transcript: the store returned ${stored.length} message(s) for ${boxes.length} on screen; keeping the DOM`
    );
  }
  viewStale(desynced);
  const claimed = new Set(
    boxes.map((box) => box.dataset.messageId).filter((id) => id !== undefined)
  );
  for (const box of boxes) {
    if (box.dataset.messageId !== undefined) continue;
    const role = box.classList.contains("user")
      ? "user"
      : box.classList.contains("assistant")
        ? "assistant"
        : "system";
    const node = box.querySelector(".content");
    const text = node ? node.textContent : "";
    // Matched on role + exact text, last unclaimed wins: the streamed bubble is
    // the one that was just added, not an older twin of the same question.
    const match = stored.find(
      (message) =>
        !claimed.has(String(message.id)) &&
        (message.role || "assistant") === role &&
        (message.content || "") === text
    );
    if (!match) continue;
    claimed.add(String(match.id));
    box.dataset.messageId = match.id;
  }
}

//: No frame for this long and the turn is declared dead on the client side too:
//: a tool that never returns must not leave the composer locked for ever.
const STALL_MS = Number(window.UH_STALL_MS || 45000);

// One door for Enter and for the button. A second message during a turn is refused
// with a sentence the user can see - never accepted and lost, never silently
// dropped because `state.streaming` was still true from a turn that died.
function canSend() {
  return !state.streaming;
}

function restoreDraft(text) {
  const input = $("input");
  if (!input || !text) return;
  if (!input.value.trim()) {
    input.value = text;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
  }
}

function submitComposer() {
  const input = $("input");
  const text = input.value.trim();
  if (!text) return;
  if (!canSend()) {
    modelNote("un tour est déjà en cours : attends sa fin ou clique Stop", "warn");
    return;  // the text stays in the field, where the user left it
  }
  sendMessage(text);
}

async function sendMessage(text) {
  if (!canSend()) return;
  // The lock is taken BEFORE the first await: `newSession()` used to run unter the
  // same flag, so a double Enter could create two sessions and two turns.
  state.streaming = true;

  const attachments = state.attachments.map((item) => item.path);
  // Kept so a refusal card can re-send this exact turn after the user grants
  // execution: the point is not to make them type it again.
  state.lastUserText = text;
  $("send").disabled = true;
  $("stop").disabled = false;
  // Le fil disait « [1 file(s) attached] » en anglais, sans nom ni taille : l'operateur ne
  // savait pas ce qui avait ete envoye. Il liste maintenant les fichiers joints, en francais.
  const attachedList = state.attachments
    .map((item) => `${item.name} (${humanSize(item.size)}, ${kindLabel(item)})`)
    .join(", ");
  const userBubble = appendMessage(
    "user",
    attachments.length ? `${text}\n\n[fichier(s) joint(s) : ${attachedList}]` : text
  );
  // Le tour qui vient d'être envoyé porte son numéro tout de suite : le serveur le
  // confirmera au prochain rechargement, avec la même règle (un par message utilisateur).
  state.turns = (state.turns || 0) + 1;
  turnBadge(userBubble, state.turns);
  const assistant = appendMessage("assistant", "");
  // Held here instead of re-found later: `lastChild` stopped being the content
  // node once the Markdown swap ran, and `querySelector(".content")` returned
  // null on a bubble whose content node had already been swapped out.
  const assistantContent = assistant.querySelector(".content") || assistant.appendChild(el("div", "content"));
  let assistantText = "";
  let openCard = null;
  // Set when the turn failed. The finally block used to re-fetch the store and
  // redraw the transcript on every outcome - so a 500 erased the question and the
  // error together, which reads as "the app swallowed my message".
  let failed = "";

  const controller = new AbortController();
  state.abort = controller;
  // Life sign watchdog: every SSE frame resets it, and a silent stream ends with a
  // visible bubble plus a free composer instead of a phantom "streaming" state.
  let stallTimer = null;
  let stallFired = false;
  const touchStream = () => {
    if (stallTimer) window.clearTimeout(stallTimer);
    stallTimer = window.setTimeout(() => {
      stallFired = true;
      failed = `pas de réponse depuis ${Math.round(STALL_MS / 1000)} s`;
      assistant.classList.add("failed");
      assistantContent.textContent =
        `Pas de réponse depuis ${Math.round(STALL_MS / 1000)} s. ` +
        "Le tour est abandonné côté navigateur : tu peux réessayer (le texte est gardé).";
      restoreDraft(text);
      if (state.abort) state.abort.abort();
    }, STALL_MS);
  };
  try {
    if (!state.sessionId) await ensureSession();
    touchStream();
    const response = await fetch(`/api/sessions/${encodeURIComponent(state.sessionId)}/chat`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        attachments,
        skill: state.skill ? state.skill.name : "",
        settings: state.llm,
      }),
      signal: controller.signal,
    });
    if (response.status === 401) {
      showSessionBanner();
      restoreDraft(text);
      failed = "jeton périmé";
      return;
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || httpErrorText(response.status));
    }
    // Accepted: the files belong to the turn that just started. A refusal above
    // keeps them on screen, so the user can retry without dropping them again.
    state.attachments = [];
    renderAttachments();

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = parseFrames(buffer, (event, data) => {
        touchStream();
        if (event === "open") {
          guardedStep("open", () => {
            // The turn is accepted: only now is the field emptied. A refusal (400,
            // 503, dead token) leaves the draft where the user can see it.
            const input = $("input");
            if (input && input.value.trim() === text.trim()) {
              input.value = "";
              input.style.height = "auto";
            }
          });
        } else if (event === "ping") {
          // A life sign, not content: the watchdog above already restarted.
        } else if (event === "token") {
          guardedStep("token", () => {
            assistantText += data.text || "";
            assistantContent.textContent = assistantText;
            $("messages").scrollTop = $("messages").scrollHeight;
          });
        } else if (event === "tool_call") {
          guardedStep("tool_call", () => {
            openCard = toolCard(data.name, data.args, undefined);
          });
        } else if (event === "tool_result") {
          guardedStep("tool_result", () => {
            if (openCard) {
              fillToolCard(openCard, data.content);
              openCard = null;
            } else {
              toolCard(data.name, null, data.content);
            }
          });
        } else if (event === "artifacts") {
          guardedStep("artifacts", () => {
            // Bytes the server already fetched and bounded: the page draws them
            // from the message, it never asks a third party for anything.
            drawArtifacts(assistant, data.images);
            $("messages").scrollTop = $("messages").scrollHeight;
          });
        } else if (event === "mode_refused") {
          guardedStep("mode_refused", () => {
            modeRefusalCard(data);
          });
        } else if (event === "notice") {
          guardedStep("notice", () => {
            appendMessage("system", String(data.message || ""));
          });
        } else if (event === "approval") {
          guardedStep("approval", () => {
            approvalCard(data);
          });
        } else if (event === "error") {
          guardedStep("error", () => {
            failed = data.message || "error";
            // A slash command failing is not the model failing: labelling it
            // "Erreur du modèle" pushed people to check a provider key while the
            // broken part was a search endpoint.
            const label = data.source === "command" ? "Commande" : "Erreur du modèle";
            // Reported by the turn, not by the page's own request: a 401 here is the
            // provider refusing the key, never a stale UI token.
            const info = humanizeError(failed, "provider");
            const bubble = appendMessage("assistant", `${label} : ${info.text}`);
            bubble.classList.add("failed");
            bubble.setAttribute("role", "alert");
            if (info.text !== failed) decorateError(bubble, failed, info);
            else decorateError(bubble, failed, { actions: info.actions.length ? info.actions : ["reload"] });
            if (data.partial) {
              // The provider died after the model had already spoken: the words
              // on screen are real, and the answer was cut. Continuing is a
              // click, not a retyped question - and the stored partial answer is
              // what the model reads on the next turn.
              const again = el("button", "notice-action continuation", "Continuer");
              again.type = "button";
              again.addEventListener("click", () => {
                again.disabled = true;
                again.textContent = "Reprise…";
                sendMessage("Continue la réponse interrompue, sans la répéter.");
              });
              bubble.appendChild(again);
            }
          });
        } else if (event === "answer") {
          guardedStep("answer", () => {
            // The non-streaming form of the same content (a slash command answers
            // this way). It fills the bubble only when no token arrived: the two
            // frames describe one answer, and drawing both would duplicate it.
            const body = String(data.content || "");
            if (!body) return;
            if (!assistantText) {
              assistantText = body;
              assistantContent.textContent = body;
              assistantContent.classList.add("md");
              assistantContent.innerHTML = renderMarkdown(body);
            }
          });
        } else if (event === "answer_meta") {
          guardedStep("answer_meta", () => {
            // The model that answered, said once and used everywhere (point 5).
            markServedModel(data, assistant);
          });
        } else if (event === "done") {
          guardedStep("done", () => {
            if (!assistantText && !assistantContent.textContent.trim()) {
              // A bubble that disappears looks like the app ate the question.
              assistant.classList.add("failed");
              assistantContent.textContent =
                "Le modèle n'a rien renvoyé. Réessaie, ou choisis un autre modèle avec « ⋯ ».";
            } else {
              // The stream was appended as plain text; swap in the Markdown
              // rendering now that the answer is complete.
              assistantContent.classList.add("md");
              assistantContent.innerHTML = renderMarkdown(assistantText);
            }
          });
        }
      });
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      failed = error.message || "erreur inconnue";
      // The bubble stays. Removing it is what made a failed turn look like the
      // question had been swallowed along with the answer.
      assistant.classList.add("failed");
      // This catch wraps the browser's own request to /api: a 401 raised here (the
      // stream was refused before it opened) is a stale page token, so "ui".
      const info = humanizeError(failed, "ui");
      assistantContent.textContent = `Erreur du modèle : ${info.text} Réessaie, ou choisis un autre modèle avec « ⋯ ».`;
      assistant.setAttribute("role", "alert");
      decorateError(assistant, failed, { actions: info.actions.length ? info.actions : ["reload"] });
    }
  } finally {
    if (stallTimer) window.clearTimeout(stallTimer);
    state.streaming = false;
    state.abort = null;
    $("send").disabled = false;
    $("stop").disabled = true;
    if (failed && !stallFired) restoreDraft(text);
    if (!failed) {
      try {
        await reconcileTranscript();
      } catch (error) {
        console.warn("transcript: reconcile failed, keeping what is on screen", error);
      }
    }
    loadSessions();
  }
}

async function quitUh() {
  if ($("quit-uh").disabled) return;
  // Closing the browser window leaves the server (and UHB) running: this is the
  // only gesture that actually stops UH, so it asks once and says what it does.
  const go = window.confirm(
    "Quitter UH ?\n\nLe serveur s'arrête et le second brain (UHB) aussi. La page ne répondra plus."
  );
  if (!go) return;
  $("quit-uh").disabled = true;
  try {
    const result = await api("/api/system/quit", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    });
    showAppError(
      result.uhb_stopped
        ? "UH est arrêté, second brain compris. Tu peux fermer cet onglet."
        : "UH est arrêté. Relance-le avec le raccourci ou `uh web`."
    );
  } catch (error) {
    $("quit-uh").disabled = false;
    modelNote(`arrêt refusé : ${error.message}`, "warn");
  }
}

async function stopTurn() {
  if (!state.sessionId) return;
  await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/stop`, { method: "POST" });
  if (state.abort) state.abort.abort();
}

// ------------------------------------------------------------------ search --
let searchTimer = null;
function wireSearch() {
  $("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    const query = $("search").value.trim();
    const box = $("search-results");
    if (!query) {
      box.hidden = true;
      return;
    }
    searchTimer = setTimeout(async () => {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
      box.textContent = "";
      for (const hit of data.hits) {
        const row = el("div", "hit");
        row.appendChild(el("span", "sid", hit.short_id));
        row.appendChild(el("div", "snippet", hit.snippet || ""));
        row.addEventListener("click", () => openSession(hit.session_id));
        box.appendChild(row);
      }
      if (!data.hits.length) box.appendChild(el("div", "meta", "no match"));
      box.hidden = false;
    }, 220);
  });
}

// ----------------------------------------------------------------- palette --
async function loadCommands() {
  const data = await api("/api/commands");
  state.commands = data.commands;
}

function renderPalette(query) {
  const list = $("palette-list");
  const needle = query.toLowerCase().replace(/^\//, "");
  state.filtered = state.commands.filter(
    (command) =>
      command.name.startsWith(needle) ||
      command.aliases.some((alias) => alias.startsWith(needle)),
  );
  state.paletteIndex = 0;
  list.textContent = "";
  state.filtered.forEach((command, index) => {
    const item = el("li", index === 0 ? "active" : null);
    item.appendChild(document.createTextNode(`/${command.name} ${command.args || ""}`.trim()));
    item.appendChild(el("span", "alias", command.help));
    item.addEventListener("click", () => runCommand(command));
    list.appendChild(item);
  });
}

function runCommand(command) {
  closePalette();
  const input = $("input");
  if (command.name === "new") newSession();
  else if (command.name === "clear") $("messages").textContent = "";
  else if (command.name === "status") loadStatus();
  else if (command.name === "sessions") loadSessions();
  else if (command.name === "stop") stopTurn();
  else {
    // Commands without a direct API equivalent are inserted for the model or
    // explained, never silently ignored.
    input.value = command.args ? `/${command.name} ` : `/${command.name}`;
    input.focus();
  }
}

function openPalette() {
  $("palette").hidden = false;
  $("palette-input").value = "";
  renderPalette("");
  $("palette-input").focus();
}

function closePalette() {
  $("palette").hidden = true;
}

function wirePalette() {
  $("palette-input").addEventListener("input", (event) => renderPalette(event.target.value));
  $("palette-input").addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      state.paletteIndex = Math.max(
        0,
        Math.min(state.filtered.length - 1, state.paletteIndex + delta),
      );
      [...$("palette-list").children].forEach((node, index) =>
        node.classList.toggle("active", index === state.paletteIndex),
      );
    } else if (event.key === "Enter") {
      event.preventDefault();
      const command = state.filtered[state.paletteIndex];
      if (command) runCommand(command);
    } else if (event.key === "Escape") {
      closePalette();
    }
  });
  $("palette").addEventListener("click", (event) => {
    if (event.target === $("palette")) closePalette();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openPalette();
    }
    if (event.key === "Escape") closePalette();
  });
}


// ---------------------------------------------------------------- providers --
// Read-only in 7a.1: what exists, how many keys, and whether the router would
// use it. No key value ever crosses this boundary.
//
// Three entry points call in here - the ⚙ button, the "keys" tab from
// `showPanelTab()`, and `openPanelFromUrl()` - and two of them fire
// back-to-back, so the loader is idempotent: one shared promise while a request
// is in flight, and the loading state is observable (`aria-busy`) instead of
// implied by a text hint. A *new* call after a settled load does refetch,
// because every mutation below (add/delete a key, change a provider) awaits it.
async function fetchProviders() {
  const data = await api("/api/providers");
  state.keyEntryAllowed = Boolean(data.key_entry_allowed);
  // The banner depends on the answer, so it is set after it, not when opening.
  $("providers-hint").textContent = state.keyEntryAllowed
    ? "keys are write-only: only a masked hint is shown"
    : "read-only: key entry needs a loopback bind";
  if (!state.presets) {
    try {
      state.presets = await api("/api/providers/presets");
    } catch (error) {
      state.presets = { presets: [], gateway_only: [] };
    }
  }
  renderProviders(data.providers || []);
  renderKeysPanel(data.keys);
}

// The router settings are a *neighbouring* panel, not a prerequisite: a 500 here
// leaves the provider list on screen and says so in its own box. It used to be
// awaited inside the same chain, so its failure wiped a list that had already
// rendered - the "empty first click" that had no HTTP error behind it.
async function loadRouterSettings() {
  const box = $("router-settings");
  if (!box) return;
  try {
    renderRouterSettings(await api("/api/router/settings"));
  } catch (error) {
    box.textContent = "";
    box.appendChild(
      el("div", "panel-hint", `réglages du routeur indisponibles : ${error.message}`)
    );
  }
}

function loadProviders() {
  // Coalesced: a second call while the first is in flight joins it. The promise
  // is cleared on settle, never on success alone, so a failed load is retryable.
  if (state.providersPromise) return state.providersPromise;
  const panel = $("providers-panel");
  if (panel) panel.setAttribute("aria-busy", "true");
  const run = (async () => {
    try {
      await fetchProviders();
      // A collapsed panel keeps `display:flex` but its grid column is 0 wide: the
      // rows are in the DOM and clipped, which reads as "the list never loaded"
      // until the panel gets a real width. Re-assert it after the render, whatever
      // the entry point was.
      if (panel && panel.clientWidth === 0) {
        $("layout").classList.remove("right-collapsed");
      }
    } finally {
      if (panel) panel.setAttribute("aria-busy", "false");
    }
  })();
  state.providersPromise = run;
  const settle = () => {
    if (state.providersPromise === run) state.providersPromise = null;
  };
  run.then(settle, settle);
  // Independent, parallel, and it cannot fail the list.
  loadRouterSettings().catch(() => {});
  return run;
}

// Remote access is a privacy switch: the warning and the consent box only appear
// once the user actually asks for it, and the server refuses to enable it
// without consent: true in the same request.
function renderRouterSettings(settings) {
  const box = $("router-settings");
  box.textContent = "";

  const allowLabel = el("label", "settings-toggle");
  const allow = document.createElement("input");
  allow.type = "checkbox";
  allow.checked = Boolean(settings.allow_remote);
  allowLabel.appendChild(allow);
  allowLabel.appendChild(el("span", null, "Allow remote (free) providers"));
  box.appendChild(allowLabel);

  const warning = el("p", "settings-warning", settings.warning || "");
  const consentLabel = el("label", "settings-consent");
  const consent = document.createElement("input");
  consent.type = "checkbox";
  consent.checked = Boolean(settings.consent);
  consentLabel.appendChild(consent);
  consentLabel.appendChild(el("span", null, "I understand and consent"));
  warning.hidden = !allow.checked;
  consentLabel.hidden = !allow.checked;
  box.appendChild(warning);
  box.appendChild(consentLabel);

  const status = el("span", "test-result", "");
  const save = el("button", null, "Enregistrer");
  save.type = "button";
  allow.addEventListener("change", () => {
    warning.hidden = !allow.checked;
    consentLabel.hidden = !allow.checked;
  });
  save.addEventListener("click", async () => {
    status.textContent = "saving…";
    try {
      const updated = await api("/api/router/settings", {
        method: "PUT",
        body: JSON.stringify({ allow_remote: allow.checked, consent: consent.checked }),
      });
      status.textContent = "saved";
      status.className = "test-result ok";
      renderRouterSettings(updated);
      await loadProviders();
    } catch (error) {
      status.textContent = `error: ${error.message}`;
      status.className = "test-result warn";
    }
  });
  box.appendChild(save);
  box.appendChild(status);
}

function providerCard(row) {
  const card = el("div", "provider-card");
  const head = el("div", "provider-head");
  head.appendChild(el("span", "provider-name", row.name));
  const status = row.custom ? "custom" : row.verified;
  const badge = el("span", `badge ${status === "verified" ? "ok" : "warn"}`, status);
  head.appendChild(badge);
  if (row.in_chain) head.appendChild(el("span", "badge hl", "in chain"));
  card.appendChild(head);
  card.appendChild(
    el("div", "provider-meta", `${row.keys} key(s) · ${row.model || "no model configured"}`),
  );
  card.appendChild(el("div", "provider-url", row.base_url || ""));

  const testRow = el("div", "provider-test");
  const testButton = el("button", null, "Test connection");
  testButton.type = "button";
  const testResult = el("span", "test-result", "");
  testButton.addEventListener("click", async () => {
    testResult.textContent = "testing…";
    try {
      const result = await api(`/api/providers/${encodeURIComponent(row.name)}/test`, {
        method: "POST",
      });
      testResult.textContent = result.ok
        ? `ok`
        : `échec : ${result.error_kind}${result.status ? ` — ${httpErrorText(result.status)}` : ""}`;
      testResult.className = `test-result ${result.ok ? "ok" : "warn"}`;
    } catch (error) {
      testResult.textContent = `failed: ${error.message}`;
      testResult.className = "test-result warn";
    }
  });
  testRow.appendChild(testButton);
  // A provider that works is worth a variant: same URL, same key, another model
  // or another name. The copy is a normal entry - removable, testable, and its
  // key never leaves keys.toml.
  const clone = el("button", null, "Cloner");
  clone.type = "button";
  clone.title = "Dupliquer cette configuration (URL, modèle, clés) sous un autre nom";
  clone.addEventListener("click", async () => {
    const wanted = window.prompt(`Nom du clone de « ${row.name} »`, `${row.name}-2`);
    if (!wanted) return;
    clone.disabled = true;
    try {
      const result = await api(`/api/providers/${encodeURIComponent(row.name)}/clone`, {
        method: "POST",
        body: JSON.stringify({ name: wanted.trim().toLowerCase() }),
      });
      testResult.textContent = `cloné : ${result.name}`;
      testResult.className = "test-result ok";
      await loadProviders();
    } catch (error) {
      testResult.textContent = error.message;
      testResult.className = "test-result warn";
    } finally {
      clone.disabled = false;
    }
  });
  testRow.appendChild(clone);
  testRow.appendChild(testResult);
  card.appendChild(testRow);

  if (row.keys > 0) {
    const chips = el("div", "key-chips");
    for (let index = 1; index <= row.keys; index += 1) {
      const chip = el("span", "key-chip", `key ${index}`);
      const remove = el("button", "key-remove", "×");
      remove.type = "button";
      remove.title = `delete key ${index}`;
      remove.addEventListener("click", async () => {
        await api(`/api/providers/${encodeURIComponent(row.name)}/keys`, {
          method: "DELETE",
          body: JSON.stringify({ index }),
        });
        await loadProviders();
      });
      chip.appendChild(remove);
      chips.appendChild(chip);
    }
    card.appendChild(chips);
  }

  if (state.keyEntryAllowed) {
    const form = el("form", "provider-key-form");
    const input = document.createElement("input");
    input.type = "password";
    input.autocomplete = "off";
    input.placeholder = "paste key (never shown again)";
    input.className = "provider-key-input";
    const submit = el("button", null, "Ajouter une clé");
    submit.type = "submit";
    form.appendChild(input);
    form.appendChild(submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = input.value;
      input.value = ""; // cleared before the request: never re-displayed, even on error
      if (!value) return;
      try {
        await api(`/api/providers/${encodeURIComponent(row.name)}/keys`, {
          method: "POST",
          body: JSON.stringify({ key: value }),
        });
        await loadProviders();
      } catch (error) {
        $("providers-hint").textContent = `error: ${error.message}`;
      }
    });
    card.appendChild(form);
  }
  return card;
}

function customProviderForm() {
  const details = el("details", "provider-custom");
  details.appendChild(el("summary", null, "Ajouter un fournisseur personnalisé compatible OpenAI"));
  const form = el("form", "custom-form");

  const fields = [
    ["name", "name (a-z, 0-9, -, _)", "text"],
    ["base_url", "https://… (http only on loopback)", "text"],
    ["model", "model id", "text"],
    ["key", "key (optional, never shown again)", "password"],
  ];
  const inputs = {};
  for (const [field, placeholder, type] of fields) {
    const input = document.createElement("input");
    input.type = type;
    input.name = field;
    input.placeholder = placeholder;
    input.autocomplete = "off";
    if (type === "password") input.className = "provider-key-input";
    inputs[field] = input;
    const row = el("label", "custom-field");
    row.appendChild(el("span", null, field));
    row.appendChild(input);
    form.appendChild(row);
  }

  const submit = el("button", null, "Create");
  submit.type = "submit";
  const status = el("span", "test-result", "");
  form.appendChild(submit);
  form.appendChild(status);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: inputs.name.value.trim(),
      base_url: inputs.base_url.value.trim(),
      model: inputs.model.value.trim(),
      key: inputs.key.value,
    };
    inputs.key.value = ""; // cleared before sending
    try {
      await api("/api/providers/custom", { method: "POST", body: JSON.stringify(payload) });
      details.open = false;
      await loadProviders();
    } catch (error) {
      status.textContent = `error: ${error.message}`;
      status.className = "test-result warn";
    }
  });

  details.appendChild(form);
  return details;
}

function renderProviders(rows) {
  const box = $("providers-list");
  box.textContent = "";
  if (state.keyEntryAllowed) box.appendChild(customProviderForm());
  if (!rows.length) {
    box.appendChild(el("div", "panel-hint", "no provider in the catalogue"));
    return;
  }
  for (const row of rows) box.appendChild(providerCard(row));
}


// ----------------------------------------------------------------- keys tab --
// Priority order, fixed by the server: Cloudflare (free) -> DeepSeek (paid
// fallback) -> OmniRoute (free gateways). Every key stays write-only: the
// panel only ever receives a masked hint, and the input is cleared before the
// request so a failure can never re-display what was typed.
function keyCard(title, badgeText, badgeClass) {
  const card = el("div", "provider-card key-section");
  const head = el("div", "provider-head");
  head.appendChild(el("span", "provider-name", title));
  head.appendChild(el("span", `badge ${badgeClass}`, badgeText));
  card.appendChild(head);
  return card;
}

function keyField(form, label, { type = "text", placeholder = "", value = "" } = {}) {
  const row = el("label", "custom-field");
  row.appendChild(el("span", null, label));
  const input = document.createElement("input");
  input.type = type;
  input.placeholder = placeholder;
  input.value = value;
  input.autocomplete = "off";
  if (type === "password") input.className = "provider-key-input";
  row.appendChild(input);
  form.appendChild(row);
  return input;
}

function keyActions(form, label) {
  const actions = el("div", "key-actions");
  const submit = el("button", null, label);
  submit.type = "submit";
  const result = el("span", "test-result", "");
  actions.appendChild(submit);
  actions.appendChild(result);
  form.appendChild(actions);
  return result;
}

// One row per stored key: the masked hint the server already returns, and a delete
// button wired to the route that exists (DELETE .../keys with a 1-based index). The
// value never reaches the browser, so deletion is by position - and the confirmation
// names the hint, because "delete key 2" is not a question anyone can answer.
function keyRows(card, provider, hints) {
  const list = el("div", "key-rows");
  hints.forEach((hint, position) => {
    const row = el("div", "key-row");
    row.appendChild(el("span", "key-hint", hint || `clé ${position + 1}`));
    const drop = el("button", "data-button danger", "Supprimer");
    drop.type = "button";
    drop.setAttribute("aria-label", `Supprimer la clé ${position + 1} de ${provider}`);
    drop.addEventListener("click", async () => {
      const sure = window.confirm(
        `Supprimer la clé « ${hint || position + 1} » de ${provider} ?\n\n` +
          "Elle ne sera plus utilisée par UH. Cette action est immédiate."
      );
      if (!sure) return;
      drop.disabled = true;
      try {
        await api(`/api/providers/${encodeURIComponent(provider)}/keys`, {
          method: "DELETE",
          body: JSON.stringify({ index: position + 1 }),
        });
        await loadProviders();
      } catch (error) {
        row.appendChild(el("span", "panel-hint", `refusé : ${error.message}`));
        drop.disabled = false;
      }
    });
    row.appendChild(drop);
    list.appendChild(row);
  });
  card.appendChild(list);
  return list;
}

function cloudflareCard(section) {
  const card = keyCard("Cloudflare Workers AI", "free", "ok");
  let status = "not configured";
  if (section.configured && section.source === "environment") {
    status = "configured from the environment (no value stored by UH)";
  } else if (section.configured) {
    status = `configured · ${(section.key_hints || []).join(", ")}`;
  }
  card.appendChild(el("div", "provider-meta", status));
  if (section.account_id) {
    card.appendChild(el("div", "provider-url", `account ${section.account_id}`));
  }
  // Only a value UH stores can be deleted from here: "from the environment" belongs
  // to the caller's shell, not to this panel.
  if (state.keyEntryAllowed && section.source !== "environment" && (section.key_hints || []).length) {
    keyRows(card, "cloudflare", section.key_hints);
  }

  if (state.keyEntryAllowed) {
    const form = el("form", "custom-form");
    const account = keyField(form, "account", {
      placeholder: "32 hex characters",
      value: section.account_id || "",
    });
    const token = keyField(form, "token", {
      type: "password",
      placeholder: "API token (never shown again)",
    });
    const result = keyActions(form, "Enregistrer");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const typed = token.value;
      token.value = ""; // cleared before the request: never re-displayed
      if (!typed) return;
      const accountId = account.value.trim();
      if (!accountId) {
        // The account id is required: posting an empty one is what produced the
        // HTTP 400. Nothing is sent, so the server never sees an empty form.
        result.textContent = "account id required (32 hex characters)";
        result.className = "test-result warn";
        return;
      }
      try {
        await api("/api/providers/cloudflare/config", {
          method: "POST",
          body: JSON.stringify({ account_id: accountId, token: typed }),
        });
        await loadProviders();
      } catch (error) {
        result.textContent = `error: ${error.message}`;
        result.className = "test-result warn";
      }
    });
    card.appendChild(form);
  }
  return card;
}

function deepseekCard(section) {
  const card = keyCard("DeepSeek", "paid fallback", "hl");
  card.appendChild(
    el(
      "div",
      "provider-meta",
      section.configured
        ? `${(section.key_hints || []).length} key(s) · ${(section.key_hints || []).join(", ")}`
        : "not configured",
    ),
  );
  // A revoked key stayed on screen for ever because nothing could remove it: the
  // route existed, the interface had no button.
  if (state.keyEntryAllowed && (section.key_hints || []).length) {
    keyRows(card, "deepseek", section.key_hints);
  }

  if (state.keyEntryAllowed) {
    const form = el("form", "custom-form");
    const key = keyField(form, "key", {
      type: "password",
      placeholder: "API key (never shown again)",
    });
    const result = keyActions(form, "Enregistrer");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const typed = key.value;
      key.value = "";
      if (!typed) return;
      try {
        await api("/api/providers/deepseek/key", {
          method: "POST",
          body: JSON.stringify({ key: typed }),
        });
        await loadProviders();
      } catch (error) {
        result.textContent = `error: ${error.message}`;
        result.className = "test-result warn";
      }
    });
    card.appendChild(form);
  }
  return card;
}

function omnirouteCard(section) {
  const card = keyCard("OmniRoute", "free gateways", "ok");
  if (!section.detected) {
    card.appendChild(el("div", "provider-meta", "no OmniRoute config detected (~/.omniroute/.env)"));
    return card;
  }
  card.appendChild(el("div", "provider-url", section.base_url || ""));
  card.appendChild(
    el(
      "div",
      "provider-meta",
      section.has_key ? "configuration detected" : "configuration detected, no API key",
    ),
  );

  if (state.keyEntryAllowed && section.has_key) {
    const actions = el("div", "provider-test");
    const button = el("button", null, "Importer depuis OmniRoute");
    button.type = "button";
    const result = el("span", "test-result", "");
    button.addEventListener("click", async () => {
      result.textContent = "importing…";
      try {
        const imported = await api("/api/providers/omniroute/import", { method: "POST" });
        result.textContent = `imported ${imported.name}`;
        result.className = "test-result ok";
        await loadProviders();
      } catch (error) {
        result.textContent = `error: ${error.message}`;
        result.className = "test-result warn";
      }
    });
    actions.appendChild(button);
    actions.appendChild(result);
    card.appendChild(actions);
  }
  return card;
}

// Popular providers: picking one only pre-fills this form. Nothing is written
// until Save, which goes through the ordinary custom-provider route (one write
// path for keys.toml). A vendor without an OpenAI-compatible endpoint (Claude)
// gets an explanation instead of a form that would 404 on /v1/chat/completions.
function presetCard(data) {
  const card = keyCard("Popular providers", "quick add", "hl");
  const presets = (data && data.presets) || [];
  const builtin = (data && data.builtin) || [];
  const gatewayOnly = (data && data.gateway_only) || [];

  card.appendChild(
    el("div", "provider-meta", "Choose one, paste the key, save. Nothing is stored before that."),
  );

  const form = el("form", "custom-form");
  const row = el("label", "custom-field");
  row.appendChild(el("span", null, "preset"));
  const select = document.createElement("select");
  select.className = "preset-select";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "choose a provider…";
  select.appendChild(blank);
  for (const entry of presets) {
    const option = document.createElement("option");
    option.value = entry.name;
    option.textContent = `${entry.label} — ${entry.model}`;
    select.appendChild(option);
  }
  for (const entry of builtin) {
    const option = document.createElement("option");
    // "builtin:" prefix: these post the key to the provider's own endpoint, and
    // create_custom() would refuse the name - UH already ships the provider.
    option.value = `builtin:${entry.name}`;
    option.textContent = `${entry.label} — built-in`;
    select.appendChild(option);
  }
  for (const entry of gatewayOnly) {
    const option = document.createElement("option");
    option.value = `gateway:${entry.name}`;
    option.textContent = `${entry.label} (gateway required)`;
    select.appendChild(option);
  }
  row.appendChild(select);
  form.appendChild(row);

  const note = el("div", "provider-meta", "");
  form.appendChild(note);

  const inputs = {
    name: keyField(form, "name", { placeholder: "openai" }),
    base_url: keyField(form, "base_url", { placeholder: "https://…" }),
    model: keyField(form, "model", { placeholder: "model id" }),
    key: keyField(form, "key", { type: "password", placeholder: "API key (never shown again)" }),
  };
  const result = keyActions(form, "Enregistrer");

  let native = null;
  const lockFields = (locked) => {
    for (const input of [inputs.name, inputs.base_url, inputs.model]) {
      input.disabled = locked;
    }
    // Hugging Face is the one built-in whose model is a choice, not a constant:
    // the router takes any repo id, so the field stays open and offers a list.
    if (native && native.name === "huggingface") {
      inputs.model.disabled = false;
      inputs.model.setAttribute("list", "hf-models");
    } else {
      inputs.model.removeAttribute("list");
    }
  };

  select.addEventListener("change", () => {
    const chosen = presets.find((entry) => entry.name === select.value);
    native = builtin.find((entry) => `builtin:${entry.name}` === select.value) || null;
    const blocked = gatewayOnly.find((entry) => `gateway:${entry.name}` === select.value);
    note.textContent = blocked
      ? blocked.note
      : native
        ? `${native.label} ships with UH: paste the key, there is no URL to set.`
        : "";
    note.className = blocked ? "provider-meta warn" : "provider-meta";
    lockFields(Boolean(native));
    if (chosen) {
      inputs.name.value = chosen.name;
      inputs.base_url.value = chosen.base_url;
      inputs.model.value = chosen.model;
    } else if (native) {
      inputs.name.value = native.name;
      inputs.base_url.value = native.base_url;
      inputs.model.value = native.model;
    } else if (!blocked) {
      inputs.name.value = "";
      inputs.base_url.value = "";
      inputs.model.value = "";
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    // A built-in provider has no base URL to store: the key goes to its own
    // route, which knows the endpoint and the default model.
    const target = native ? native.endpoint : "/api/providers/custom";
    const payload = native
      ? { key: inputs.key.value }
      : {
          name: inputs.name.value.trim(),
          base_url: inputs.base_url.value.trim(),
          model: inputs.model.value.trim(),
          key: inputs.key.value,
        };
    const savedName = native ? native.name : payload.name;
    inputs.key.value = ""; // cleared before sending: never re-displayed
    try {
      await api(target, { method: "POST", body: JSON.stringify(payload) });
      result.textContent = `saved ${savedName}`;
      result.className = "test-result ok";
      await loadProviders();
    } catch (error) {
      result.textContent = `error: ${error.message}`;
      result.className = "test-result warn";
    }
  });

  card.appendChild(form);
  return card;
}

function renderKeysPanel(keys) {
  const box = $("keys-priority");
  box.textContent = "";
  if (!keys) {
    box.appendChild(el("div", "panel-hint", "key state unavailable"));
    return;
  }
  if (state.keyEntryAllowed) box.appendChild(presetCard(state.presets));
  box.appendChild(cloudflareCard(keys.cloudflare || {}));
  box.appendChild(deepseekCard(keys.deepseek || {}));
  box.appendChild(omnirouteCard(keys.omniroute || {}));
}

// --- hardware / models tab ---------------------------------------------------
async function loadCapabilities() {
  const box = $("hardware-card");
  try {
    const data = await api("/api/system/capabilities");
    const models = await api("/api/models");
    box.textContent = "";

    box.appendChild(el("p", "hardware-sentence", data.sentence));
    if (data.recommended_model) {
      const installed = models.ollama || [];
      const pulling = models.pulling || [];
      const row = el("div", "hardware-model");
      row.appendChild(el("span", "hardware-model-name", data.recommended_model));
      row.appendChild(
        el(
          "span",
          "hardware-model-meta",
          `context ${Math.round(data.recommended_budget / 1000)}k · ${installed.includes(
            data.recommended_model
          ) ? "installed" : pulling.includes(data.recommended_model) ? "downloading…" : "not installed"}`
        )
      );
      if (!installed.includes(data.recommended_model) && !pulling.includes(data.recommended_model)) {
        const pull = el("button", "hardware-pull", "Download");
        pull.addEventListener("click", async () => {
          pull.disabled = true;
          pull.textContent = "starting…";
          await api("/api/models/pull", {
            method: "POST",
            body: JSON.stringify({ name: data.recommended_model }),
          });
          setTimeout(() => loadCapabilities().catch(() => {}), 3000);
        });
        row.appendChild(pull);
      }
      box.appendChild(row);
    }
    const facts = el("dl", "hardware-facts");
    for (const [label, value] of [
      ["RAM", `${data.ram_gb} GB`],
      ["cores", String(data.cpu_cores)],
      ["GPU", data.gpu || "none"],
      ["platform", `${data.os} ${data.arch}`],
    ]) {
      facts.appendChild(el("dt", null, label));
      facts.appendChild(el("dd", null, value));
    }
    box.appendChild(facts);

    // Ollama is a boost for people who already have it. Three states, no red,
    // no install button: the sentence below is the whole product statement.
    const ollama = await api("/api/system/ollama").catch(() => null);
    if (ollama) {
      const LABELS = {
        active: ["⚡ Boost actif", "boost-active"],
        available: ["Boost disponible", "boost-available"],
        unavailable: ["Boost local indisponible", "boost-unavailable"],
      };
      const [label, style] = LABELS[ollama.boost] || LABELS.unavailable;
      const row = el("div", `boost ${style}`);
      row.appendChild(el("span", "boost-badge", label));
      row.appendChild(
        el(
          "span",
          "boost-meta",
          ollama.running && ollama.models.length
            ? ollama.models.slice(0, 2).join(", ")
            : ollama.reason || ""
        )
      );
      box.appendChild(row);
      box.appendChild(
        el(
          "p",
          "boost-sentence",
          "Ollama est optionnel. UH ne le telecharge ni ne l'exige - il accelere et reduit le cout quand il est deja installe."
        )
      );
    }

    if (!data.local_possible) {
      box.appendChild(
        el(
          "p",
          "hardware-warning",
          "No local model fits here: configure a remote provider in the Providers tab."
        )
      );
    }
  } catch (error) {
    box.textContent = "";
    box.appendChild(el("div", "panel-hint", `error: ${error.message}`));
  }
}

// --- second brain (UHB, proxied by UH) ---------------------------------------
const TIER_COLOURS = { free: "var(--accent)", eco: "var(--highlight)", paid: "var(--error)" };

//: One automatic attempt per page load. UHB is a real process: retrying a dead
//: one in a loop would be a fork bomb with good intentions.
let uhbAutoTried = false;

async function connectUhb(box, button, out) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Connexion… (jusqu'à 25 s)";
  out.hidden = false;
  out.textContent = "";
  out.appendChild(el("p", "panel-hint", "UH démarre UHB en arrière-plan."));
  try {
    const result = await api("/api/uhb/start", { method: "POST" });
    if (result.connected) {
      await loadUhb();
      return;
    }
    out.textContent = "";
    out.appendChild(
      el("p", "uhb-offline", `Toujours injoignable : ${result.reason || "raison inconnue"}`)
    );
    out.appendChild(el("pre", "uhb-log", result.log || "journal vide"));
  } catch (error) {
    out.textContent = "";
    out.appendChild(el("p", "uhb-offline", `Le démarrage a échoué : ${error.message}`));
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function loadUhb() {
  const box = $("uhb-panel");
  box.textContent = "";
  let data;
  try {
    data = await api("/api/uhb/status");
  } catch (error) {
    box.appendChild(el("p", "panel-hint", `UHB: ${error.message}`));
    return;
  }
  if (!data.connected) {
    // The user should not have to run a shell command to switch on a panel they
    // can see. One button, one attempt, and the log only if it fails.
    box.appendChild(el("p", "uhb-offline", "Le second cerveau n'est pas joignable."));
    box.appendChild(
      el(
        "p",
        "panel-hint",
        "Il apporte SiYuan, le RAG et le routage des modèles. UH fonctionne sans lui — le chat et les outils restent disponibles."
      )
    );
    const actions = el("div", "uhb-actions");
    const connect = el("button", "data-button primary", "Connecter le second cerveau");
    const details = el("button", "data-button", "Détails");
    const out = el("div", "uhb-decision");
    out.hidden = true;
    actions.appendChild(connect);
    actions.appendChild(details);
    box.appendChild(actions);
    box.appendChild(out);

    connect.addEventListener("click", () => connectUhb(box, connect, out));
    details.addEventListener("click", () => {
      out.hidden = !out.hidden;
      if (!out.hidden) {
        out.textContent = "";
        out.appendChild(el("p", "panel-hint", `raison : ${data.reason || "inconnue"}`));
        if (data.checkout) out.appendChild(el("p", "panel-hint", `dossier : ${data.checkout}`));
        out.appendChild(el("pre", "uhb-log", data.log || "journal vide"));
      }
    });
    // Opening the panel is the intent to use it: try once, silently.
    if (!uhbAutoTried && !String(data.reason || "").includes("disabled")) {
      uhbAutoTried = true;
      setTimeout(() => connectUhb(box, connect, out), 500);
    }
    return;
  }
  // Connected is one line: a dot, a version, a mode. What UHB is and what it
  // brings is a paragraph nobody reads twice - it lives behind "Détails".
  const head = el("p", "uhb-connected");
  head.appendChild(el("span", "dot dot-ok"));
  head.appendChild(
    el("span", null, `Connecté · v${data.version || "?"} · mode ${data.mode || "?"}`)
  );
  box.appendChild(head);

  const facts = el("dl", "hardware-facts");
  facts.hidden = true;
  for (const [label, value] of [
    ["harness", data.harness || "?"],
    ["providers", (data.providers || []).join(", ") || "none"],
    ["modes", (data.modes || []).join(", ") || "none"],
  ]) {
    facts.appendChild(el("dt", null, label));
    facts.appendChild(el("dd", null, String(value)));
  }
  const factsToggle = el("button", "data-button", "Détails");
  factsToggle.type = "button";
  factsToggle.addEventListener("click", () => {
    facts.hidden = !facts.hidden;
  });
  box.appendChild(factsToggle);
  box.appendChild(facts);

  // A backend the user cannot switch off is one they will kill from the task
  // manager instead. Chat and tools keep working without it.
  const stopUhb = el("button", "data-button", "Arrêter le second cerveau");
  stopUhb.type = "button";
  stopUhb.addEventListener("click", async () => {
    stopUhb.disabled = true;
    stopUhb.textContent = "Arrêt…";
    try {
      await api("/api/uhb/stop", { method: "POST" });
      await loadUhb();
    } catch (error) {
      stopUhb.disabled = false;
      stopUhb.textContent = "Arrêter le second cerveau";
      box.appendChild(el("p", "uhb-offline", `Arrêt impossible : ${error.message}`));
    }
  });
  box.appendChild(stopUhb);

  const test = el("button", "hardware-pull", "Test routing");
  const out = el("div", "uhb-decision");
  test.addEventListener("click", async () => {
    const prompt = $("input").value.trim() || "test";
    test.disabled = true;
    out.textContent = "";
    try {
      const plan = await api("/api/uhb/route", {
        method: "POST",
        body: JSON.stringify({ prompt }),
      });
      out.appendChild(
        el(
          "p",
          "uhb-line",
          `${plan.complexity} · ${plan.reason || "no reason given"}`
        )
      );
      for (const candidate of plan.chain || []) {
        const row = el("div", "uhb-candidate");
        const tier = String(candidate.tier || "?");
        const dot = el("span", "uhb-dot");
        dot.style.background = TIER_COLOURS[tier] || "var(--muted)";  // free/eco/paid
        row.appendChild(dot);
        row.appendChild(el("span", "uhb-ref", candidate.ref || candidate.model_id || "?"));
        row.appendChild(el("span", "uhb-tier", tier));
        out.appendChild(row);
      }
      if (!(plan.chain || []).length) out.appendChild(el("p", "panel-hint", "no chain"));
    } catch (error) {
      out.appendChild(el("p", "panel-hint", `route: ${error.message}`));
    } finally {
      test.disabled = false;
    }
  });
  box.appendChild(test);
  box.appendChild(out);
}

// --- command chips -----------------------------------------------------------
// Clicking inserts the command, it does not run it: the user may want to add an
// argument, and a web composer that fires on one click is a way to lose a draft.
const CHIPS = [
  ["🔧", "/tools", "List active toolsets"],
  ["🌐", "/web", "Chercher sur le web, ou ouvrir l'interface"],
  ["➕", "/new", "Démarrer une nouvelle session"],
  ["🧹", "/clear", "Effacer cette conversation"],
  ["❓", "/help", "List commands"],
];

function wireChips() {
  const row = $("command-chips");
  for (const [icon, command, title] of CHIPS) {
    const chip = el("button", "chip", `${icon} ${command}`);
    chip.title = title;
    chip.addEventListener("click", () => {
      const input = $("input");
      input.value = `${command} `;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
    row.appendChild(chip);
  }
}

// --- memory (P3.2): wiki, search, RAG/SiYuan state, MEMORY.md ---------------
// Everything here is read-only. A source that cannot be read says why, and a
// button that could not work is simply not drawn.
function memorySection(title) {
  const section = el("section", "memory-section");
  section.appendChild(el("h3", "memory-title", title));
  return section;
}

function memoryNoteList(notes, reader) {
  const list = el("div", "memory-note-list");
  if (!notes.length) {
    list.appendChild(el("p", "panel-hint", "no notes in this wiki"));
    return list;
  }
  for (const note of notes) {
    const row = el("button", "memory-note");
    row.appendChild(el("span", "memory-note-name", note.path));
    row.appendChild(el("span", "memory-note-meta", `${Math.round(note.size / 1024)} KB`));
    row.addEventListener("click", async () => {
      reader.textContent = "loading…";
      try {
        const data = await api(`/api/memory/wiki/${note.path}`);
        reader.textContent = data.text;
      } catch (error) {
        reader.textContent = `error: ${error.message}`;
      }
    });
    list.appendChild(row);
  }
  return list;
}

function stateBlock(pairs, note) {
  const box = el("div", "memory-state");
  for (const [label, value] of pairs) {
    if (value === undefined || value === null || value === "") continue;
    box.appendChild(el("span", "memory-state-label", label));
    box.appendChild(el("span", "memory-state-value", String(value)));
  }
  if (note) box.appendChild(el("p", "memory-state-note", note));
  return box;
}

async function runMemorySearch(query, results) {
  results.textContent = "searching…";
  try {
    const data = await api(`/api/memory/search?q=${encodeURIComponent(query)}`);
    results.textContent = "";
    if (!data.hits.length) {
      results.appendChild(el("p", "panel-hint", "no hit"));
      return;
    }
    for (const kind of ["wiki", "session", "memory"]) {
      const group = data.hits.filter((hit) => hit.kind === kind);
      if (!group.length) continue;
      results.appendChild(el("h4", "memory-group", kind === "session" ? "Sessions" : kind === "wiki" ? "Wiki" : "MEMORY.md"));
      for (const hit of group) {
        const row = el("div", "memory-hit");
        row.appendChild(el("span", "memory-hit-title", hit.title || hit.path));
        row.appendChild(el("p", "memory-hit-snippet", hit.snippet || ""));
        results.appendChild(row);
      }
    }
  } catch (error) {
    results.textContent = `error: ${error.message}`;
  }
}

async function loadMemory() {
  const box = $("memory-panel");
  box.textContent = "";
  let data;
  let uhb = { connected: false };
  try {
    data = await api("/api/memory/sources");
  } catch (error) {
    box.appendChild(el("p", "panel-hint", `memory: ${error.message}`));
    return;
  }
  try {
    uhb = await api("/api/uhb/status");
  } catch {
    uhb = { connected: false };
  }

  if (!uhb.connected) {
    box.appendChild(
      el(
        "p",
        "memory-banner",
        "UHB offline: the wiki folder and UH's own store still work; RAG and SiYuan do not."
      )
    );
  }

  // -- search ---------------------------------------------------------------
  const search = memorySection("Recherche");
  const form = el("form", "memory-search");
  const input = el("input", "memory-search-input");
  input.id = "memory-search";
  input.name = "memory-search";
  input.placeholder = "search the wiki, MEMORY.md and the sessions";
  input.setAttribute("aria-label", "Rechercher dans la mémoire");
  const go = el("button", null, "Rechercher");
  go.type = "submit";
  form.appendChild(input);
  form.appendChild(go);
  const results = el("div", "memory-results");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (query) runMemorySearch(query, results);
  });
  search.appendChild(form);
  search.appendChild(results);
  box.appendChild(search);

  // -- wiki -----------------------------------------------------------------
  const wiki = memorySection(`Wiki${data.wiki.notes ? ` (${data.wiki.notes})` : ""}`);
  if (data.wiki.available) {
    let listing = { notes: [] };
    try {
      listing = await api("/api/memory/wiki");
    } catch (error) {
      wiki.appendChild(el("p", "panel-hint", `wiki: ${error.message}`));
    }
    const reader = el("pre", "memory-reader");
    reader.textContent = "select a note";
    wiki.appendChild(el("p", "memory-state-note", data.wiki.path || ""));
    wiki.appendChild(memoryNoteList(listing.notes || [], reader));
    wiki.appendChild(reader);
  } else {
    wiki.appendChild(el("p", "panel-hint", data.wiki.reason || "no wiki found"));
  }
  box.appendChild(wiki);

  // -- MEMORY.md ------------------------------------------------------------
  const memory = memorySection("MEMORY.md");
  if (data.memory.available) {
    const sections = Object.entries(data.memory.bytes_by_section || {});
    memory.appendChild(
      stateBlock(
        sections.map(([name, size]) => [name, `${size} B`]),
        data.memory.path
      )
    );
    if (!sections.length) memory.appendChild(el("p", "panel-hint", "the file exists but has no content"));
  } else {
    memory.appendChild(
      el(
        "p",
        "panel-hint",
        "MEMORY.md not created yet - it appears after the first memory_write."
      )
    );
  }
  box.appendChild(memory);

  // -- RAG ------------------------------------------------------------------
  const rag = memorySection("RAG");
  // Two shapes describe the same thing: the local disk scan (`available`,
  // `index_mb`) and UHB's view of the service (`built`, `index_bytes`, `chunks`).
  const ragState = data.rag || {};
  const ragBuilt = Boolean(ragState.available || ragState.built || ragState.chunks > 0);
  const size = ragState.index_mb
    ? `${ragState.index_mb} MB`
    : ragState.index_bytes
      ? `${Math.round(ragState.index_bytes / (1024 * 1024))} MB`
      : "";
  rag.appendChild(
    stateBlock(
      [
        ["index", ragBuilt ? `built (${ragState.source || ragState.via || "disk"})` : "not built"],
        ["size", size],
        ["chunks", ragState.chunks],
        ["documents", ragState.documents],
        ["modified", ragState.modified || ragState.last_indexed],
      ],
      ragState.reason
    )
  );
  if (!ragBuilt) {
    rag.appendChild(
      el(
        "p",
        "panel-hint",
        "RAG not built yet - the index is compiled from your own notes; see uhb/services/rag/README.md."
      )
    );
  }
  // The only real action that exists: ask the running service to re-read the
  // index it already has. The button says reload because that is what happens.
  const reload = el("button", "hardware-pull", "Reload RAG");
  const reloadState = el("p", "memory-state-note", "");
  reload.addEventListener("click", async () => {
    reload.disabled = true;
    reload.textContent = "reloading…";
    reloadState.className = "memory-state-note";
    reloadState.textContent = "";
    const answer = await apiResult("/api/memory/rag/reload", { method: "POST" });
    reload.disabled = false;
    reload.textContent = "Reload RAG";
    if (answer.ok) {
      const chunks = (answer.body.service && answer.body.service.fragments) || "?";
      const when = new Date(answer.body.at).toLocaleTimeString();
      reloadState.className = "memory-state-note memory-ok";
      reloadState.textContent = `Reloaded at ${when} · ${chunks} chunks`;
      setTimeout(() => {
        reloadState.textContent = "";
        reloadState.className = "memory-state-note";
      }, 5000);
      return;
    }
    const body = answer.body || {};
    reloadState.className = "memory-state-note memory-error";
    if (body.kind === "service_offline") {
      reloadState.textContent = body.hint || "the RAG service is not running";
    } else if (body.kind === "uhb_offline") {
      reloadState.textContent = "UHB offline - memory actions unavailable";
    } else {
      reloadState.textContent = body.error || `rechargement impossible : ${httpErrorText(answer.status)}`;
    }
  });
  const ragAction = el("div", "memory-action");
  ragAction.appendChild(reload);
  ragAction.appendChild(reloadState);
  rag.appendChild(ragAction);
  box.appendChild(rag);

  // -- SiYuan ---------------------------------------------------------------
  const siyuan = memorySection("SiYuan");
  const siyuanState = data.siyuan || {};
  siyuan.appendChild(
    stateBlock(
      [
        ["configured", siyuanState.configured ? "yes" : "no"],
        ["url", siyuanState.url || ""],
      ],
      siyuanState.reason
    )
  );
  // Blocks are read through UH, never straight from the kernel: the CSP is
  // 'self' and the token stays on the server side.
  const pathInput = el("input", "memory-path-input");
  pathInput.id = "memory-path";
  pathInput.name = "memory-path";
  pathInput.value = "veille";
  pathInput.placeholder = "path (hpath filter)";
  pathInput.setAttribute("aria-label", "SiYuan path filter");
  const load = el("button", "hardware-pull", "Load blocks");
  const list = el("div", "memory-blocks");
  const note = el("p", "memory-state-note", "");
  load.addEventListener("click", async () => {
    load.disabled = true;
    load.textContent = "loading…";
    note.className = "memory-state-note";
    note.textContent = "";
    list.textContent = "";
    const path = pathInput.value.trim();
    const query = path ? `?path=${encodeURIComponent(path)}&limit=20` : "?limit=20";
    const answer = await apiResult(`/api/memory/siyuan/blocks${query}`);
    load.disabled = false;
    load.textContent = "Load blocks";
    if (answer.ok) {
      const blocks = answer.body.blocks || [];
      note.textContent = `${answer.body.count || 0} block(s)${path ? ` in ${path}` : ""}`;
      for (const block of blocks) {
        const row = el("div", "memory-block");
        row.appendChild(el("span", "memory-block-type", String(block.type || "?")));
        row.appendChild(el("span", "memory-block-path", String(block.hpath || "")));
        row.appendChild(el("span", "memory-block-content", String(block.content || "")));
        list.appendChild(row);
      }
      if (!blocks.length) list.appendChild(el("p", "panel-hint", "no block for this path"));
      return;
    }
    const body = answer.body || {};
    note.className = "memory-state-note memory-error";
    if (body.kind === "config_missing") {
      note.textContent = body.hint || "SIYUAN_TOKEN not set";
    } else if (body.kind === "auth") {
      note.textContent = "SiYuan rejected the token - check SIYUAN_TOKEN";
    } else if (body.kind === "uhb_offline") {
      note.textContent = "UHB offline - memory actions unavailable";
    } else {
      note.textContent = body.error || `blocs indisponibles : ${httpErrorText(answer.status)}`;
    }
  });
  const siyuanForm = el("div", "memory-action");
  siyuanForm.appendChild(pathInput);
  siyuanForm.appendChild(load);
  siyuan.appendChild(siyuanForm);
  siyuan.appendChild(note);
  siyuan.appendChild(list);
  box.appendChild(siyuan);
}

// --- decision (P3.3): why the last turn looked the way it did ----------------
function decisionBlock(title, body) {
  const block = el("section", "decision-block");
  block.appendChild(el("h3", "decision-title", title));
  block.appendChild(body);
  return block;
}

function tierDot(tier) {
  const dot = el("span", "uhb-dot");
  dot.style.background = TIER_COLOURS[tier] || "var(--muted)";
  return dot;
}

// --- execution mode (UHB) -------------------------------------------------- //
// The mode belongs to the UHB PROCESS (`--mode`), so "switch to dev" means
// restarting it - there is no route on the server to change it while it runs.
// This control lives in the panel the assistant already sends people to
// ("va dans Décision et passe en dev") and which had no control at all. UH only
// restarts a process it owns; a foreign UHB is reported, never killed.
// Un seul vocabulaire, et le nom technique à côté : l'onglet, l'en-tête et UHB
// doivent dire la même chose. « Atelier » seul laissait le modèle parler de « mode
// dev » sans que l'opérateur puisse faire le lien.
const MODE_LABELS = {
  chat: ["Discussion (chat)", "lecture seule : lit et cherche, n'exécute rien"],
  dev: ["Atelier (dev)", "exécute du code, écriture limitée au dossier du run"],
  review: ["Relecture (review)", "lit et commente, n'exécute pas"],
  observe: ["Observation (observe)", "observe et rapporte, ne modifie rien"],
};

async function renderExecutionMode() {
  const host = $("decision-panel");
  if (!host) return;
  let card = $("execution-mode");
  if (!card) {
    // A SIBLING of #decision-panel, not a child: `renderDecision` clears that
    // panel on every reload (`box.textContent = ""`) and used to take the mode
    // control with it.
    card = el("div", "panel-card mode-card");
    card.id = "execution-mode";
    host.parentNode.insertBefore(card, host);
  }
  card.textContent = "";
  card.appendChild(el("div", "panel-label", "Mode d'exécution"));

  let status = {};
  try {
    status = await api("/api/uhb/status");
  } catch (error) {
    card.appendChild(el("div", "panel-hint", `second cerveau indisponible : ${error.message}`));
    return;
  }
  const mode = String(status.mode || "");
  const label = MODE_LABELS[mode];
  card.appendChild(
    el(
      "div",
      "panel-hint",
      label ? `${label[0]} — ${label[1]}` : status.connected ? `mode « ${mode} »` : "second cerveau arrêté"
    )
  );
  card.appendChild(
    el(
      "div",
      "panel-hint",
      "l'exécution de code exige le mode Atelier ; un mode se change en redémarrant le second cerveau, c'est UH qui s'en charge"
    )
  );
  const row = el("div", "mode-row");
  for (const value of ["chat", "dev"]) {
    const active = value === mode;
    const button = el("button", "mode-btn" + (active ? " active" : ""), MODE_LABELS[value][0]);
    button.type = "button";
    button.dataset.mode = value;
    button.title = MODE_LABELS[value][1];
    button.setAttribute("aria-pressed", String(active));
    button.disabled = active;
    button.addEventListener("click", () => switchExecutionMode(value, card));
    row.appendChild(button);
  }
  card.appendChild(row);
}

async function switchExecutionMode(mode, card) {
  const hint = el("div", "panel-hint", "changement de mode…");
  card.appendChild(hint);
  try {
    const answer = await api("/api/uhb/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
    hint.textContent = answer.changed
      ? `mode ${MODE_LABELS[mode][0]} actif : renvoie ton message, l'outil est maintenant proposé.`
      : "déjà dans ce mode.";
    await renderExecutionMode();
  } catch (error) {
    hint.textContent = error.message;
  }
}

// --- mode d'exécution : badge permanent + carte de refus -------------------- //
// A capability the user cannot see is a capability they cannot grant. The badge is
// in the header on every screen; the card appears in the conversation, exactly
// where the refusal happened, and its button is the only way to grant execution.
const MODE_BADGE_TEXT = {
  chat: "Discussion (chat) : lecture seule",
  // Measured, not hoped: the workshop interpreter carries numpy and matplotlib,
  // the audit hook refuses a write outside the run directory, the credential
  // files and any subprocess - proven by a counter-probe on 19/11 (an attempt on
  // each of the four is refused, a savefig in the run is kept). A win32 call made
  // through ctypes is still outside that hook; the sentence says what is confined.
  dev: "Atelier (dev) : exécute du code (dossier du run uniquement)",
  review: "Relecture (review) : lit, n'exécute pas",
  observe: "Observation : rapporte seulement",
};

//: The four states of the second brain, said with an icon AND words AND a colour
//: (never the colour alone). `degraded` = reachable but reporting itself unhealthy.
const UHB_STATES = {
  dev: {
    icon: "✎",
    text: "Dev · écriture",
    full: "Connecté (Mode Dev) : écriture et dessin actifs",
  },
  chat: {
    icon: "◉",
    text: "Chat · lecture seule",
    full: "Connecté (Mode Chat) : lecture seule",
  },
  degraded: {
    icon: "⚠",
    text: "Dégradé",
    full: "Second cerveau dégradé : certaines fonctions peuvent manquer",
  },
  offline: {
    icon: "✖",
    text: "Hors ligne",
    full: "Second cerveau hors ligne",
  },
};

function uhbStateOf(status) {
  if (!status || !status.connected) return "offline";
  if (status.degraded === true || status.ok === false || status.healthy === false) return "degraded";
  const mode = String(status.mode || "");
  return mode === "dev" ? "dev" : "chat";
}

async function refreshModeBadge() {
  const badge = $("mode-badge");
  if (!badge) return;
  let status = {};
  try {
    status = await api("/api/uhb/status");
  } catch (error) {
    status = { connected: false };
  }
  const mode = String(status.mode || "");
  const key = uhbStateOf(status);
  const view = UHB_STATES[key];
  // review / observe keep their own wording: they are connected, read-only variants.
  const text = key === "chat" && MODE_BADGE_TEXT[mode] && mode !== "chat" ? MODE_BADGE_TEXT[mode] : view.text;
  const full = text === view.text ? view.full : `Connecté : ${MODE_BADGE_TEXT[mode]}`;
  $("mode-badge-label").textContent = text;
  const dot = badge.querySelector(".mode-badge-dot");
  if (dot) dot.textContent = view.icon;
  badge.dataset.mode = key === "offline" ? "" : mode;
  badge.dataset.uhb = key;
  badge.classList.toggle("readonly", key === "chat");
  badge.classList.toggle("agent", key === "dev");
  badge.classList.toggle("degraded", key === "degraded");
  badge.classList.toggle("offline", key === "offline");
  badge.setAttribute("aria-label", `Second cerveau : ${full}`);
  badge.title =
    key === "offline"
      ? "Le second cerveau ne répond pas. Cliquez pour ouvrir son panneau et le démarrer."
      : key === "degraded"
        ? "Le second cerveau répond mais signale un problème. Cliquez pour ouvrir son panneau."
        : mode === "chat"
          ? "Lecture seule. Cliquez pour autoriser l'exécution de code (mode Atelier)."
          : "Ce mode peut exécuter du code. Cliquez pour revenir en lecture seule.";
}

async function toggleModeFromBadge() {
  const badge = $("mode-badge");
  // Offline or degraded: switching a mode is meaningless, show the panel that can fix it.
  if (badge && (badge.dataset.uhb === "offline" || badge.dataset.uhb === "degraded")) {
    showPanelTab("uhb");
    return;
  }
  const target = badge && badge.dataset.mode === "dev" ? "chat" : "dev";
  await applyMode(target);
  await refreshModeBadge();
}

// One call, used by the badge and by the refusal card: switch, then report.
async function applyMode(mode) {
  const answer = await api("/api/uhb/mode", { method: "POST", body: JSON.stringify({ mode }) });
  await refreshModeBadge();
  return answer;
}

// The card that turns "switch to dev" into one click, and re-sends the turn the
// user already typed - no copy, no paste, no lost message.
function modeRefusalCard(data) {
  const card = el("div", "mode-card-inline");
  card.appendChild(
    el(
      "div",
      "mode-card-text",
      `Le second cerveau est en lecture seule, donc \`${data.tool || "l'outil"}\` n'était pas disponible. ` +
        "Rien n'a été exécuté."
    )
  );
  const button = artifactButton("keep", "Autoriser pour cette conversation", "passer en mode Atelier et relancer ce tour", null, "safe");
  button.classList.add("mode-allow");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await applyMode("dev");
      card.remove();
      const again = state.lastUserText;
      if (again) await sendMessage(again);
    } catch (error) {
      button.disabled = false;
      card.appendChild(el("div", "tool-artifact-note", `refusé : ${error.message}`));
    }
  });
  card.appendChild(button);
  $("messages").appendChild(card);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function renderDecision(data, reload) {
  const box = $("decision-panel");
  box.textContent = "";

  if (!data.available) {
    // Two different silences: no session at all, or a session whose turns were
    // never recorded. The route's reason is English and internal; say the thing
    // the person can act on, and keep the raw reason as a tooltip.
    const reason = String(data.reason || "");
    const text = /no turn recorded/i.test(reason)
      ? "Aucun tour enregistré pour cette session : cette vue se remplit après la première réponse du modèle."
      : "Cette vue montre comment UH choisit le modèle, ce qu'il essaie en premier et ce qu'il consomme. Envoyez un message, puis revenez ici.";
    const hint = el("p", "panel-hint", text);
    if (reason) hint.title = reason;
    box.appendChild(hint);
    return;
  }

  // 1 - the prompt, as classified (or the honest absence)
  const prompt = data.prompt || {};
  const promptBody = el("div", "decision-body");
  if (prompt.complexity) {
    promptBody.appendChild(el("p", "decision-headline", String(prompt.complexity)));
    if (prompt.mode) promptBody.appendChild(el("p", "decision-sub", `Mode : ${prompt.mode}`));
    if (prompt.reason) promptBody.appendChild(el("p", "decision-note", prompt.reason));
  } else {
    const absent = el(
      "p",
      "decision-note",
      "Le second cerveau (UHB) n'a pas été consulté pour ce tour : le modèle a répondu seul, sans conseil de routage."
    );
    absent.title = String(prompt.note || "UHB was not consulted for this turn: no uhb_route call");
    promptBody.appendChild(absent);
  }
  box.appendChild(decisionBlock("Votre demande", promptBody));

  // 2 - the model that answered
  const model = data.model || {};
  const modelBody = el("div", "decision-body");
  const chosen = el("p", "decision-headline");
  chosen.appendChild(el("span", "decision-model", String(model.model || "?")));
  chosen.appendChild(el("span", "decision-sub", `${model.provider || "?"}`));
  modelBody.appendChild(chosen);
  if (model.substituted) {
    // An alias resolved to another tag: say it, do not hide it.
    modelBody.appendChild(
      el("p", "decision-note", `Modèle demandé : ${model.chosen || model.model} · modèle réellement servi : ${model.served}`)
    );
  }
  box.appendChild(decisionBlock("Modèle qui a répondu", modelBody));

  // 3 - the chain, in order, chosen first
  const chainBody = el("div", "decision-body");
  if ((data.chain || []).length) {
    for (const candidate of data.chain) {
      const row = el("div", "uhb-candidate");
      row.appendChild(tierDot(candidate.tier));
      row.appendChild(el("span", "uhb-ref", String(candidate.ref || "?")));
      row.appendChild(el("span", "uhb-tier", String(candidate.tier || "?")));
      if (candidate.ref && candidate.ref === data.model.chosen) row.classList.add("chosen");
      chainBody.appendChild(row);
    }
  } else {
    const none = el(
      "p",
      "decision-note",
      "Aucun ordre de repli prévu : le second cerveau n'a pas planifié ce tour, donc un seul modèle a été essayé."
    );
    none.title = "no chain: UHB did not plan this turn";
    chainBody.appendChild(none);
  }
  box.appendChild(decisionBlock("Modèles essayés, dans l'ordre", chainBody));

  // 4 - context: what was sent, against the budget
  const context = data.context || {};
  const usage = data.usage || {};
  const contextBody = el("div", "decision-body");
  const track = el("div", "decision-bar");
  const fill = el("div", "decision-bar-fill");
  // The bar compares the payload the pruner measured with the budget it had to
  // respect. The usage counters below are a different quantity: they sum every
  // call of the turn, so using them here would show a false overflow.
  // The gauge tracks what the pruner measured. Turns recorded before 1.2.0
  // carry no payload size, and showing "0 / 300" next to 1573 tokens counted by
  // the provider reads like a bug in the pruner: fall back to the provider's own
  // count and say which number is on screen.
  const payload = Number(context.payload_tokens || 0);
  const budget = Number(context.budget || 0);
  const counted = Number(usage.prompt || 0);
  const measured = payload > 0 ? payload : counted;
  fill.style.width = `${budget > 0 ? Math.min(100, Math.round((measured / budget) * 100)) : 0}%`;
  track.appendChild(fill);
  contextBody.appendChild(track);
  const percent = budget > 0 ? Math.min(100, Math.round((measured / budget) * 100)) : 0;
  const plain = el(
    "p",
    "decision-note",
    budget > 0
      ? `Mémoire de la conversation utilisée : ${percent} % de ce que le modèle peut lire.`
      : "Taille de la conversation envoyée au modèle : non mesurée pour ce tour."
  );
  plain.title = "payload / budget, en tokens (un token ≈ 4 caractères)";
  contextBody.appendChild(plain);
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", "Part de la mémoire de conversation utilisée");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  track.setAttribute("aria-valuenow", String(percent));
  // The numbers stay one click away, for whoever needs them.
  const tech = el("details", "decision-tech");
  tech.appendChild(el("summary", null, "Détails techniques"));
  tech.appendChild(
    el(
      "p",
      "decision-note",
      payload > 0
        ? `Charge envoyée : ${payload} / ${budget || "?"} tokens · ${data.tool_calls || 0} appel(s) d'outil`
        : `Charge non enregistrée pour ce tour (avant la 1.2.0) · comptée par le fournisseur : ${counted} / ${
            budget || "?"
          } tokens`
    )
  );
  tech.appendChild(
    el(
      "p",
      "decision-note",
      usage.reported
        ? `Tokens lus : ${usage.prompt} · tokens écrits : ${usage.completion}`
        : "Le fournisseur n'a pas indiqué ses compteurs."
    )
  );
  contextBody.appendChild(tech);
  if (context.mode === "summary" && context.summarised_exchanges) {
    contextBody.appendChild(
      el(
        "p",
        "decision-note",
        `${context.summarised_exchanges} échanges anciens résumés · ${context.kept_exchanges} gardés tels quels`
      )
    );
  } else if (context.mode === "trim") {
    // The middle was dropped, not summarised: saying "summarised" would describe
    // text the model never received.
    contextBody.appendChild(
      el("p", "decision-note", "Le milieu de la conversation a été retiré sans résumé (mémoire trop petite pour ce modèle).")
    );
  }
  box.appendChild(decisionBlock("Mémoire de la conversation", contextBody));

  // 5 - the summary itself, collapsed, because it replaced messages
  if (context.summary) {
    const summaryBody = el("div", "decision-body");
    const details = el("details", "decision-summary");
    details.appendChild(el("summary", null, `Résumé qui remplace ${context.summarised_exchanges} échanges`));
    details.appendChild(el("pre", "memory-reader", context.summary));
    summaryBody.appendChild(details);
    box.appendChild(decisionBlock("Résumé de la conversation", summaryBody));
  }

  // 6 - duration, this turn and the average
  const turn = data.turn || {};
  const durationBody = el("div", "decision-body");
  durationBody.appendChild(el("p", "decision-headline", `${(Number(turn.duration_ms || 0) / 1000).toFixed(2)} s`));
  durationBody.appendChild(
    el(
      "p",
      "decision-note",
      `Moyenne sur les ${turn.turns || 0} derniers tours : ${(Number(turn.average_ms || 0) / 1000).toFixed(2)} s`
    )
  );
  box.appendChild(decisionBlock("Durée de la réponse", durationBody));

  // A plan UHB would give now, clearly marked as not applied.
  const ask = el("button", "hardware-pull", "Demander conseil au second cerveau pour cette demande");
  const out = el("div", "decision-body");
  ask.addEventListener("click", async () => {
    ask.disabled = true;
    out.textContent = "";
    try {
      const last = [...document.querySelectorAll(".msg.user")].pop();
      const promptText = (last && last.textContent.trim()) || "test";
      const plan = await api("/api/uhb/route", {
        method: "POST",
        body: JSON.stringify({ prompt: promptText }),
      });
      out.appendChild(el("p", "decision-note", "not applied to the turn: a plan only"));
      for (const candidate of plan.chain || []) {
        const row = el("div", "uhb-candidate");
        row.appendChild(tierDot(candidate.tier));
        row.appendChild(el("span", "uhb-ref", String(candidate.ref || "?")));
        row.appendChild(el("span", "uhb-tier", String(candidate.tier || "?")));
        out.appendChild(row);
      }
    } catch (error) {
      out.appendChild(el("p", "panel-hint", `route: ${error.message}`));
    } finally {
      ask.disabled = false;
    }
  });
  const extras = el("div", "decision-extra");
  extras.appendChild(ask);
  extras.appendChild(out);
  box.appendChild(extras);
}

async function loadDecision() {
  const box = $("decision-panel");
  box.textContent = "";
  if (!state.sessionId) {
    box.appendChild(
      el(
        "p",
        "panel-hint",
        "Envoyez un message : cette vue montre comment UH choisit le modèle, ce qu'il essaie en premier et ce qu'il consomme."
      )
    );
    return;
  }
  try {
    const data = await api(`/api/sessions/${state.sessionId}/decision`);
    renderDecision(data);
  } catch (error) {
    box.appendChild(el("p", "panel-hint", `decision: ${error.message}`));
  }
}

// ------------------------------------------------------------------- tools --
// The catalogue lives in UHB and is shown here verbatim. No list of tool names
// exists in this file: one copy in Python, one in JavaScript, and the two drift
// the first time a schema changes. `available` is information - the panel says
// "not allowed in this mode", it never grants anything.
function catalogCard(entry) {
  const card = el("div", "catalog-card");
  const head = el("div", "catalog-head");
  head.appendChild(el("span", "catalog-name", entry.name || "?"));
  if (entry.category) head.appendChild(el("span", "catalog-tag", entry.category));
  const allowed = entry.available === true;
  head.appendChild(
    el("span", allowed ? "catalog-state ok" : "catalog-state bad", allowed ? "disponible" : "refusé")
  );
  card.appendChild(head);
  if (entry.description) card.appendChild(el("p", "catalog-desc", entry.description));
  const modes = Array.isArray(entry.compatible_modes) ? entry.compatible_modes : [];
  if (modes.length) card.appendChild(el("div", "catalog-meta", `modes : ${modes.join(", ")}`));
  if (!allowed) {
    // The reason is a code plus a hint from UHB, never a sentence this file
    // invents: the panel translates the code, it does not guess.
    const reason = entry.reason || {};
    const text = [reason.code, reason.hint].filter(Boolean).join(" — ");
    if (text) card.appendChild(el("div", "catalog-reason", text));
  }
  if (entry.parameters && Object.keys(entry.parameters).length) {
    const details = el("details", "catalog-schema");
    details.appendChild(el("summary", "", "paramètres (JSON Schema)"));
    details.appendChild(el("pre", "", JSON.stringify(entry.parameters, null, 2)));
    card.appendChild(details);
  }
  return card;
}

function renderToolCatalog(payload) {
  const list = $("tools-list");
  list.textContent = "";
  const hint = $("tools-hint");
  if (payload.available === false) {
    // UHB absent is a state, not an empty catalogue: an empty list would read as
    // "this build has no tools", which is false and alarming.
    const reason = payload.reason || {};
    hint.textContent = `catalogue indisponible : ${reason.hint || reason.code || "UHB ne répond pas"}`;
    return;
  }
  const tools = Array.isArray(payload.tools) ? payload.tools : [];
  hint.textContent = tools.length
    ? `${tools.length} outil(s) · mode ${payload.mode || "courant"} · information, pas une autorisation`
    : "aucun outil déclaré par UHB";
  for (const entry of tools) list.appendChild(catalogCard(entry));
}

// --- gestion des Skills (P3-15) -------------------------------------------- //
// Une liste unique, deux sections : Skills fournis avec UH, et Skills de
// l'utilisateur. La case à cocher ne donne AUCUNE permission : elle décide si le
// Skill peut servir de contexte et être proposé par le routeur. L'exécution des
// outils reste autorisée ou refusée par UHB, indépendamment.
let SKILL_STATE = { skills: [], busy: false };

function skillsHint(message) {
  const hint = $("skills-hint");
  if (hint) hint.textContent = message || "";
}

function skillRow(skill) {
  const row = el("label", "skill-row");
  row.dataset.skill = String(skill.name || "");
  row.dataset.origin = String(skill.origin || "user");
  const box = el("input");
  box.type = "checkbox";
  box.name = "skill-enabled";  // pas d'id : plusieurs lignes partagent ce champ
  box.className = "skill-toggle";
  box.checked = Boolean(skill.enabled);
  box.disabled = SKILL_STATE.busy;
  box.addEventListener("change", () => {
    setSkillEnabled(skill, box.checked).catch((error) => {
      box.checked = !box.checked; // l'état affiché ne ment jamais sur le serveur
      skillsHint(error.message || "modification refusée");
    });
  });
  row.appendChild(box);
  const text = el("span", "skill-text");
  text.appendChild(el("span", "skill-name", String(skill.title || skill.name || "sans nom")));
  if (skill.description) text.appendChild(el("span", "skill-desc", String(skill.description)));
  row.appendChild(text);
  row.appendChild(el("span", "skill-origin", skill.origin === "native" ? "fourni" : "perso"));
  const actions = el("span", "skill-actions-row");
  const edit = el("button", "ghost skill-action", skill.origin === "native" ? "" : "Modifier");
  if (skill.origin !== "native") {
    edit.type = "button";
    edit.addEventListener("click", (event) => {
      event.preventDefault();
      openSkillEditor(skill).catch((error) => skillError(error.message));
    });
    actions.appendChild(edit);
  }
  const dup = el("button", "ghost skill-action", "Dupliquer");
  dup.type = "button";
  dup.addEventListener("click", (event) => {
    event.preventDefault();
    duplicateSkill(skill).catch((error) => skillError(error.message));
  });
  actions.appendChild(dup);
  if (skill.origin !== "native") {
    const del = el("button", "ghost skill-action danger", "Supprimer");
    del.type = "button";
    del.addEventListener("click", (event) => {
      event.preventDefault();
      deleteSkill(skill).catch((error) => skillError(error.message));
    });
    actions.appendChild(del);
  }
  row.appendChild(actions);
  if (!skill.enabled) row.classList.add("off");
  return row;
}

function skillSection(title, entries) {
  const box = el("div", "skill-section");
  box.appendChild(el("div", "skill-section-title", title));
  for (const skill of entries) box.appendChild(skillRow(skill));
  return box;
}

function renderSkills(data) {
  SKILL_STATE.skills = Array.isArray(data.skills) ? data.skills : [];
  const panel = $("skills-manager");
  if (!panel) return;
  panel.textContent = "";
  const natives = SKILL_STATE.skills.filter((s) => s.origin === "native");
  const mine = SKILL_STATE.skills.filter((s) => s.origin !== "native");
  if (!SKILL_STATE.skills.length) {
    panel.appendChild(el("div", "skill-empty", "aucun Skill installé"));
  }
  if (mine.length) panel.appendChild(skillSection(`Mes Skills (${mine.length})`, mine));
  if (natives.length) panel.appendChild(skillSection(`Fournis avec UH (${natives.length})`, natives));
  const off = SKILL_STATE.skills.filter((s) => !s.enabled).length;
  skillsHint(`${SKILL_STATE.skills.length} Skills · ${off} désactivé${off > 1 ? "s" : ""}`);
}

async function loadSkillsManager() {
  const panel = $("skills-manager");
  if (panel) panel.setAttribute("aria-busy", "true");
  try {
    renderSkills(await api("/api/skills"));
  } catch (error) {
    skillsHint(`liste indisponible : ${error.message}`);
  } finally {
    if (panel) panel.setAttribute("aria-busy", "false");
  }
}

async function setSkillEnabled(skill, enabled) {
  SKILL_STATE.busy = true;
  try {
    const data = await api(`/api/skills/${encodeURIComponent(skill.name)}/enabled`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
    skill.enabled = Boolean(data.enabled);
  } finally {
    // Le drapeau doit redescendre AVANT le rendu : rendu pendant busy=true, toutes
    // les cases restent désactivées et l'interface devient inutilisable (mesuré :
    // « element is not enabled » sur la case suivante).
    SKILL_STATE.busy = false;
    renderSkills({ skills: SKILL_STATE.skills });
  }
}

//: Chaîne des opérations groupées : deux clics rapprochés (« tout éteindre » puis « tout
//: rallumer ») ne doivent pas s'entrelacer. Sans elle, la seconde boucle lisait un état
//: encore en cours et chaque réponse tardive de la première ré-éteignait ce que la seconde
//: venait d'allumer — mesuré : le compteur de lignes éteintes montait de 1 à 10 en CI, et
//: 6 essais sur 6 finissaient sur un état croisé (6 ou 18 lignes éteintes au lieu de 0).
let SKILLS_BULK = Promise.resolve();

function setAllSkills(enabled) {
  const operation = SKILLS_BULK.then(() => runAllSkills(enabled));
  // La chaîne doit rester utilisable même si une opération échoue ; l'erreur, elle, remonte
  // à l'appelant par `operation`.
  SKILLS_BULK = operation.then(
    () => undefined,
    () => undefined,
  );
  return operation;
}

async function runAllSkills(enabled) {
  const cibles = SKILL_STATE.skills.filter((skill) => Boolean(skill.enabled) !== enabled);
  if (!cibles.length) {
    skillsHint(enabled ? "tous déjà activés" : "tous déjà désactivés");
    return;
  }
  SKILL_STATE.busy = true;
  skillsHint(
    enabled ? `activation de ${cibles.length} skill(s)…` : `désactivation de ${cibles.length} skill(s)…`,
  );
  try {
    // En parallèle, et sans s'arrêter au premier refus : douze PATCH en série dépassaient
    // le délai d'attente sur un runner chargé, et une erreur au milieu de la boucle
    // laissait la moitié des lignes « éteintes » sans un mot (mesuré deux fois sur
    // `e2e / windows-latest`).
    const resultats = await Promise.allSettled(
      cibles.map((skill) => setSkillEnabled(skill, enabled)),
    );
    const echecs = cibles.filter((_, index) => resultats[index].status === "rejected");
    renderSkills({ skills: SKILL_STATE.skills });
    if (echecs.length) {
      const motif = resultats.find((item) => item.status === "rejected");
      const raison = motif && motif.reason ? motif.reason.message || String(motif.reason) : "";
      skillsHint(
        `${echecs.length} skill(s) inchangé(s) : ${echecs.map((skill) => skill.name).join(", ")}${raison ? " — " + raison : ""}`,
      );
    } else {
      skillsHint(enabled ? `${cibles.length} skill(s) activé(s)` : `${cibles.length} skill(s) désactivé(s)`);
    }
  } finally {
    SKILL_STATE.busy = false;
  }
}

function skillError(message) {
  const box = $("skill-error");
  if (!box) return;
  box.hidden = !message;
  box.textContent = message || "";
}

function editorFields(data) {
  $("skill-name").value = String(data.name || "");
  $("skill-name").readOnly = Boolean(data.editing);
  $("skill-title").value = String(data.title || "");
  $("skill-description").value = String(data.description || "");
  $("skill-instructions").value = String(data.instructions || "");
}

function openSkillEditor(skill) {
  const form = $("skill-editor");
  if (!form) return Promise.resolve();
  form.hidden = true; // visible seulement une fois les champs remplis
  form.dataset.editing = skill ? String(skill.name) : "";
  $("skill-editor-title").textContent = skill ? `Modifier « ${skill.title || skill.name} »` : "Nouveau Skill";
  skillError("");
  if (!skill) {
    editorFields({ editing: false });
    form.hidden = false;
    $("skill-name").focus();
    return Promise.resolve();
  }
  // Le formulaire n'apparaît qu'après le GET : sinon une saisie rapide est écrasée
  // par les valeurs chargées (mesuré : un titre tapé juste après le clic repartait
  // avec l'ancienne valeur dans le PUT).
  return api(`/api/skills/${encodeURIComponent(skill.name)}`).then((data) => {
    editorFields({ ...data, editing: true });
    form.hidden = false;
    $("skill-title").focus();
  });
}

async function saveSkill(event) {
  event.preventDefault();
  const button = $("skill-save");
  if (button.disabled) return; // pas de double soumission
  const editing = $("skill-editor").dataset.editing || "";
  const body = {
    title: $("skill-title").value.trim(),
    description: $("skill-description").value.trim(),
    instructions: $("skill-instructions").value.trim(),
  };
  const name = $("skill-name").value.trim();
  button.disabled = true;
  skillError("");
  try {
    if (editing) {
      await api(`/api/skills/${encodeURIComponent(editing)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
    } else {
      await api("/api/skills", { method: "POST", body: JSON.stringify({ ...body, name }) });
    }
    $("skill-editor").hidden = true;
    await loadSkillsManager();
  } catch (error) {
    skillError(error.message || "enregistrement refusé");
  } finally {
    button.disabled = false;
  }
}

async function duplicateSkill(skill) {
  await api(`/api/skills/${encodeURIComponent(skill.name)}/duplicate`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  await loadSkillsManager();
}

async function deleteSkill(skill) {
  const label = skill.title || skill.name;
  if (!window.confirm(`Supprimer définitivement le Skill « ${label} » ?`)) return;
  await api(`/api/skills/${encodeURIComponent(skill.name)}`, { method: "DELETE" });
  await loadSkillsManager();
}

async function importSkill(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".md")) {
    skillError("seuls les fichiers .md sont acceptés");
    return;
  }
  const content = await file.text();
  try {
    await api("/api/skills/import", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, content }),
    });
    await loadSkillsManager();
  } catch (error) {
    skillError(error.message || "import refusé");
  }
}

function wireSkillsManager() {
  const refresh = $("skills-refresh");
  if (refresh) refresh.addEventListener("click", () => loadSkillsManager().catch(() => {}));
  const enableAll = $("skills-enable-all");
  if (enableAll) enableAll.addEventListener("click", () => setAllSkills(true).catch(() => {}));
  const disableAll = $("skills-disable-all");
  if (disableAll) disableAll.addEventListener("click", () => setAllSkills(false).catch(() => {}));
  const create = $("skill-new");
  if (create) create.addEventListener("click", () => openSkillEditor(null).catch(() => {}));
  const cancel = $("skill-cancel");
  if (cancel) cancel.addEventListener("click", () => { $("skill-editor").hidden = true; skillError(""); });
  const form = $("skill-editor");
  if (form) form.addEventListener("submit", (event) => { saveSkill(event).catch(() => {}); });
  // Délégation sur le document : le champ peut être reconstruit par un rendu, le
  // listener n'est jamais perdu (mesuré : après un premier import, le second choix
  // de fichier ne déclenchait plus aucun POST). Le champ est réarmé APRÈS la lecture,
  // jamais pendant la distribution de l'événement.
  if (!window.__uhSkillFileWired) {
    window.__uhSkillFileWired = true;
    document.addEventListener("change", (event) => {
      const input = event.target;
      if (!input || input.id !== "skill-file") return;
      const chosen = input.files && input.files[0];
      if (!chosen) return;
      // Le champ est vidé TOUT DE SUITE, après capture du File (la référence reste
      // lisible) : sinon une sélection suivante, faite avant la fin de la lecture,
      // laisse la même valeur et Chromium n'émet aucun nouvel événement change.
      input.value = "";
      importSkill(chosen).catch((error) => skillError(error.message || "import refusé"));
    }, true);
  }
}

function loadToolCatalog() {
  if (state.toolsPromise) return state.toolsPromise;
  const list = $("tools-list");
  if (list) list.setAttribute("aria-busy", "true");
  const run = (async () => {
    try {
      renderToolCatalog(await api("/api/tools/catalog"));
    } finally {
      if (list) list.setAttribute("aria-busy", "false");
    }
  })();
  state.toolsPromise = run;
  const settle = () => {
    if (state.toolsPromise === run) state.toolsPromise = null;
  };
  run.then(settle, settle);
  return run;
}

// P3-16b : cinq groupes par usage. Un groupe ouvre ses sections ; l'onglet de groupe
// garde en mémoire la section où l'on était, pour qu'un aller-retour ne ramène pas
// toujours à la première.
const TAB_GROUPS = {
  projects: { label: "Projets", leaves: ["projects", "files"] },
  decision: { label: "Décision", leaves: ["decision", "role"] },
  tools: { label: "Outils & Connexions", leaves: ["tools", "conn", "uhb", "infra", "tasks", "extensions"] },
  brain: { label: "Cerveau & Mémoire", leaves: ["memory", "telemetry", "data"] },
  models: { label: "Modèles & Fournisseurs", leaves: ["keys", "models", "local"] },
};
const TAB_GROUP_OF = {};
for (const [group, meta] of Object.entries(TAB_GROUPS)) {
  for (const leaf of meta.leaves) TAB_GROUP_OF[leaf] = group;
}
const groupLeaf = {};

//: Déplace les boutons de section du réservoir vers la barre du groupe ouvert.
function renderSubtabs(group) {
  const bar = $("panel-subtabs");
  const pool = $("panel-pool");
  if (!bar || !pool) return;
  const meta = TAB_GROUPS[group];
  const leaves = meta ? meta.leaves : [];
  // Les boutons ne sont jamais recrees : ils retournent au reservoir puis reviennent.
  // `bar.textContent = ""` les detruisait, et le `getElementById` du tour suivant
  // tombait sur null (mesure : « Cannot read properties of null »).
  for (const child of [...bar.children]) pool.appendChild(child);
  for (const leaf of leaves) {
    const button = $(`tab-${leaf}`);
    if (button) bar.appendChild(button);
  }
  bar.hidden = leaves.length < 2;
}

function markGroupTabs(group) {
  for (const key of Object.keys(TAB_GROUPS)) {
    const button = $(`group-${key}`);
    if (!button) continue;
    const active = key === group;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
}

function showPanelTab(name) {
  state.activeTab = name;
  const group = TAB_GROUP_OF[name] || name;
  if (TAB_GROUPS[group] && TAB_GROUPS[group].leaves.includes(name)) groupLeaf[group] = name;
  markGroupTabs(group);
  renderSubtabs(group);
  for (const [tab, view] of [
    ["keys", "keys-view"],
    ["local", "local-view"],
    ["models", "models-view"],
    ["uhb", "uhb-view"],
    ["memory", "memory-view"],
    ["decision", "decision-view"],
    ["role", "role-view"],
    ["telemetry", "telemetry-view"],
    ["conn", "conn-view"],
    ["data", "data-view"],
    ["tools", "tools-view"],
    ["projects", "projects-view"],
    ["files", "files-view"],
    ["infra", "infra-view"],
    ["tasks", "tasks-view"],
    ["extensions", "extensions-view"],
  ]) {
    const active = tab === name;
    $(`tab-${tab}`).classList.toggle("active", active);
    $(`tab-${tab}`).setAttribute("aria-selected", String(active));
    // A view living in its own window is never hidden by the tab strip, and
    // selecting its tab brings that window forward.
    if (paneWindowFor(tab)) {
      if (active) paneWindowFor(tab).style.zIndex = String(++paneZ);
      $(view).hidden = false;
    } else {
      $(view).hidden = !active;
    }
  }
  if (name === "models") loadCapabilities().catch(() => {});
  // The tab strip is visible even when the column is collapsed or the panel was
  // opened by a shortcut: clicking "Fournisseurs" must load the list, not just
  // uncover an empty section (that is what it did - only the ⚙ button loaded it).
  if (name === "keys") {
    openProviders();
  }
  if (name === "uhb") {
    loadMemoryGraph().catch(() => {});
    loadUhb().catch(() => {});
  }
  if (name === "tools") {
    loadToolCatalog().catch(() => {});
    loadSkillsManager().catch(() => {});
  }
  // Le groupe ouvre la section : sans ce chargement, la liste restait vide jusqu'à
  // « Actualiser » — le bouton d'onglet chargeait, le groupe non (mesuré par l'e2e Git).
  if (name === "projects") {
    loadProjects().catch(() => {});
    loadRuns().catch(() => {});
  }
  if (name === "files") loadFs().catch(() => {});
  if (name === "infra") loadInfra().catch(() => {});
  if (name === "tasks") loadSchedules().catch(() => {});
  if (name === "extensions") loadExtensions().catch(() => {});
  if (name === "memory") {
    loadMemory().catch(() => {});
    // Le graphe a suivi la mémoire dans « Cerveau & Mémoire » : le charger ici évite
    // un panneau vide au premier clic.
    loadMemoryGraph().catch(() => {});
  }
  if (name === "decision") {
    loadDecision().catch(() => {});
    renderExecutionMode().catch(() => {});
  }
  if (name === "role") loadRolePanel().catch(() => {});
  if (name === "telemetry") {
    loadTelemetry().catch(() => {});
    startTelemetryPolling();
  } else {
    stopTelemetryPolling();
  }
  if (name === "conn") loadConnections().catch(() => {});
  if (name === "local") loadLocalPanel().catch(() => {});
  if (name === "data") loadData().catch(() => {});
}

// --- character & role (P3-14) ---------------------------------------------- //
// A role changes who answers — posture, expertise, tone — and nothing else. It is
// stored with the session in SQLite, applied server-side in the system prompt, and
// it never touches a skill, a toolset or an MCP server (tests/test_roles.py proves
// that byte for byte). The wording stays plain: "caractère", not "persona".
let ROLE_PRESETS = null;
let ROLE_CURRENT = null;

async function loadRolePresets() {
  if (ROLE_PRESETS) return ROLE_PRESETS;
  const data = await api("/api/roles");
  ROLE_PRESETS = data.presets || [];
  return ROLE_PRESETS;
}

function roleLabel(role) {
  return (role && role.label) || "Aucun rôle";
}

function fillRoleSelect(role) {
  ROLE_CURRENT = role || null;
  const select = $("role-select");
  if (!select) return;
  select.textContent = "";
  const none = el("option", null, "Aucun rôle");
  none.value = "";
  select.appendChild(none);
  for (const preset of ROLE_PRESETS || []) {
    const option = el("option", null, preset.label);
    option.value = preset.id;
    select.appendChild(option);
  }
  if (role && role.id === "custom") {
    const option = el("option", null, role.label);
    option.value = "custom";
    select.appendChild(option);
  }
  select.value = role ? String(role.id || "custom") : "";
  select.title = role
    ? `Rôle : ${roleLabel(role)} — la posture seule, les outils ne changent pas`
    : "Caractère / rôle : la posture et le ton de la réponse. Ne change ni les skills ni les outils.";
}

function roleButton(item) {
  const button = el("button", "role-option");
  button.type = "button";
  button.dataset.role = item.id || "";
  button.appendChild(el("span", null, item.label));
  if (item.hint) button.appendChild(el("small", null, item.hint));
  button.addEventListener("click", () => {
    if (item.id === "custom") {
      $("role-custom").hidden = false;
      $("role-label").focus();
      return;
    }
    chooseRole(item.id).catch((error) => modelNote(error.message, "warn"));
  });
  return button;
}

function paintRoleSelection() {
  const current = ROLE_CURRENT ? String(ROLE_CURRENT.id || "custom") : "";
  for (const node of document.querySelectorAll("#role-panel .role-option")) {
    node.classList.toggle("active", (node.dataset.role || "") === current);
  }
  if ($("role-custom")) $("role-custom").hidden = current !== "custom";
}

async function chooseRole(id) {
  if (id === "custom") {
    $("role-custom").hidden = false;
    $("role-label").focus();
    return;
  }
  const presets = await loadRolePresets();
  const preset = presets.find((item) => item.id === id);
  if (id && !preset) {
    modelNote("ce rôle n'existe pas", "warn");
    return;
  }
  // The preset travels by id: the posture is applied server-side, never sent here.
  await patchRole(preset ? preset.id : null);
}

async function patchRole(role) {
  if (!state.sessionId) await ensureSession();
  const data = await api(`/api/sessions/${encodeURIComponent(state.sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
  const applied = data.role || null;
  // `role` peut être un identifiant (preset de l'en-tête) ou un objet (texte libre) :
  // on compare des identifiants, jamais des représentations d'objet.
  const asked = role && typeof role === "object" ? String(role.id || "") : String(role || "");
  // Le serveur peut répondre sans rôle (preset refusé, session remplacée entre-temps).
  // Repeindre « Aucun rôle » laisserait croire que l'opérateur l'a demandé : on le dit,
  // et on garde son choix à l'écran pour qu'il puisse réessayer. Mesuré en CI Windows,
  // où cette réponse est arrivée sans que rien ne soit affiché.
  if (asked && (!applied || String(applied.id) !== asked)) {
    modelNote("rôle non enregistré par le serveur — réessaie ou recharge la page", "warn");
    paintRoleSelection();
    return;
  }
  fillRoleSelect(applied);
  paintRoleSelection();
  modelNote(`Rôle : ${roleLabel(applied)}`, "ok");
}

async function loadRolePanel() {
  await loadRolePresets();
  const panel = $("role-panel");
  if (!panel) return;
  panel.textContent = "";
  panel.appendChild(roleButton({ id: "", label: "Aucun rôle", hint: "réponse neutre" }));
  for (const preset of ROLE_PRESETS || []) panel.appendChild(roleButton(preset));
  panel.appendChild(roleButton({ id: "custom", label: "Texte libre", hint: "votre propre posture" }));
  paintRoleSelection();
}

function wireRole() {
  $("role-select").addEventListener("change", (event) => {
    chooseRole(String(event.target.value || "")).catch((error) => modelNote(error.message, "warn"));
  });
  $("role-custom-save").addEventListener("click", () => {
    const label = ($("role-label").value || "").trim();
    const posture = ($("role-posture").value || "").trim();
    if (!label || !posture) {
      modelNote("un rôle libre a besoin d'un nom et d'une phrase de posture", "warn");
      return;
    }
    patchRole({ id: "custom", label, posture }).catch((error) => modelNote(error.message, "warn"));
  });
  // A header select filled after the first session exists: an empty list would read
  // as "there are no roles", which is a different - and false - statement.
  loadRolePresets().then(() => fillRoleSelect(ROLE_CURRENT)).catch(() => {});
}

function openProviders() {
  $("layout").classList.remove("right-collapsed");
  $("providers-hint").textContent = "loading…";
  loadProviders().catch((error) => {
    // Keep whatever is on screen: an emptied list plus "error" reads as "you have
    // no providers", which is a different - and false - statement.
    $("providers-hint").textContent = `erreur de chargement : ${error.message}`;
  });
}

function closeProviders() {
  if (panelIsFloating()) dockPanel();
  $("layout").classList.add("right-collapsed");
  stopTelemetryPolling();
}

function providersAreOpen() {
  return !$("layout").classList.contains("right-collapsed");
}

// The right column is inline on wide screens and an overlay on narrow ones
// (matchMedia mirrors the CSS breakpoint). Click-outside only makes sense in
// overlay mode; inline, the column is part of the layout and stays put.
function isNarrow() {
  return window.matchMedia("(max-width: 1200px)").matches;
}



// --------------------------------------------------------------------------- //
// help: what a person can do here, and where
// --------------------------------------------------------------------------- //
const HELP_ACTIONS = [
  ["Connecter un stockage cloud (S3, Google Drive)", "conn", "Connecter un Cloud"],
  ["Ajouter un serveur MCP (programme local)", "conn", "Serveur MCP personnalisé"],
  ["Connecter un IDE ou un outil HTTP (Positron, VS Code…)", "conn", "IDE & outils externes"],
  ["Lancer une passe de rêve", "decision", "☾ Rêves, dans la barre du haut"],
  ["Réparer UH (l'IA propose un correctif)", "data", "Réparer UH"],
];

function openHelpModal() {
  const backdrop = el("div", "modal-backdrop");
  const modal = el("div", "modal");
  const form = el("form", "modal-form");
  form.appendChild(el("p", "modal-title", "Aide — où cliquer"));
  form.appendChild(
    el(
      "p",
      "data-note",
      "Chaque ligne ouvre directement le bon onglet. La même adresse fonctionne dans le chat : [Ouvrir Connexions](/?panel=conn)."
    )
  );
  HELP_ACTIONS.forEach(([label, tab, where]) => {
    const row = el("div", "help-row");
    row.appendChild(el("span", "help-label", label));
    row.appendChild(el("span", "help-where", where));
    const go = el("button", "data-button", "Ouvrir");
    go.type = "button";
    go.addEventListener("click", () => {
      backdrop.remove();
      openProviders();
      showPanelTab(tab);
    });
    row.appendChild(go);
    form.appendChild(row);
  });
  const actions = el("div", "modal-actions");
  const close = el("button", "data-button primary", "Fermer");
  close.type = "button";
  close.addEventListener("click", () => backdrop.remove());
  actions.appendChild(close);
  form.appendChild(actions);
  modal.appendChild(form);
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  modal.addEventListener("click", (event) => event.stopPropagation());
  backdrop.addEventListener("click", () => backdrop.remove());
  closeOnEscape(backdrop);
}

// A tab nobody notices is a tab that does not exist: the badge stays until one
// connection exists, then disappears for good.
async function refreshConnBadge() {
  const badge = $("conn-badge");
  if (!badge) return;
  let connected = false;
  try {
    const status = await api("/api/tools/status");
    connected = Number((status.mcp || {}).configured || 0) > 0;
  } catch (error) {
    /* no status: leave the badge as it is */
  }
  if (!connected) {
    try {
      const catalog = await api("/api/mcp/cloud");
      connected =
        (catalog.upload_targets || []).length > 0 || (catalog.http_servers || []).length > 0;
    } catch (error) {
      /* ignore */
    }
  }
  badge.hidden = connected;
}

// "providers" is gone as a tab - its content lives in "Fournisseurs" (keys).
// A link written before the merge must still land somewhere sane.
const PANEL_ALIASES = {
  providers: "keys",
  "clé": "keys",
  cles: "keys",
  connexions: "conn",
  // Les noms des cinq groupes mènent à leur première section : un lien écrit à la
  // main doit tomber quelque part de sensé, pas sur un panneau vide.
  outils: "tools",
  cerveau: "memory",
  memoire: "memory",
  données: "data",
  donnees: "data",
  modeles: "keys",
};
const PANEL_TABS = new Set([
  "projects", "keys", "local", "conn", "models", "uhb", "memory", "decision", "role", "telemetry", "data", "tools",
]);

function openPanelFromUrl() {
  const raw = (new URLSearchParams(location.search).get("panel") || "").toLowerCase();
  const wanted = PANEL_ALIASES[raw] || raw;
  if (!wanted || !PANEL_TABS.has(wanted)) return;
  openProviders();
  showPanelTab(wanted);
}

function wireProviders() {
  ensureHfModels().catch(() => {});
  $("providers-open").addEventListener("click", openProviders);
  $("providers-close").addEventListener("click", closeProviders);
  // Les cinq groupes : chacun rouvre la section où l'on s'était arrêté.
  for (const [group, meta] of Object.entries(TAB_GROUPS)) {
    const button = $(`group-${group}`);
    if (button) {
      button.addEventListener("click", () => showPanelTab(groupLeaf[group] || meta.leaves[0]));
    }
  }
  $("tab-keys").addEventListener("click", () => showPanelTab("keys"));
  $("tab-local").addEventListener("click", () => showPanelTab("local"));
  $("tab-models").addEventListener("click", () => showPanelTab("models"));
  $("tab-uhb").addEventListener("click", () => showPanelTab("uhb"));
  $("tab-memory").addEventListener("click", () => showPanelTab("memory"));
  $("tab-decision").addEventListener("click", () => showPanelTab("decision"));
  $("tab-role").addEventListener("click", () => showPanelTab("role"));
  $("tab-telemetry").addEventListener("click", () => showPanelTab("telemetry"));
  $("tab-conn").addEventListener("click", () => showPanelTab("conn"));
  $("help-open").addEventListener("click", openHelpModal);
  $("quit-uh").addEventListener("click", quitUh);
  $("tab-data").addEventListener("click", () => showPanelTab("data"));
  $("tab-tools").addEventListener("click", () => showPanelTab("tools"));
  $("tab-projects").addEventListener("click", () => showPanelTab("projects"));
  $("tab-files").addEventListener("click", () => showPanelTab("files"));
  $("tab-infra").addEventListener("click", () => showPanelTab("infra"));
  $("infra-refresh").addEventListener("click", () => loadInfra());
  $("tab-tasks").addEventListener("click", () => showPanelTab("tasks"));
  $("tasks-refresh").addEventListener("click", () => loadSchedules());
  $("tasks-form").addEventListener("submit", (event) => createTask(event));
  $("tab-extensions").addEventListener("click", () => showPanelTab("extensions"));
  $("extensions-refresh").addEventListener("click", () => loadExtensions());
  $("files-refresh").addEventListener("click", () => loadFs(FS.path).catch(() => {}));
  $("files-attach").addEventListener("click", () => attachSelectedFile());
  $("git-more").addEventListener("click", async () => {
    GIT.offset += GIT.page;
    await loadGitLog(true);
  });
  $("git-commit").addEventListener("click", () => commitWork());
  $("git-message").addEventListener("input", () => {
    const button = $("git-commit");
    if (button) button.disabled = !(GIT.staged > 0 && $("git-message").value.trim());
  });
  // Ouverture : le groupe de la section par défaut est marqué et sa barre remplie,
  // sinon la colonne s'ouvre sur cinq onglets dont aucun n'est actif.
  const startLeaf = state.activeTab || "keys";
  const startGroup = TAB_GROUP_OF[startLeaf] || "models";
  groupLeaf[startGroup] = startLeaf;
  markGroupTabs(startGroup);
  renderSubtabs(startGroup);
  $("projects-refresh").addEventListener("click", () => { loadProjects(); loadRuns(); });
  $("project-add").addEventListener("click", addProject);
  $("runs-clean").addEventListener("click", cleanRuns);
  // One button in the topbar, next to the Dreams one: the data panel is a place,
  // not a tab you have to know about.
  $("data-open").addEventListener("click", () => {
    openProviders();
    showPanelTab("data");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !providersAreOpen()) return;
    // La modale d'attachement a son propre Échap : sans cette garde, fermer la modale
    // repliait aussi le panneau de droite (mesuré : #group-tools passait d'une largeur
    // de 139 px à 0, alors que la classe du panneau ne changeait pas).
    const menu = $("plus-menu");
    if (menu && !menu.hidden) return;
    closeProviders();
  });
  document.addEventListener("click", (event) => {
    if (!isNarrow() || !providersAreOpen()) return;
    if ($("providers-panel").contains(event.target)) return;
    if ($("providers-open").contains(event.target)) return;
    closeProviders();
  });
}

// --- telemetry (P6): tokens per day, the machine now, the last passes -------- //
// Three readings, one route. The numbers are whatever uh/telemetry.py measured:
// the chart draws recorded turns, the gauges draw the machine, the table draws
// schedule.log. Nothing here is interpolated, and "coût" prints the reason UH
// refuses to invent one.
const TELEM_NS = "http://www.w3.org/2000/svg";
const TELEM_REFRESH_MS = 10000;
let telemTimer = null;

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(TELEM_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

function tokens(value) {
  return Number(value || 0).toLocaleString("fr-FR");
}

// 13280 -> "13 k": the axis needs three characters, not five.
function compactTokens(value) {
  const number = Number(value || 0);
  if (number >= 10000) return `${Math.round(number / 1000)}k`;
  if (number >= 1000) return `${(number / 1000).toFixed(1).replace(".", ",")}k`;
  return String(number);
}

function telemetryChart(days) {
  const W = 300;
  const H = 108;
  const pad = 6;
  const gap = 6;
  const floor = 20; // room for the day labels
  const plot = H - floor;
  const left = pad + 26; // room for the scale labels
  const max = Math.max(1, ...days.map((day) => day.local + day.cloud));
  const width = Math.max(6, (W - left - pad - gap * (days.length - 1)) / Math.max(1, days.length));
  const svg = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "telem-chart-svg",
    role: "img",
    "aria-label": "Jetons par jour, locaux et distants",
  });
  // A scale, not decoration: without it a single tall bar says nothing.
  for (const fraction of [1, 0.5]) {
    const y = plot - Math.round((plot - 4) * fraction);
    svg.appendChild(
      svgEl("line", { x1: left - 4, y1: y, x2: W - pad, y2: y, class: "telem-grid" })
    );
    const label = svgEl("text", { x: pad, y: y + 3, class: "telem-scale" });
    label.textContent = compactTokens(Math.round(max * fraction));
    svg.appendChild(label);
  }
  svg.appendChild(svgEl("line", { x1: left - 4, y1: plot, x2: W - pad, y2: plot, class: "telem-axis" }));
  days.forEach((day, index) => {
    const x = left + index * (width + gap);
    const total = day.local + day.cloud;
    // A non-zero value smaller than a pixel must still be visible: 981 local
    // tokens next to 12 299 cloud would otherwise vanish.
    const scaled = (value) =>
      value <= 0 ? 0 : Math.max(2, Math.round((value / max) * (plot - 4)));
    let cloudHeight = scaled(day.cloud);
    let localHeight = scaled(day.local);
    const room = plot - 4;
    while (cloudHeight + localHeight > room) {
      if (cloudHeight >= localHeight && cloudHeight > 2) cloudHeight -= 1;
      else if (localHeight > 2) localHeight -= 1;
      else break;
    }
    if (total === 0) {
      svg.appendChild(
        svgEl("rect", { x, y: plot - 1, width, height: 1, class: "telem-bar-empty" })
      );
    } else {
      let y = plot;
      for (const [cls, height] of [
        ["telem-bar-cloud", cloudHeight],
        ["telem-bar-local", localHeight],
      ]) {
        if (height <= 0) continue;
        y -= height;
        const bar = svgEl("rect", { x, y, width, height, class: cls, rx: 2 });
        const title = svgEl("title");
        title.textContent = `${day.date} · local ${tokens(day.local)} · cloud ${tokens(
          day.cloud
        )} · ${day.turns} tour(s)${
          day.estimated ? ` · dont ≈${tokens(day.estimated)} estimés` : ""
        }`;
        bar.appendChild(title);
        svg.appendChild(bar);
      }
    }
    const label = svgEl("text", { x: x + width / 2, y: H - 6, class: "telem-day" });
    // A "≈" says the day was inferred from the session, not recorded with the turn.
    label.textContent = day.estimated ? `${day.date.slice(8, 10)}≈` : day.date.slice(8, 10);
    svg.appendChild(label);
  });
  return svg;
}

function telemGauge(label, percent, detail) {
  const row = el("div", "telem-gauge");
  row.appendChild(el("span", "telem-gauge-label", label));
  if (percent === null || percent === undefined || Number.isNaN(Number(percent))) {
    row.appendChild(el("span", "telem-gauge-none", "indisponible"));
  } else {
    const bar = document.createElement("progress");
    bar.className = "telem-gauge-track";
    bar.max = 100;
    bar.value = Math.max(0, Math.min(100, Number(percent)));
    row.appendChild(bar);
  }
  row.appendChild(el("span", "telem-gauge-value", detail));
  return row;
}

function telemPassRow(pass) {
  const row = el("div", "telem-pass");
  const when = pass.at ? `${pass.at.slice(8, 10)}/${pass.at.slice(5, 7)} ${pass.at.slice(11, 16)}` : "?";
  row.appendChild(el("span", "telem-pass-when", when));
  row.appendChild(el("span", "telem-pass-model", pass.model || pass.provider || "?"));
  const seconds = pass.wall_time_s ?? pass.elapsed_s;
  row.appendChild(el("span", "telem-pass-time", seconds === null ? "?" : `${seconds}s`));
  const status = el("span", `telem-pass-status status-${pass.status}`, pass.status);
  status.title = `${pass.ideas || 0} idée(s) · ${pass.tokens === null ? "tokens non rapportés" : `${tokens(pass.tokens)} tokens`}${pass.local ? " · local" : ""}`;
  row.appendChild(status);
  return row;
}

function renderTelemetry(data, quiet) {
  const box = $("telemetry-panel");
  if (!quiet) box.textContent = "";
  const usage = data.usage || {};
  const system = data.system || {};
  const dreams = data.dreams || {};
  const view = el("div", "telem");

  const head = el("div", "telem-head");
  head.appendChild(el("span", null, `Jetons · ${usage.window_days || 7} derniers jours`));
  const stamp = data.generated_at ? new Date(data.generated_at) : new Date();
  head.appendChild(el("span", "telem-stamp", stamp.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })));
  view.appendChild(head);

  view.appendChild(telemetryChart(usage.days || []));
  const legend = el("div", "telem-legend");
  legend.appendChild(el("span", "telem-key telem-key-local", "local (ollama)"));
  legend.appendChild(el("span", "telem-key telem-key-cloud", "cloud"));
  view.appendChild(legend);

  const totals = usage.totals || {};
  view.appendChild(
    el(
      "p",
      "telem-totals",
      `${tokens(totals.tokens)} tokens · local ${tokens(totals.local)} · cloud ${tokens(
        totals.cloud
      )} · ${totals.turns || 0} tour(s)`
    )
  );

  // Which engine is worth using here: measured, not guessed. Speed alone is not
  // intelligence, so the table shows turns, latency, tokens per second and how
  // often the model actually called a tool.
  const evaluation = data.evaluation || {};
  const modelRows = evaluation.models || [];
  const evalBox = el("section", "telem-eval");
  evalBox.appendChild(el("h3", "panel-section-title", "Évaluation des modèles"));
  if (!modelRows.length) {
    evalBox.appendChild(
      el(
        "p",
        "panel-hint",
        "Aucun tour mesuré sur la fenêtre : la table se remplit au premier échange."
      )
    );
  } else {
    const table = el("table", "telem-table");
    const headRow = el("tr");
    ["modèle", "tours", "latence", "tok/s", "outils"].forEach((label) =>
      headRow.appendChild(el("th", null, label))
    );
    table.appendChild(headRow);
    for (const row of modelRows) {
      const line = el("tr");
      const name = el("td", "telem-model");
      name.appendChild(el("span", null, `${row.local ? "💻" : "☁️"} ${row.model}`));
      name.appendChild(el("span", "telem-provider", row.provider));
      line.appendChild(name);
      line.appendChild(el("td", null, String(row.turns)));
      line.appendChild(el("td", null, row.avg_ms === null ? "—" : `${(row.avg_ms / 1000).toFixed(1)} s`));
      line.appendChild(el("td", null, row.tokens_per_s === null ? "—" : String(row.tokens_per_s)));
      line.appendChild(el("td", null, String(row.tool_calls)));
      table.appendChild(line);
    }
    evalBox.appendChild(table);
    if (evaluation.outside_window || evaluation.undated) {
      evalBox.appendChild(
        el(
          "p",
          "panel-hint",
          `${evaluation.outside_window || 0} tour(s) hors fenêtre et ${
            evaluation.undated || 0
          } sans date ne sont pas comptés ici.`
        )
      );
    }
  }
  view.appendChild(evalBox);

  // Honest gaps, one line each: a turn whose provider reported no counters is not
  // a zero-token turn, and a record written before the timestamp existed cannot
  // be placed on a day.
  const notes = el("div", "telem-notes");
  if (usage.turns_without_usage) {
    notes.appendChild(
      el("p", null, `${usage.turns_without_usage} tour(s) dont le fournisseur n'a rapporté aucun compteur`)
    );
  }
  if (usage.totals && usage.totals.estimated) {
    notes.appendChild(
      el(
        "p",
        null,
        `≈ ${tokens(usage.totals.estimated)} tokens placés sur la date de leur session : ces tours précèdent l'horodatage des décisions`
      )
    );
  }
  if (usage.undated && usage.undated.turns) {
    notes.appendChild(
      el("p", null, `${usage.undated.turns} tour(s) sans date du tout : ${tokens(
        usage.undated.tokens
      )} tokens hors graphique`)
    );
  }
  if (usage.outside_window && usage.outside_window.turns) {
    notes.appendChild(
      el("p", null, `${usage.outside_window.turns} tour(s) plus anciens que la fenêtre`)
    );
  }
  const cost = el(
    "p",
    "telem-cost",
    "coût : non calculé — UH compte des tokens, pas des euros (aucune table de prix)"
  );
  cost.title = (usage.cost || {}).reason || "";
  notes.appendChild(cost);
  view.appendChild(notes);

  const gauges = el("div", "telem-gauges");
  gauges.appendChild(
    telemGauge(
      "CPU",
      system.cpu_percent,
      system.cpu_percent === null || system.cpu_percent === undefined
        ? `${system.cores || 0} cœurs`
        : `${system.cpu_percent}% · ${system.cores || 0} cœurs`
    )
  );
  const ram = system.ram;
  gauges.appendChild(
    telemGauge(
      "RAM",
      ram ? ram.percent : null,
      ram ? `${ram.used_gb} / ${ram.total_gb} GB` : "non rapportée"
    )
  );
  const vram = system.vram;
  gauges.appendChild(
    telemGauge(
      "VRAM",
      vram ? vram.percent : null,
      vram ? `${vram.used_mb} / ${vram.total_mb} MB` : system.gpu ? "nvidia-smi muet" : "pas de GPU"
    )
  );
  view.appendChild(gauges);

  const passes = dreams.passes || [];
  const passHead = el("div", "telem-head telem-head-passes");
  passHead.appendChild(el("span", null, "Dernières passes de rêve"));
  passHead.appendChild(
    el("span", "telem-stamp", `${passes.length} / ${dreams.lines || 0} lignes`)
  );
  view.appendChild(passHead);
  if (!passes.length) {
    view.appendChild(el("p", "telem-empty", "Aucune passe dans dreams/schedule.log."));
  } else {
    const list = el("div", "telem-passes");
    passes.forEach((pass) => list.appendChild(telemPassRow(pass)));
    view.appendChild(list);
  }

  const refresh = el("button", "telem-refresh", "↻ Rafraîchir");
  refresh.addEventListener("click", () => loadTelemetry().catch(() => {}));
  view.appendChild(refresh);

  box.appendChild(view);
}

async function loadTelemetry(options = {}) {
  const quiet = Boolean(options.quiet);
  try {
    const data = await api("/api/telemetry");
    renderTelemetry(data, quiet);
  } catch (error) {
    if (!quiet) {
      const box = $("telemetry-panel");
      box.textContent = "";
      box.appendChild(el("p", "panel-hint", `télémétrie indisponible : ${error.message}`));
    }
  }
}

// Polling only while the tab is on screen: a cockpit that keeps a timer running
// behind a closed panel is a battery that empties for nothing.
function startTelemetryPolling() {
  stopTelemetryPolling();
  telemTimer = window.setInterval(() => {
    if ($("telemetry-view") && !$("telemetry-view").hidden) loadTelemetry({ quiet: true }).catch(() => {});
  }, TELEM_REFRESH_MS);
}

function stopTelemetryPolling() {
  if (telemTimer) window.clearInterval(telemTimer);
  telemTimer = null;
}

// --- data (P8): one archive, your skills, what is actually connected --------- //
// The promise behind this panel: UH keeps its state in files it can hand back.
// Nothing here relies on localStorage or on a cookie - the browser asks, the
// server writes a zip under $UH_HOME/backups, and the file is yours.
function humanBytes(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} kB`;
  return `${value} B`;
}

function dataSection(title, hint) {
  const section = el("section", "data-section");
  const head = el("div", "data-head");
  head.appendChild(el("span", null, title));
  if (hint) head.appendChild(el("span", "data-hint", hint));
  section.appendChild(head);
  return section;
}

function dataStatus(section) {
  const status = el("p", "data-status");
  section.appendChild(status);
  return status;
}

async function backupSection() {
  const section = dataSection("Sauvegarde", "un fichier, toute ta vie UH");
  const actions = el("div", "data-actions");
  const exportButton = el("button", "data-button primary", "⬇ Exporter");
  exportButton.type = "button";
  const importButton = el("button", "data-button", "⬆ Importer");
  importButton.type = "button";
  const file = document.createElement("input");
  file.type = "file";
  file.accept = ".zip,application/zip";
  file.hidden = true;
  actions.appendChild(exportButton);
  actions.appendChild(importButton);
  actions.appendChild(file);
  section.appendChild(actions);
  const status = dataStatus(section);
  const list = el("div", "data-list");
  section.appendChild(list);

  const refresh = async () => {
    list.textContent = "";
    try {
      const data = await api("/api/backup");
      if (!data.backups.length) {
        list.appendChild(el("p", "data-empty", `Aucune archive pour l'instant. Elles s'écrivent dans ${data.dir}`));
        return;
      }
      data.backups.slice(0, 6).forEach((backup) => {
        const row = el("div", "backup-row");
        const link = el("a", "backup-name", backup.name);
        link.href = `/api/backup/download/${encodeURIComponent(backup.name)}`;
        link.download = backup.name;
        row.appendChild(link);
        row.appendChild(el("span", "backup-meta", `${humanBytes(backup.bytes)} · ${backup.modified.slice(0, 16).replace("T", " ")}`));
        list.appendChild(row);
      });
    } catch (error) {
      list.appendChild(el("p", "data-empty", `archives illisibles : ${error.message}`));
    }
  };

  exportButton.addEventListener("click", async () => {
    exportButton.disabled = true;
    status.textContent = "écriture de l'archive…";
    try {
      const result = await api("/api/backup/export", { method: "POST" });
      const excluded = result.excludes || [];
      status.textContent = `${result.name} · ${humanBytes(result.bytes)} · ${result.files} fichier(s)${
        result.wal_checkpoint ? "" : " · base occupée : les derniers tours peuvent manquer"
      }`;
      // One security-relevant exclusion is worth naming; the rest is a tooltip,
      // because eleven file names in a 340 px column is noise.
      status.title = `jamais inclus : ${excluded.join(", ")}`;
      const important = excluded.filter((name) => name === "keys.json" || name === "backups");
      if (important.length) {
        status.textContent += ` · jamais inclus : ${important.join(", ")}${
          excluded.length > important.length ? ` (+${excluded.length - important.length})` : ""
        }`;
      }
      // The archive exists on disk; the link hands the same bytes to the browser.
      const link = document.createElement("a");
      link.href = `/api/backup/download/${encodeURIComponent(result.name)}`;
      link.download = result.name;
      link.click();
      await refresh();
    } catch (error) {
      status.textContent = `export impossible : ${error.message}`;
    } finally {
      exportButton.disabled = false;
    }
  });

  importButton.addEventListener("click", () => file.click());
  file.addEventListener("change", async () => {
    const chosen = file.files && file.files[0];
    file.value = "";
    if (!chosen) return;
    if (
      !window.confirm(
        `Restaurer ${chosen.name} ?\n\nL'état actuel est d'abord sauvegardé dans $UH_HOME/backups/ : rien n'est perdu.`
      )
    ) {
      return;
    }
    status.textContent = "restauration…";
    try {
      const body = await chosen.arrayBuffer();
      const result = await api("/api/backup/import", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body,
      });
      status.textContent = `${result.restored} fichier(s) restaurés · copie de sécurité : ${result.safety} · les fichiers absents de l'archive n'ont pas été touchés · redémarre UH pour recharger la base`;
      await refresh();
    } catch (error) {
      status.textContent = `import refusé : ${error.message}`;
    }
  });

  await refresh();
  return section;
}

async function skillsSection() {
  const section = dataSection("Skills", "des .md que tu ouvres dans ton éditeur");
  const actions = el("div", "data-actions");
  const create = el("button", "data-button", "+ Nouveau skill");
  create.type = "button";
  create.addEventListener("click", () => openSkillModal());
  actions.appendChild(create);
  section.appendChild(actions);
  const list = el("div", "data-list");
  section.appendChild(list);
  try {
    const data = await api("/api/skills");
    if (!data.skills.length) {
      list.appendChild(el("p", "data-empty", `Aucun skill encore. Les tiens s'écrivent dans ${data.user_dir}`));
    }
    data.skills.forEach((skill) => {
      const row = el("div", "skill-row");
      const head = el("div", "skill-head");
      head.appendChild(el("span", "skill-title", skill.title));
      head.appendChild(el("span", `badge ${skill.native ? "hl" : ""}`, skill.native ? "Natif" : "Utilisateur"));
      row.appendChild(head);
      if (skill.description) row.appendChild(el("p", "skill-desc", skill.description));
      row.appendChild(el("p", "skill-path", skill.path));
      list.appendChild(row);
    });
    if (!data.native_available) {
      // Honest empty state: a "Natif" badge nobody can see would be decoration.
      section.appendChild(
        el(
          "p",
          "data-note",
          "Aucun skill natif livré pour l'instant : UH n'en embarque pas encore, la liste se remplit des tiens."
        )
      );
    }
  } catch (error) {
    list.appendChild(el("p", "data-empty", `skills illisibles : ${error.message}`));
  }
  return section;
}

async function toolsSection() {
  const section = dataSection("Outils", "ce qui est réellement connecté");
  const list = el("div", "data-list");
  section.appendChild(list);
  const rows = [];
  // `data` lives outside the try: the MCP block below reads `data.mcp`, and a
  // const inside the try put it out of scope - the panel then failed with
  // "data is not defined" whenever the status call *succeeded*.
  let data = null;
  try {
    data = await api("/api/tools/status");
    const uhb = data.uhb || {};
    rows.push([
      "UHB · second cerveau",
      uhb.connected ? "ok" : "off",
      uhb.connected
        ? [uhb.version ? `v${uhb.version}` : "", uhb.mode ? `mode ${uhb.mode}` : ""].filter(Boolean).join(" · ")
        : uhb.reason || "non connecté",
    ]);
    const ollama = data.ollama || {};
    rows.push([
      "Ollama",
      ollama.boost === "active" ? "ok" : ollama.boost === "available" ? "warn" : "off",
      ollama.reason || (ollama.models || []).slice(0, 3).join(", ") || "indisponible",
    ]);
    Object.entries(data.toolsets || {}).forEach(([name, tools]) => {
      rows.push([`toolset · ${name}`, "ok", `${(tools || []).length} outil(s)`]);
    });
    rows.push([
      "terminal · shell",
      data.terminal ? "warn" : "off",
      data.terminal ? "activé : le modèle peut lancer des commandes" : "désactivé (--allow-terminal)",
    ]);
  } catch (error) {
    list.appendChild(el("p", "data-empty", `état des outils indisponible : ${error.message}`));
    return section;
  }
  rows.forEach(([label, state, detail]) => {
    const row = el("div", "tool-row");
    row.appendChild(el("span", `dot dot-${state}`));
    row.appendChild(el("span", "tool-name", label));
    row.appendChild(el("span", "tool-detail", detail));
    list.appendChild(row);
  });

  // MCP: one row per configured server, one button to ask it *now*. The dot
  // says what is true at this instant - "idle" is a server the harness has not
  // started yet, never a green dot for something nobody has contacted.
  const mcp = data.mcp || {};
  const servers = Array.isArray(mcp.servers) ? mcp.servers : [];
  if (!mcp.supported) {
    const row = el("div", "tool-row");
    row.appendChild(el("span", "dot dot-off"));
    row.appendChild(el("span", "tool-name", "MCP"));
    row.appendChild(el("span", "tool-detail", mcp.reason || "non pris en charge"));
    list.appendChild(row);
  } else if (!servers.length) {
    const row = el("div", "tool-row");
    row.appendChild(el("span", "dot dot-off"));
    row.appendChild(el("span", "tool-name", "MCP"));
    row.appendChild(el("span", "tool-detail", mcp.reason || "aucun serveur configuré"));
    list.appendChild(row);
  } else {
    const dots = {};
    servers.forEach((server) => {
      const row = el("div", "tool-row");
      const dot = el("span", `dot dot-${server.connected ? "ok" : server.state === "failed" ? "warn" : "off"}`);
      const detail = el(
        "span",
        "tool-detail",
        server.connected
          ? `${(server.tools || []).length} outil(s) · ${server.server || server.command}`
          : server.reason || "pas encore démarré"
      );
      dots[server.name] = { dot, detail };
      row.appendChild(dot);
      row.appendChild(el("span", "tool-name", `mcp · ${server.name}`));
      row.appendChild(detail);
      list.appendChild(row);
    });
    const bar = el("div", "tool-test");
    servers.forEach((server) => {
      const button = el("button", "data-button", `Tester ${server.name}`);
      button.type = "button";
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "test en cours…";
        try {
          const result = await api(`/api/mcp/${encodeURIComponent(server.name)}/test`, { method: "POST" });
          const tools = result.tools || [];
          dots[server.name].dot.className = "dot dot-ok";
          dots[server.name].detail.textContent = `${tools.length} outil(s) · ${result.server || server.command}`;
          button.textContent = `OK — ${tools.length} outil(s)`;
        } catch (error) {
          dots[server.name].dot.className = "dot dot-warn";
          dots[server.name].detail.textContent = error.message;
          button.textContent = "Échec — réessayer";
        } finally {
          button.disabled = false;
        }
      });
      bar.appendChild(button);
    });
    section.appendChild(bar);
  }

  // No connection button here on purpose: "Connexions" is the only door to an
  // external service. This tab is the local maintenance room (backup, skills,
  // installed tools, repair) - and a second door made people wonder which one
  // was the real one.

  // Cloud connections: a template, a few fields, one button. The token goes to
  // mcp-secrets.toml - config.toml is the file people paste into issues.
  mountFix(section);

  section.appendChild(
    el(
      "p",
      "data-note",
      "Un voyant dit seulement ce qui a répondu : « MCP · nom » en gris est un serveur déclaré dans config.toml que ce processus n'a pas encore lancé, pas une panne."
    )
  );
  return section;
}


// One list, created on first use: a dropdown of chat models that ship tools, with
// the field still free-form - the router accepts any repo id.
let hfModelsReady = false;
async function ensureHfModels() {
  if (hfModelsReady || document.getElementById("hf-models")) return;
  hfModelsReady = true;
  const list = document.createElement("datalist");
  list.id = "hf-models";
  try {
    const state = await api("/api/setup");
    (state.huggingface_models || []).forEach((id) => {
      const option = document.createElement("option");
      option.value = id;
      list.appendChild(option);
    });
  } catch (error) {
    /* an older backend: no suggestions, the field still accepts an id */
  }
  document.body.appendChild(list);
}

function openSkillModal() {
  const backdrop = el("div", "modal-backdrop");
  const modal = el("div", "modal");
  const form = el("form", "modal-form");
  form.appendChild(el("p", "modal-title", "Nouveau skill"));

  const title = document.createElement("input");
  title.className = "modal-input";
  title.placeholder = "Titre — ce que le skill fait";
  title.maxLength = 120;
  const description = document.createElement("input");
  description.className = "modal-input";
  description.placeholder = "Description (une ligne)";
  description.maxLength = 240;
  const instructions = document.createElement("textarea");
  instructions.className = "modal-area";
  instructions.rows = 8;
  instructions.placeholder = "Instructions — en prose, comme à un collègue";
  const error = el("p", "modal-error");
  const actions = el("div", "modal-actions");
  const cancel = el("button", "data-button", "Annuler");
  cancel.type = "button";
  const submit = el("button", "data-button primary", "Créer");
  submit.type = "submit";
  actions.appendChild(cancel);
  actions.appendChild(submit);

  [title, description, instructions, error, actions].forEach((node) => form.appendChild(node));
  modal.appendChild(form);
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);

  const close = () => backdrop.remove();
  cancel.addEventListener("click", close);
  // The document-level click-outside handler closes the right panel on narrow
  // screens; a click inside a modal is not a click outside it.
  modal.addEventListener("click", (event) => event.stopPropagation());
  backdrop.addEventListener("click", close);
  document.addEventListener(
    "keydown",
    function escape(event) {
      if (event.key === "Escape") {
        close();
        document.removeEventListener("keydown", escape);
      }
    },
    { once: true }
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    error.textContent = "";
    try {
      const created = await api("/api/skills", {
        method: "POST",
        body: JSON.stringify({
          title: title.value,
          description: description.value,
          instructions: instructions.value,
        }),
      });
      close();
      await loadData();
      const status = document.querySelector("#data-panel .data-status");
      if (status) status.textContent = `skill écrit : ${created.path}`;
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  });
  title.focus();
}

async function loadData() {
  const box = $("data-panel");
  box.textContent = "";
  const view = el("div", "data");
  // The shell first, then each section as its route answers: the slowest one
  // (tools, which probes UHB and Ollama) must not blank a panel that already
  // has the archive list in it.
  box.appendChild(view);
  for (const builder of [backupSection, skillsSection, toolsSection]) {
    try {
      view.appendChild(await builder());
    } catch (error) {
      const failed = dataSection("Erreur", null);
      failed.appendChild(el("p", "data-empty", error.message));
      view.appendChild(failed);
    }
  }
}

// --- skills (P8): attach one .md to this turn --------------------------------- //
// The badge is the honest part: a skill changes the system prompt, so it stays on
// screen until it is removed, and it is sent with every turn it applies to.
function renderSkillBadge() {
  const badge = $("skill-badge");
  badge.textContent = "";
  badge.hidden = !state.skill;
  if (!state.skill) return;
  badge.appendChild(el("span", "skill-badge-label", `Skill : ${state.skill.title}`));
  const clear = el("button", "skill-badge-clear", "×");
  clear.type = "button";
  clear.title = "Retirer le skill de ce tour";
  clear.addEventListener("click", () => {
    state.skill = null;
    renderSkillBadge();
  });
  badge.appendChild(clear);
}

function closeSkillMenu() {
  const menu = $("skill-menu");
  if (menu) menu.remove();
}

async function openSkillMenu() {
  if ($("skill-menu")) {
    closeSkillMenu();
    return;
  }
  let data;
  try {
    data = await api("/api/skills");
  } catch (error) {
    return;
  }
  const menu = el("div", "skill-menu");
  menu.id = "skill-menu";
  menu.appendChild(el("p", "skill-menu-head", data.skills.length ? "Skill pour ce tour" : "Aucun skill"));

  const row = (title, hint, onClick) => {
    const item = el("button", "skill-menu-item");
    item.type = "button";
    item.appendChild(el("span", "skill-menu-title", title));
    if (hint) item.appendChild(el("span", "skill-menu-hint", hint));
    item.addEventListener("click", () => {
      onClick();
      closeSkillMenu();
    });
    menu.appendChild(item);
  };
  row("Aucun", "le prompt système seul", () => {
    state.skill = null;
    renderSkillBadge();
  });
  data.skills.forEach((skill) => {
    row(skill.title, skill.description || `${skill.origin === "native" ? "natif" : "utilisateur"} · ${skill.name}`, () => {
      state.skill = { name: skill.name, title: skill.title };
      renderSkillBadge();
    });
  });
  document.body.appendChild(menu);

  // Above the composer button, kept inside the window.
  const rect = $("skill-open").getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 280))}px`;
  menu.style.bottom = `${Math.max(8, window.innerHeight - rect.top + 8)}px`;

  setTimeout(() => {
    const away = (event) => {
      if (menu.contains(event.target) || $("skill-open").contains(event.target)) return;
      closeSkillMenu();
      document.removeEventListener("click", away);
    };
    document.addEventListener("click", away);
  }, 0);
}

// --- the memory graph (P6/P8): what feeds a turn, and what does not ---------- //
// Drawn as SVG from /api/memory/graph: no Mermaid, no library, no new CSP hole.
// Solid and animated = wired; dashed and grey = "UH cannot read this yet", which
// is exactly what the roadmap says in prose.
function memoryGraph(data) {
  const W = 320;
  const H = 300;
  const box = { in: [10, 104], core: [132, 86], out: [232, 78] };
  const spots = {
    memory: [18, "in"],
    wiki: [78, "in"],
    rag: [138, "in"],
    siyuan: [198, "in"],
    turn: [124, "core"],
    sessions: [56, "out"],
    dreams: [190, "out"],
  };
  const nodes = new Map((data.nodes || []).map((node) => [node.id, node]));
  const center = (id) => {
    const spot = spots[id];
    if (!spot) return null;
    const [y, column] = spot;
    const [x, width] = box[column];
    return { x, y, width, height: 40, midY: y + 20, midX: x + width / 2 };
  };
  const svg = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`,
    class: "mem-svg",
    role: "img",
    "aria-label": "Sources mémoire et ce qui parvient à un tour",
  });
  const detail = el("p", "mem-detail", "Survole un nœud pour son détail.");

  const edges = [];
  (data.edges || []).forEach((edge) => {
    const from = center(edge.from);
    const to = center(edge.to);
    if (!from || !to) return;
    // Always draw left to right, whatever the edge direction: a cubic that starts
    // to the right of its target doubles back through the boxes.
    const [left, right] = from.midX <= to.midX ? [from, to] : [to, from];
    const startX = left.x + left.width;
    const endX = right.x;
    const gap = Math.max(4, endX - startX);
    const dx = Math.min(14, Math.max(6, gap / 2));
    const path = svgEl("path", {
      d: `M${startX},${left.midY} C${startX + dx},${left.midY} ${endX - dx},${right.midY} ${endX},${right.midY}`,
      class: `mem-edge ${edge.wired ? "mem-edge-wired" : "mem-edge-off"}`,
    });
    const title = svgEl("title");
    title.textContent = `${edge.label} · ${edge.wired ? "branché" : "non branché"}`;
    path.appendChild(title);
    svg.appendChild(path);
    edges.push({ path, edge });
  });

  (data.nodes || []).forEach((node) => {
    const spot = spots[node.id];
    if (!spot) return;
    const area = center(node.id);
    const group = svgEl("g", {
      class: `mem-node mem-node-${node.column} mem-node-state-${node.state || "off"}`,
    });
    group.appendChild(
      svgEl("rect", { x: area.x, y: area.y, width: area.width, height: area.height, rx: 8 })
    );
    const label = svgEl("text", { x: area.midX, y: area.y + 16, class: "mem-label" });
    label.textContent = node.label;
    group.appendChild(label);
    const value = svgEl("text", { x: area.midX, y: area.y + 30, class: "mem-count" });
    // No count means no measurement: "—", never a zero that would read as "empty".
    value.textContent =
      node.count === null || node.count === undefined ? "—" : `${node.count} ${node.unit}`;
    group.appendChild(value);
    const title = svgEl("title");
    title.textContent = `${node.label}${node.count === null ? "" : ` · ${node.count} ${node.unit}`} · ${node.detail}`;
    group.appendChild(title);
    group.addEventListener("click", () => {
      detail.textContent = `${node.label} — ${node.detail || "aucun détail"}${
        node.count === null || node.count === undefined ? "" : ` (${node.count} ${node.unit})`
      }`;
    });
    svg.appendChild(group);
    // Highlight the edges of the hovered node: the graph answers "does this reach
    // the turn?" with a gesture, not with a legend to memorise.
    group.addEventListener("mouseenter", () => {
      edges.forEach(({ path, edge }) => {
        if (edge.from === node.id || edge.to === node.id) path.classList.add("mem-edge-hot");
      });
    });
    group.addEventListener("mouseleave", () => {
      edges.forEach(({ path }) => path.classList.remove("mem-edge-hot"));
    });
  });

  const wrapper = el("div", "mem");
  const head = el("div", "telem-head");
  head.appendChild(el("span", null, "Second cerveau · ce qui alimente un tour"));
  head.appendChild(el("span", "telem-stamp", `${data.wired || 0} / ${(data.edges || []).length} liens branchés`));
  wrapper.appendChild(head);
  wrapper.appendChild(svg);
  wrapper.appendChild(detail);
  wrapper.appendChild(
    el(
      "p",
      "data-note",
      "Légende — trait plein : UH consulte cette source pour un tour ; pointillés : il ne peut pas encore (pas d'outil pour le wiki, pas de route pour le RAG, SiYuan passe par UHB) ; nœud plein : source effectivement lue ; nœud creux : joignable mais non lue pour ce tour. Le détail est aussi écrit sous le graphe quand tu survoles un nœud : rien ne dépend de la couleur seule."
    )
  );
  return wrapper;
}

async function loadMemoryGraph() {
  const box = $("memory-graph");
  try {
    const data = await api("/api/memory/graph");
    box.textContent = "";
    box.appendChild(memoryGraph(data));
  } catch (error) {
    box.textContent = "";
    box.appendChild(el("p", "panel-hint", `graphe indisponible : ${error.message}`));
  }
}

// --- dockable panel (P6): detach, drag, snap to an edge, dock back ---------- //
// v1 of the cockpit, without a layout library: the grid stays the source of truth
// while the panel is docked, and the panel becomes fixed (draggable, resizable
// through CSS `resize`, magnetic to the window edges) while it is detached.
// Below 768 px the button is hidden: the panel is already an overlay there, and a
// draggable copy would fight the drawer. The chat column is not detachable yet -
// that is the part that would need a real docking engine.
const DOCK_SNAP = 28;
let dockDrag = null;

function isPhone() {
  return window.matchMedia("(max-width: 768px)").matches;
}

function panelIsFloating() {
  return $("providers-panel").classList.contains("floating");
}

function placePanel(rect) {
  const panel = $("providers-panel");
  panel.style.left = `${Math.round(rect.left)}px`;
  panel.style.top = `${Math.round(rect.top)}px`;
  panel.style.width = `${Math.round(rect.width)}px`;
  panel.style.height = `${Math.round(rect.height)}px`;
}

function detachPanel() {
  if (panelIsFloating() || isPhone()) return;
  openProviders();
  const panel = $("providers-panel");
  const rect = panel.getBoundingClientRect();
  $("layout").classList.add("dock-float");
  panel.classList.add("floating");
  placePanel({
    left: Math.max(12, Math.min(rect.left, window.innerWidth - 380)),
    top: Math.max(12, rect.top + 16),
    width: Math.max(320, Math.min(420, rect.width + 40)),
    // A detached window that fills the screen is not detached: start at 70% of
    // the column, capped at three quarters of the viewport.
    height: Math.max(300, Math.min(rect.height * 0.7 || 520, window.innerHeight * 0.75)),
  });
  $("panel-float").hidden = true;
  $("panel-dock").hidden = false;
}

function dockPanel() {
  const panel = $("providers-panel");
  panel.classList.remove("floating", "dragging");
  panel.style.left = "";
  panel.style.top = "";
  panel.style.width = "";
  panel.style.height = "";
  $("layout").classList.remove("dock-float");
  $("panel-float").hidden = false;
  $("panel-dock").hidden = true;
  dockDrag = null;
}

function snapPanel() {
  const rect = $("providers-panel").getBoundingClientRect();
  const width = Math.max(300, Math.min(420, rect.width));
  const height = Math.max(260, rect.height);
  // The bottom snap only makes sense for a window that leaves the top visible:
  // a tall panel dragged downwards is always "near the bottom".
  const bottomHit = window.innerHeight - rect.bottom <= DOCK_SNAP && rect.height < window.innerHeight * 0.6;
  if (bottomHit) {
    // bottom edge: full width, keeps its height
    placePanel({
      left: 12,
      top: window.innerHeight - Math.min(height, window.innerHeight - 24) - 12,
      width: window.innerWidth - 24,
      height: Math.min(height, window.innerHeight - 24),
    });
  } else if (rect.left <= DOCK_SNAP) {
    placePanel({ left: 12, top: 12, width, height: window.innerHeight - 24 });
  } else if (window.innerWidth - rect.right <= DOCK_SNAP) {
    placePanel({ left: window.innerWidth - width - 12, top: 12, width, height: window.innerHeight - 24 });
  } else {
    placePanel({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8)),
      top: Math.max(8, Math.min(rect.top, window.innerHeight - 80)),
      width: rect.width,
      height: rect.height,
    });
  }
}

function wireDock() {
  $("panel-float").addEventListener("click", detachPanel);
  $("panel-dock").addEventListener("click", dockPanel);
  const head = $("panel-head");
  head.addEventListener("pointerdown", (event) => {
    if (!panelIsFloating() || event.target.closest("button")) return;
    const rect = $("providers-panel").getBoundingClientRect();
    dockDrag = { dx: event.clientX - rect.left, dy: event.clientY - rect.top, width: rect.width, height: rect.height };
    $("providers-panel").classList.add("dragging");
    try {
      head.setPointerCapture(event.pointerId);
    } catch (error) {
      // A synthetic pointer has nothing to capture; the drag still works.
    }
    event.preventDefault();
  });
  head.addEventListener("pointermove", (event) => {
    if (!dockDrag) return;
    placePanel({
      left: event.clientX - dockDrag.dx,
      top: event.clientY - dockDrag.dy,
      width: dockDrag.width,
      height: dockDrag.height,
    });
  });
  head.addEventListener("pointerup", () => {
    if (!dockDrag) return;
    $("providers-panel").classList.remove("dragging");
    dockDrag = null;
    snapPanel();
  });
  window.addEventListener("resize", () => {
    // A phone has no room for a floating panel: put it back in the drawer.
    if (isPhone() && panelIsFloating()) dockPanel();
  });
}

// --- conversation settings (P9): the knobs, drawn from one table ------------- //
// The panel is generated from /api/llm/settings, so a slider can never promise a
// range or a parameter the backend does not know. Settings are stored on the
// session (PATCH), sent with every turn, and ignored entirely while the master
// switch is off - an untouched slider must not change an answer.
let llmSaveTimer = null;

function renderLlmSummary() {
  const badge = $("llm-badge");
  badge.textContent = "";
  badge.hidden = !state.llm.enabled;
  if (!state.llm.enabled) return;
  const values = state.llm.values || {};
  const bits = [];
  if (values.temperature !== undefined) {
    bits.push(`T ${Number(values.temperature).toFixed(2).replace(".", ",")}`);
  }
  if (values.max_tokens) bits.push(`${values.max_tokens} tok`);
  if (values.reasoning_effort) bits.push(`effort ${values.reasoning_effort}`);
  badge.appendChild(el("span", "skill-badge-label", `Réglages : ${bits.join(" · ")}`));
  const clear = el("button", "skill-badge-clear", "×");
  clear.type = "button";
  clear.title = "Revenir aux réglages du modèle";
  clear.addEventListener("click", () => {
    state.llm.enabled = false;
    renderLlmSummary();
    if ($("llm-panel")) renderLlmRows();
    saveLlmSettings();
  });
  badge.appendChild(clear);
}

function saveLlmSettings() {
  if (!state.sessionId) return;
  window.clearTimeout(llmSaveTimer);
  llmSaveTimer = window.setTimeout(async () => {
    try {
      await api(`/api/sessions/${encodeURIComponent(state.sessionId)}`, {
        method: "PATCH",
        body: JSON.stringify({ settings: state.llm }),
      });
    } catch (error) {
      // A settings write that fails must not block the sliders; the next turn
      // still carries them, and the badge still says what is active.
    }
  }, 400);
}

function llmSpec() {
  return (state.llmSpec && state.llmSpec.spec) || {};
}

function renderLlmRows() {
  const box = $("llm-panel");
  if (!box) return;
  const rows = box.querySelector("#llm-rows");
  rows.textContent = "";
  const spec = llmSpec();
  Object.entries(spec).forEach(([key, field]) => {
    // A boolean is a switch, not a bare checkbox: a <label> would also make the
    // whole row a second click target for the button inside it.
    const row = el(field.kind === "bool" ? "div" : "label", "llm-row");
    const head = el("div", "llm-row-head");
    const labelNode = el("span", "llm-label", field.label);
    labelNode.id = `llm-label-${key}`;
    head.appendChild(labelNode);
    const value = el("span", "llm-value");
    head.appendChild(value);
    row.appendChild(head);

    const current = state.llm.values[key];
    if (field.kind === "bool") {
      const toggle = el("button", "llm-switch llm-switch-field");
      toggle.type = "button";
      toggle.id = `llm-${key}`;
      toggle.setAttribute("role", "switch");
      toggle.setAttribute("aria-labelledby", `llm-label-${key}`);
      toggle.appendChild(el("span", "llm-switch-knob"));
      toggle.firstChild.setAttribute("aria-hidden", "true");
      const toggleText = el("span", "llm-switch-label");
      toggle.appendChild(toggleText);
      const paint = (on) => {
        toggle.setAttribute("aria-checked", String(on));
        toggle.classList.toggle("on", on);
        toggleText.textContent = on ? "Activé" : "Désactivé";
        value.textContent = "";
      };
      paint(Boolean(current));
      toggle.addEventListener("click", () => {
        const next = toggle.getAttribute("aria-checked") !== "true";
        state.llm.values[key] = next;
        paint(next);
        renderLlmSummary();
        saveLlmSettings();
      });
      row.appendChild(toggle);
    } else if (field.kind === "choice") {
      const select = document.createElement("select");
      select.id = `llm-${key}`;
      (field.choices || []).forEach((choice) => {
        const option = el("option", null, choice === "" ? "modèle par défaut" : choice);
        option.value = choice;
        select.appendChild(option);
      });
      select.value = current ?? "";
      select.addEventListener("change", () => {
        state.llm.values[key] = select.value;
        value.textContent = select.value || "défaut";
        renderLlmSummary();
        saveLlmSettings();
      });
      row.appendChild(select);
      value.textContent = current || "défaut";
    } else {
      const input = document.createElement("input");
      input.type = "range";
      input.className = "llm-range";
      input.id = `llm-${key}`;
      input.min = field.min;
      input.max = field.max;
      input.step = field.step;
      input.value = current ?? field.default;
      const show = () => {
        value.textContent =
          field.kind === "int" ? String(Number(input.value)) : Number(input.value).toFixed(2).replace(".", ",");
      };
      show();
      input.addEventListener("input", () => {
        state.llm.values[key] = field.kind === "int" ? Number(input.value) : Number(input.value);
        show();
        renderLlmSummary();
        saveLlmSettings();
      });
      row.appendChild(input);
    }
    if (field.hint) row.appendChild(el("span", "llm-hint", field.hint));
    rows.appendChild(row);
  });

  setLlmSwitch(Boolean(state.llm.enabled));
  // The honest line: what this provider will apply, and what it will drop.
  const support = box.querySelector("#llm-support");
  support.textContent = "";
  const info = (state.llmSpec && state.llmSpec.support) || {};
  if (info.applied) {
    support.appendChild(
      el("p", null, `Appliqué par ${info.provider || "ce fournisseur"} : ${info.applied.join(", ") || "aucun"}`)
    );
  }
  (info.ignored || []).forEach((entry) => {
    support.appendChild(el("p", "llm-ignored", `Ignoré : ${entry.name} — ${entry.reason}`));
  });
  support.appendChild(
    el(
      "p",
      "llm-note",
      "Rien n'est envoyé tant que « Paramètres personnalisés » est décoché : le tour garde les valeurs par défaut du modèle."
    )
  );
}

// The master switch of the conversation settings. It used to be a bare 13 px
// checkbox labelled "Paramètres personnalisés": an on/off control nobody sees,
// on a panel that changes what gets sent to the provider.
function setLlmSwitch(enabled) {
  const master = $("llm-enabled");
  if (!master) return;
  const on = Boolean(enabled);
  master.setAttribute("aria-checked", String(on));
  master.classList.toggle("on", on);
  const label = master.querySelector(".llm-switch-label");
  if (label) {
    label.textContent = `Paramètres personnalisés : ${on ? "activés" : "désactivés"}`;
  }
}

async function loadLlmPanel() {
  let data;
  try {
    const query = state.sessionId ? `?session_id=${encodeURIComponent(state.sessionId)}` : "";
    data = await api(`/api/llm/settings${query}`);
  } catch (error) {
    return;
  }
  state.llmSpec = data;
  state.llmDefaults = data.defaults;
  // Fill the missing keys from the defaults, without erasing what the user set.
  const values = { ...((data.defaults && data.defaults.values) || {}) };
  Object.keys(data.spec || {}).forEach((key) => {
    if (state.llm.values[key] !== undefined) values[key] = state.llm.values[key];
  });
  state.llm.values = values;
  if (!state.llm.enabled && !Object.keys(state.llm.values).length) {
    state.llm.enabled = false;
  }
  renderLlmRows();
  renderLlmSummary();
}

function openLlmPanel() {
  let panel = $("llm-panel");
  if (!panel) {
    panel = el("aside", "llm-panel");
    panel.id = "llm-panel";
    panel.innerHTML = `
      <header class="llm-head">
        <span>Réglages de la conversation</span>
        <button id="llm-close" class="ghost" title="Fermer" aria-label="Fermer">×</button>
      </header>
      <button type="button" id="llm-enabled" class="llm-switch" role="switch" aria-checked="false">
        <span class="llm-switch-knob" aria-hidden="true"></span>
        <span class="llm-switch-label">Paramètres personnalisés : désactivés</span>
      </button>
      <div id="llm-rows" class="llm-rows"></div>
      <div id="llm-support" class="llm-support"></div>
      <div class="llm-actions">
        <button id="llm-reset" class="data-button" type="button">Réinitialiser</button>
      </div>
    `;
    document.body.appendChild(panel);
    panel.querySelector("#llm-close").addEventListener("click", () => panel.remove());
    panel.querySelector("#llm-enabled").addEventListener("click", () => {
      state.llm.enabled = !state.llm.enabled;
      setLlmSwitch(state.llm.enabled);
      renderLlmRows();
      renderLlmSummary();
      saveLlmSettings();
    });
    panel.querySelector("#llm-reset").addEventListener("click", () => {
      state.llm = {
        enabled: false,
        values: { ...((state.llmDefaults && state.llmDefaults.values) || {}) },
      };
      renderLlmRows();
      renderLlmSummary();
      saveLlmSettings();
    });
  }
  panel.classList.add("open");
  loadLlmPanel().catch(() => {});
}

// --- console panes (P6 v2): every view can leave the column ------------------ //
// The docking layer, built rather than borrowed. Measured before deciding:
// dockview-core 8.9.0 (no dependencies) is 402 KB minified and, at load, builds
// <style> elements and appends them to document.head (dockview.esm.mjs:490). With
// `style-src 'self'` and no 'unsafe-inline' those sheets are dropped - the exact
// hole we refused for Mermaid. The `noStyle` build avoids the injection but the
// drag overlay rules go with it. So: the views move as DOM nodes between the tab
// strip and a floating window, which keeps their listeners, their scroll and
// their CSS, and needs no library at all.
const PANE_TITLES = {
  keys: "Clé API",
  local: "IA locale",
  providers: "Providers",
  models: "Models",
  uhb: "Second brain",
  memory: "Memory",
  decision: "Decision",
  telemetry: "Télémétrie",
  data: "Données",
};
const PANE_KEY = "uh.panes.v1";
const paneHomes = {};
let paneZ = 70;

function paneWindowFor(name) {
  return document.getElementById(`pane-${name}`);
}

function openPaneWindows() {
  return Array.from(document.querySelectorAll(".pane-window"));
}

function readPaneLayout() {
  try {
    const raw = window.localStorage.getItem(PANE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    return {};  // a corrupted layout must not stop the app from booting
  }
}

function savePaneLayout() {
  const layout = {};
  openPaneWindows().forEach((win) => {
    const rect = win.getBoundingClientRect();
    layout[win.dataset.view] = {
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  });
  try {
    window.localStorage.setItem(PANE_KEY, JSON.stringify(layout));
  } catch (error) {
    // Private mode or a full quota: the cockpit still works, it just forgets.
  }
}

function placePane(win, rect) {
  win.style.left = `${Math.round(rect.left)}px`;
  win.style.top = `${Math.round(rect.top)}px`;
  win.style.width = `${Math.round(rect.width)}px`;
  win.style.height = `${Math.round(rect.height)}px`;
}

function magneticRect(rect) {
  // Same 28 px threshold as the dockable panel; a pane taller than 60 % of the
  // viewport never snaps to the bottom (it is always "near" it).
  const bottomHit =
    window.innerHeight - (rect.top + rect.height) <= DOCK_SNAP &&
    rect.height < window.innerHeight * 0.6;
  if (bottomHit) {
    return { left: 12, top: window.innerHeight - rect.height - 12, width: window.innerWidth - 24, height: rect.height };
  }
  if (rect.left <= DOCK_SNAP) {
    return { left: 12, top: 12, width: rect.width, height: window.innerHeight - 24 };
  }
  if (window.innerWidth - (rect.left + rect.width) <= DOCK_SNAP) {
    return { left: window.innerWidth - rect.width - 12, top: 12, width: rect.width, height: window.innerHeight - 24 };
  }
  return {
    left: Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8)),
    top: Math.max(8, Math.min(rect.top, window.innerHeight - 60)),
    width: rect.width,
    height: rect.height,
  };
}

function wirePaneDrag(win, head) {
  let drag = null;
  head.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button")) return;
    const rect = win.getBoundingClientRect();
    drag = { dx: event.clientX - rect.left, dy: event.clientY - rect.top, width: rect.width, height: rect.height };
    win.classList.add("dragging");
    try {
      head.setPointerCapture(event.pointerId);
    } catch (error) {
      // A synthetic pointer has nothing to capture; the drag still works.
    }
    event.preventDefault();
  });
  head.addEventListener("pointermove", (event) => {
    if (!drag) return;
    placePane(win, {
      left: event.clientX - drag.dx,
      top: event.clientY - drag.dy,
      width: drag.width,
      height: drag.height,
    });
  });
  head.addEventListener("pointerup", () => {
    if (!drag) return;
    win.classList.remove("dragging");
    drag = null;
    const rect = win.getBoundingClientRect();
    placePane(win, magneticRect({ left: rect.left, top: rect.top, width: rect.width, height: rect.height }));
    savePaneLayout();
  });
}

function detachPane(name, rect) {
  if (isPhone()) return null;
  const view = $(`${name}-view`);
  if (!view || paneWindowFor(name)) return paneWindowFor(name);

  paneHomes[name] = { parent: view.parentElement, next: view.nextElementSibling };

  const win = el("section", "pane-window");
  win.id = `pane-${name}`;
  win.dataset.view = name;
  const head = el("header", "pane-head");
  head.appendChild(el("strong", null, PANE_TITLES[name] || name));
  const spacer = el("span", "pane-spacer");
  head.appendChild(spacer);
  const dockBack = el("button", "ghost", "⇲");
  dockBack.type = "button";
  dockBack.title = "Réancrer";
  dockBack.setAttribute("aria-label", "Réancrer");
  dockBack.addEventListener("click", () => dockPane(name));
  const close = el("button", "ghost", "×");
  close.type = "button";
  close.title = "Fermer";
  close.setAttribute("aria-label", "Fermer");
  close.addEventListener("click", () => dockPane(name));
  head.appendChild(dockBack);
  head.appendChild(close);
  const body = el("div", "pane-body");
  win.appendChild(head);
  win.appendChild(body);
  document.body.appendChild(win);

  body.appendChild(view);
  view.hidden = false;
  $(`tab-${name}`).classList.add("detached");

  if (rect) {
    placePane(win, rect);
  } else {
    const open = openPaneWindows().length - 1;
    const width = Math.min(460, Math.max(320, Math.round(window.innerWidth * 0.28)));
    const height = Math.min(Math.round(window.innerHeight * 0.6), Math.max(320, window.innerHeight - 140));
    placePane(win, {
      left: Math.max(8, window.innerWidth - width - 24 - open * 28),
      top: 96 + open * 28,
      width,
      height,
    });
  }
  win.style.zIndex = String(++paneZ);
  win.addEventListener("pointerdown", () => {
    win.style.zIndex = String(++paneZ);
  });
  wirePaneDrag(win, head);
  return win;
}

function dockPane(name) {
  const win = paneWindowFor(name);
  const view = $(`${name}-view`);
  const home = paneHomes[name];
  if (win) win.remove();
  if (view && home && home.parent) {
    home.parent.insertBefore(view, home.next && home.next.isConnected ? home.next : null);
    // A view that comes home follows the active tab again, instead of showing on
    // top of whichever tab is selected.
    view.hidden = state.activeTab !== name;
  }
  const tab = $(`tab-${name}`);
  if (tab) tab.classList.remove("detached");
  delete paneHomes[name];
  savePaneLayout();
}

function resetPanes() {
  openPaneWindows().forEach((win) => dockPane(win.dataset.view));
  try {
    window.localStorage.removeItem(PANE_KEY);
  } catch (error) {
    // nothing to clean
  }
}

function restorePanes() {
  if (isPhone()) return;
  const layout = readPaneLayout();
  Object.entries(layout).forEach(([name, rect]) => {
    if (!PANE_TITLES[name] || !rect) return;
    if (!$(name + "-view")) return;
    detachPane(name, rect);
    // A restored console must have something in it, not an empty box.
    if (name === "telemetry") loadTelemetry().catch(() => {});
    if (name === "data") loadData().catch(() => {});
    if (name === "memory") loadMemory().catch(() => {});
    if (name === "decision") {
      loadDecision().catch(() => {});
      renderExecutionMode().catch(() => {});
    }
    if (name === "uhb") {
      loadMemoryGraph().catch(() => {});
      loadUhb().catch(() => {});
    }
  });
}

function wirePanes() {
  const detach = $("pane-detach");
  if (detach) {
    detach.addEventListener("click", () => {
      const name = state.activeTab || "keys";
      if (paneWindowFor(name)) {
        paneWindowFor(name).style.zIndex = String(++paneZ);
        return;
      }
      detachPane(name);
      savePaneLayout();
    });
  }
  const reset = $("pane-reset");
  if (reset) reset.addEventListener("click", resetPanes);
  window.addEventListener("resize", () => {
    // A window that no longer fits (or a phone) gets its consoles back.
    openPaneWindows().forEach((win) => {
      const rect = win.getBoundingClientRect();
      if (isPhone() || rect.left > window.innerWidth - 40 || rect.top > window.innerHeight - 40) {
        dockPane(win.dataset.view);
      }
    });
  });
  restorePanes();
}

// -------------------------------------------------------------------- boot --
function wireComposer() {
  const input = $("input");
  $("skill-open").addEventListener("click", openSkillMenu);
  $("llm-open").addEventListener("click", openLlmPanel);
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitComposer();
    }
  });
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    submitComposer();
  });
  $("stop").addEventListener("click", stopTurn);
  $("new-session").addEventListener("click", newSession);
  $("new-folder").addEventListener("click", newFolder);
  $("menu-toggle").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  $("panel-toggle").addEventListener("click", () => {
    if (providersAreOpen()) closeProviders();
    else openProviders();
  });
  $("theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  });
}

// A phone cannot run a local model. Saying so once, at the top, is cheaper than
// letting the user wonder why every turn fails.
async function showRemoteBanner() {
  const data = await api("/api/system/capabilities");
  if (data.tier !== "small" && data.tier !== "remote") return;
  const banner = $("remote-banner");
  banner.textContent = `UH runs remote on this device (${data.tier} tier). Configure a provider in the ⚙ panel.`;
  banner.hidden = false;
}

// One boot step, isolated: a rejected load reports itself and the next step still
// runs. The old chain was a single unguarded sequence, so one throw (a TDZ, a bad
// payload, a 500) left the rest of boot undone - session list, transcript, and the
// "no session" state - while the page looked alive.
async function bootStep(label, run) {
  try {
    await run();
    return true;
  } catch (error) {
    console.error(`boot: ${label} failed`, error);
    bootFailures.push(label);
    return false;
  }
}

async function boot() {
  wireLayout();
  wireAttachments();
  wireMic();
  wireChips();
  wireComposer();
  wireViewStale();
  wirePalette();
  wireSearch();
  wireProviders();
  wireRole();
  wireSkillsManager();
  wireDock();
  wirePanes();
  wireModelToggle();
  await bootStep("status", loadStatus);
  // The settings table first, so the first session opened knows its defaults.
  await bootStep("llm-panel", () => loadLlmPanel().catch(() => {}));
  await bootStep("models", loadModels);
  await bootStep("commands", loadCommands);
  await bootStep("sessions", loadSessions);
  if (bootFailures.length) {
    showAppError(
      `UH a démarré partiellement (${bootFailures.join(", ")}). Le chat reste utilisable ; ` +
        "recharge la page (Ctrl+F5) si un panneau est vide."
    );
  }
  // ?panel=conn opens the right tab: a chat answer can hand the user a link
  // instead of a sentence describing where to click.
  openPanelFromUrl();
  // A tab nobody notices does not exist: badge until one connection is configured.
  refreshConnBadge().catch(() => {});
  // Last, so the first-run screen opens over a UI that is already painted.
  // The app is usable before any provider exists: a discreet banner offers the
  // configuration, it never stands in the way. No modal on first launch.
  showSetupBanner().catch(() => {});
  // Narrow screens: the right column starts collapsed (overlay mode).
  if (isNarrow()) closeProviders();
  // The column is open on wide screens and "Fournisseurs" is the default tab, but
  // nothing loaded it at boot: the list stayed empty until another tab was clicked
  // - `showPanelTab` is the only caller that fetches, and boot never called it.
  // Every entry point (⚙, tab click, ?panel=, #providers) now ends in the same
  // coalesced loader, so this costs one request on arrival.
  if (providersAreOpen() && state.activeTab === "keys") openProviders();
  // The mode is a state of the second brain, not of a tab: it is read on arrival.
  refreshModeBadge().catch(() => {});
  // Permanent means current: a second brain that dies mid-session must show up.
  window.setInterval(() => {
    if (!document.hidden) refreshModeBadge().catch(() => {});
  }, 20000);
  installModalAccessibility();
  const badge = $("mode-badge");
  if (badge) badge.addEventListener("click", () => toggleModeFromBadge().catch(() => {}));
  showRemoteBanner().catch(() => {});
  // Open the most recent session on first load: an empty pane on arrival looks
  // like a broken app, and the session the user left is the one they want.
  const newest = document.querySelector("#session-list li[data-id]");
  if (!state.sessionId && newest) await bootStep("open-last", () => openSession(newest.dataset.id));
  if (!state.sessionId && !newest) firstRunHint();
  if (location.hash === "#providers") openProviders();
}

// First launch: an empty chat pane is a wall. One sentence and the link that
// opens the right tab - never "run this command": a capability that lives in the
// interface is reached from the interface.
function firstRunHint() {
  const bubble = el("div", "msg assistant");
  const content = el("div", "content md");
  content.appendChild(
    document.createTextNode("Bienvenue dans UH. Il s'ouvre déjà : le chat, les fichiers et la mémoire locale fonctionnent. ")
  );
  const ai = document.createElement("a");
  ai.href = "/?panel=local";
  ai.textContent = "Configurer une IA";
  content.appendChild(ai);
  content.appendChild(document.createTextNode(" (Ollama, TGI ou clé API), ou brancher ce que tu utilises : "));
  const conn = document.createElement("a");
  conn.href = "/?panel=conn";
  conn.textContent = "ouvrir Connexions";
  content.appendChild(conn);
  content.appendChild(
    document.createTextNode(" — un stockage cloud, un serveur d'outils, un IDE. Tout se règle depuis l'interface, sans commande.")
  );
  bubble.appendChild(content);
  $("messages").appendChild(bubble);
}

// ------------------------------------------------------------------ splash -- //
// The splash is static markup in index.html so the very first paint has
// something on screen; this only ends it. Two ways out: boot() resolved (the
// shell is wired and the first calls landed), or the timer - a hung API must not
// trap the operator behind a pulsing mark. Never a click: a splash you can
// dismiss is a splash that hid a failure.
const SPLASH_FADE_MS = 380;
const SPLASH_LIMIT_MS = 4000;
// A splash dismissed in 120 ms is a white flash with a flicker in it: the mark is
// part of the product, so it stays long enough to be read. It never *waits* for
// anything - boot() resolving is still what ends it - and it is never longer
// than the boot plus this.
const SPLASH_MIN_MS = 900;
const SPLASH_SHOWN_AT = (typeof performance !== "undefined" ? performance.now() : 0);
let splashGone = false;
let splashPending = false;

function dismissSplash() {
  if (splashGone) return;
  const elapsed = (typeof performance !== "undefined" ? performance.now() : SPLASH_MIN_MS) -
    SPLASH_SHOWN_AT;
  if (elapsed < SPLASH_MIN_MS) {
    if (!splashPending) {
      splashPending = true;
      setTimeout(dismissSplash, SPLASH_MIN_MS - elapsed);
    }
    return;
  }
  splashGone = true;
  const splash = $("splash");
  if (!splash) return;
  splash.classList.add("is-done");
  // Not transitionend: with prefers-reduced-motion there is no transition.
  setTimeout(() => splash.remove(), SPLASH_FADE_MS + 40);
}

setTimeout(dismissSplash, SPLASH_LIMIT_MS);

boot()
  .then(dismissSplash)
  .catch((error) => {
    dismissSplash();
    // The shell stays: a failed boot is one banner, not a blank page. The chat is
    // usually still usable, and a reload is one click away.
    showAppError(`UH n'a pas fini de démarrer : ${error.message}. Recharge la page (Ctrl+F5).`);
  });

// --------------------------------------------------------------------------- //
// Connexions: cloud storage, MCP servers, and the one popup that adds them
// --------------------------------------------------------------------------- //
// Three ideas share this tab, and none of them is a model key: a storage account
// (sort, sync, back up folders), an MCP server (a tool UH may call), an IDE. The
// keys live in "Clé API", the local model in "IA locale" - and the header says so.
const HTTP_STATE_TEXT = {
  connected: "connecté",
  configured: "configuré",
  connecting: "connexion en cours",
  auth: "jeton refusé",
  unreachable: "injoignable",
  incompatible: "protocole incompatible",
  timeout: "délai dépassé",
  disconnected: "coupure détectée",
  reconnecting: "reconnexion en cours",
  invalid: "adresse refusée",
};

function httpStateDot(state) {
  if (state === "connected") return "dot-ok";
  if (!state || state === "configured" || state === "connecting" || state === "reconnecting") {
    return "dot-off";
  }
  return "dot-warn";
}

function httpStateLine(server) {
  if (server.state === "connected") {
    const count = (server.tools || []).length;
    const when = server.duration_ms !== undefined ? ` · ${server.duration_ms} ms` : "";
    return `connecté · ${count} outil(s)${when}`;
  }
  const label = HTTP_STATE_TEXT[server.state] || server.state || "configuré";
  return server.reason ? `${label} — ${server.reason}` : label;
}

// One server row: state, what it is, and the button that asks it *now*.
function mcpServerRow(server) {
  const row = el("div", "tool-row");
  const dot = el("span", `dot ${httpStateDot(server.state)}`);
  dot.setAttribute("aria-hidden", "true");
  row.appendChild(dot);
  row.appendChild(el("span", "tool-name", `${server.transport === "stdio" ? "stdio" : "http"} · ${server.name}`));

  const tokenHint = server.has_token ? ` · jeton ${server.token}` : "";
  const where = server.url || server.command || "";
  row.appendChild(el("span", "tool-detail", `${where}${tokenHint}`));

  const state = el("span", "tool-detail", httpStateLine(server));
  row.appendChild(state);
  if (server.insecure) {
    row.appendChild(
      el("span", "panel-hint", "HTTP non chiffré : le jeton circule en clair. Préfère https://.")
    );
  }

  const test = el("button", "data-button", "Tester la connexion");
  test.type = "button";
  test.setAttribute("aria-label", `Tester la connexion à ${server.name}`);
  test.addEventListener("click", async () => {
    test.disabled = true;
    state.textContent = "connexion en cours…";
    try {
      const result = await api(`/api/mcp/${encodeURIComponent(server.name)}/test`, {
        method: "POST",
      });
      const count = (result.tools || []).length;
      if (result.state === "connected") {
        state.textContent = `connecté · ${count} outil(s) · ${result.duration_ms} ms`;
        dot.className = "dot dot-ok";
      } else {
        state.textContent = `${HTTP_STATE_TEXT[result.state] || result.state} — ${
          result.advice || result.reason || ""
        }`;
        dot.className = `dot ${httpStateDot(result.state)}`;
      }
    } catch (error) {
      state.textContent = `échec du test : ${error.message}`;
      dot.className = "dot dot-warn";
    } finally {
      test.disabled = false;
    }
  });
  row.appendChild(test);
  // A green dot says "the handshake worked", not "a tool answers". One read-only
  // call, timed, is the difference between a promise and a measurement - and it
  // is the answer to "Positron marche ou pas ?" without leaving the panel.
  if (server.transport !== "stdio") {
    const readOnly = (server.tools || []).find((name) =>
      /^(list_|get_|search_)/.test(String(name))
    );
    const probe = el("button", "data-button", "Tester un appel");
    probe.type = "button";
    probe.setAttribute("aria-label", `Exécuter un outil en lecture seule sur ${server.name}`);
    probe.title = `Exécute ${readOnly || "list_sessions"} et mesure la latence`;
    probe.addEventListener("click", async () => {
      probe.disabled = true;
      state.textContent = "appel en cours…";
      try {
        const result = await api(`/api/mcp/${encodeURIComponent(server.name)}/call`, {
          method: "POST",
          body: JSON.stringify({ tool: readOnly || "list_sessions" }),
        });
        if (result.ok) {
          const preview = JSON.stringify(result.result || {}).slice(0, 140);
          state.textContent = `appel ok · ${result.latency_ms} ms · ${preview}`;
          dot.className = "dot dot-ok";
        } else {
          state.textContent = `appel refusé : ${result.error}`;
          dot.className = "dot dot-warn";
        }
      } catch (error) {
        state.textContent = `échec de l'appel : ${error.message}`;
        dot.className = "dot dot-warn";
      } finally {
        probe.disabled = false;
      }
    });
    row.appendChild(probe);
  }
  return row;
}

function mcpSection(servers, note) {
  const box = el("div", "tool-cloud");
  box.appendChild(el("div", "tool-name", "Serveurs MCP"));
  box.appendChild(
    el(
      "p",
      "data-note",
      note ||
        "Un outil que UH peut appeler : un serveur lancé comme un programme, un IDE, un service. Chaque appel d'un serveur distant te demande ton accord."
    )
  );
  const rows = el("div", "tool-rows");
  if (!servers.length) {
    rows.appendChild(
      el("p", "data-note", "Aucun serveur pour l'instant. « + Connecter un MCP » en ajoute un.")
    );
  }
  servers.forEach((server) => rows.appendChild(mcpServerRow(server)));
  box.appendChild(rows);

  const add = el("button", "data-button primary", "+ Connecter un MCP");
  add.type = "button";
  add.addEventListener("click", () => mcpConnectModal());
  box.appendChild(add);
  return box;
}

// --------------------------------------------------------------------------- //
// The popup: name, transport, command or URL, an import, a test, then a save
// --------------------------------------------------------------------------- //
// "Enregistrer" stays disabled until the probe answered `connected`: a broken
// entry never reaches config.toml. Editing any field after a successful test
// disables it again - the tested configuration is the one that gets saved.
function mcpConnectModal(initial) {
  const backdrop = el("div", "modal-backdrop");
  const modal = el("div", "modal mcp-connect");
  const form = el("form", "modal-form");
  form.appendChild(el("p", "modal-title", "Connecter un serveur MCP"));
  form.appendChild(
    el(
      "p",
      "data-note",
      "Un programme (stdio) ou une adresse (http/sse). UH teste avant d'enregistrer : rien n'entre dans config.toml sans un test réussi, et un jeton part dans mcp-secrets.toml."
    )
  );

  const field = (label, placeholder, multiline = false) => {
    const wrap = el("label", "cloud-field");
    wrap.appendChild(el("span", "cloud-label", label));
    const input = multiline ? document.createElement("textarea") : document.createElement("input");
    input.className = multiline ? "modal-area" : "cloud-input";
    if (multiline) input.rows = 3;
    else {
      input.type = "text";
      input.autocomplete = "off";
    }
    input.placeholder = placeholder;
    wrap.appendChild(input);
    return { wrap, input };
  };

  // Import first: a file that already exists is the common case.
  const importRow = el("div", "mcp-import");
  importRow.appendChild(el("span", "cloud-label", "Importer un fichier .json ou .yaml"));
  const file = document.createElement("input");
  file.type = "file";
  file.accept = ".json,.yaml,.yml,application/json";
  file.className = "cloud-input";
  importRow.appendChild(file);
  importRow.appendChild(el("span", "panel-hint", "ou dépose le fichier sur cette fenêtre"));
  form.appendChild(importRow);

  const transport = document.createElement("select");
  transport.className = "modal-input";
  [
    ["stdio", "stdio — un programme local"],
    ["http", "http — une adresse (Streamable HTTP)"],
    ["sse", "sse — une adresse (flux d'événements)"],
  ].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    transport.appendChild(option);
  });
  const transportWrap = el("label", "cloud-field");
  transportWrap.appendChild(el("span", "cloud-label", "Transport"));
  transportWrap.appendChild(transport);
  form.appendChild(transportWrap);

  const name = field("Nom (minuscules, chiffres, - ou _)", "mon-serveur");
  const command = field("Commande", "npx");
  const args = field("Arguments (un par ligne)", "-y\n@modelcontextprotocol/server-filesystem\nC:/docs", true);
  const env = field("Variables d'environnement (KEY=valeur, une par ligne)", "S3_BUCKET=mes-docs", true);
  const url = field("Adresse du serveur MCP", "http://127.0.0.1:8787/mcp", false);
  url.input.type = "url";
  const token = field("Jeton (optionnel, jamais réaffiché)", "••••", false);
  token.input.type = "password";
  const insecureWrap = el("label", "cloud-field");
  const insecure = document.createElement("input");
  insecure.type = "checkbox";
  insecureWrap.appendChild(insecure);
  insecureWrap.appendChild(
    el("span", "cloud-label", "Autoriser le HTTP non chiffré vers une autre machine")
  );

  const stdioFields = [name, command, args, env];
  const httpFields = [name, url, token];
  stdioFields.forEach((item) => form.appendChild(item.wrap));
  httpFields.forEach((item) => form.appendChild(item.wrap));
  form.appendChild(insecureWrap);

  const status = el("p", "data-note mcp-status", "Teste la connexion : « Enregistrer » s'active ensuite.");
  form.appendChild(status);

  const actions = el("div", "modal-actions");
  const test = el("button", "data-button", "Tester la connexion");
  const save = el("button", "data-button primary", "Enregistrer");
  const cancel = el("button", "data-button", "Annuler");
  [test, save, cancel].forEach((button) => {
    button.type = "button";
    actions.appendChild(button);
  });
  save.disabled = true;
  form.appendChild(actions);
  modal.appendChild(form);
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  modal.addEventListener("click", (event) => event.stopPropagation());
  backdrop.addEventListener("click", () => backdrop.remove());
  closeOnEscape(backdrop);

  // The tested configuration is what gets saved: any edit invalidates it.
  let tested = null;
  const invalidate = () => {
    tested = null;
    save.disabled = true;
  };
  [name.input, command.input, args.input, env.input, url.input, token.input, transport, insecure]
    .forEach((node) => node.addEventListener("input", invalidate));
  transport.addEventListener("change", invalidate);

  const syncTransport = () => {
    const isHttp = transport.value !== "stdio";
    stdioFields.forEach((item) => (item.wrap.hidden = isHttp));
    httpFields.forEach((item) => (item.wrap.hidden = !isHttp));
  };
  transport.addEventListener("change", syncTransport);
  syncTransport();

  const payload = () => {
    const variables = {};
    env.input.value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        const index = line.indexOf("=");
        if (index > 0) variables[line.slice(0, index).trim()] = line.slice(index + 1).trim();
      });
    const common = { name: name.input.value.trim(), transport: transport.value };
    if (transport.value === "stdio") {
      return {
        ...common,
        command: command.input.value.trim(),
        args: args.input.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
        env: variables,
      };
    }
    return {
      ...common,
      url: url.input.value.trim(),
      token: token.input.value.trim(),
      allow_insecure: insecure.checked,
    };
  };

  const fill = (server) => {
    transport.value = server.transport || "stdio";
    syncTransport();
    name.input.value = server.name || "";
    command.input.value = server.command || "";
    args.input.value = (server.args || []).join("\n");
    env.input.value = Object.entries(server.env || {})
      .map(([key, value]) => `${key}=${value}`)
      .join("\n");
    url.input.value = server.url || "";
    token.input.value = server.token || "";
    insecure.checked = false;
    invalidate();
    status.textContent = `Importé : ${server.name}. Teste la connexion avant d'enregistrer.`;
  };

  const importText = async (text, filename) => {
    try {
      const result = await api("/api/mcp/import", {
        method: "POST",
        body: JSON.stringify({ text, filename }),
      });
      const found = result.servers || [];
      if (!found.length) {
        status.textContent = "Ce fichier ne contient aucun serveur.";
        return;
      }
      fill(found[0]);
      if (found.length > 1) {
        status.textContent = `${found.length} serveurs dans le fichier : ${
          found[0].name
        } est rempli, les autres s'ajoutent en rouvrant le fichier (${found
          .map((server) => server.name)
          .join(", ")}).`;
      }
    } catch (error) {
      status.textContent = `Import refusé : ${error.message}`;
    }
  };

  const readFile = async (chosen) => {
    if (!chosen) return;
    const text = await chosen.text();
    await importText(text, chosen.name);
  };
  file.addEventListener("change", () => readFile(file.files && file.files[0]));
  modal.addEventListener("dragover", (event) => {
    event.preventDefault();
    modal.classList.add("is-dragging");
  });
  modal.addEventListener("dragleave", () => modal.classList.remove("is-dragging"));
  modal.addEventListener("drop", async (event) => {
    event.preventDefault();
    modal.classList.remove("is-dragging");
    const dropped = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    await readFile(dropped);
  });

  test.addEventListener("click", async () => {
    test.disabled = true;
    status.textContent = "test en cours (5 s max)…";
    try {
      const result = await api("/api/mcp/probe", {
        method: "POST",
        body: JSON.stringify(payload()),
      });
      if (result.state === "connected") {
        tested = payload();
        save.disabled = false;
        const count = (result.tools || []).length;
        status.textContent = `Connecté · ${count} outil(s) · ${result.duration_ms} ms. « Enregistrer » est actif.`;
      } else {
        invalidate();
        status.textContent = `${HTTP_STATE_TEXT[result.state] || result.state} — ${
          result.advice || result.reason || ""
        }`;
      }
    } catch (error) {
      invalidate();
      status.textContent = `Refusé : ${error.message}`;
    } finally {
      test.disabled = false;
    }
  });

  save.addEventListener("click", async () => {
    if (!tested) return;
    save.disabled = true;
    save.textContent = "enregistrement…";
    try {
      if (tested.transport === "stdio") {
        await api("/api/mcp/custom", {
          method: "POST",
          body: JSON.stringify({
            name: tested.name,
            command: tested.command,
            args: tested.args,
            env: tested.env,
          }),
        });
      } else {
        await api("/api/mcp/http", {
          method: "POST",
          body: JSON.stringify({
            name: tested.name,
            url: tested.url,
            token: tested.token,
            allow_insecure: tested.allow_insecure,
          }),
        });
      }
      backdrop.remove();
      await loadConnections();
    } catch (error) {
      status.textContent = `Refusé : ${error.message}`;
      save.disabled = false;
    } finally {
      save.textContent = "Enregistrer";
    }
  });
  cancel.addEventListener("click", () => backdrop.remove());

  if (initial) fill(initial);
  name.input.focus();
  return modal;
}

async function loadConnections() {
  const view = $("conn-view");
  if (!view) return;
  view.textContent = "";
  // A contextual card, not a wall: the tab says what it configures, and where
  // the two things that look like keys actually live.
  const help = el("div", "help-card");
  help.appendChild(el("strong", null, "Ce que vous configurez ici"));
  help.appendChild(
    el(
      "p",
      "data-note",
      "Gérez ici toutes vos connexions externes : stockages cloud (S3, Google Drive) et serveurs MCP (Positron, VS Code, un outil personnalisé). Les clés des modèles sont dans « Fournisseurs », l'IA de cette machine dans « IA locale »."
    )
  );
  view.appendChild(help);
  const list = el("div", "data-list");
  view.appendChild(list);
  await mountCloud(list);

  let servers = [];
  let note = "";
  try {
    const catalog = await api("/api/mcp/cloud");
    servers = catalog.http_servers || [];
    note = catalog.http_note || "";
  } catch (error) {
    /* an older backend: the popup still works, the list is just empty */
  }
  // The backend note describes a recorded server; with none, the section's own
  // sentence is the right one.
  if (!servers.length) note = "";
  try {
    const status = await api("/api/tools/status");
    const mcp = status.mcp || {};
    const stdio = (mcp.servers || []).map((server) => ({
      ...server,
      transport: "stdio",
      url: server.command || "",
      has_token: false,
    }));
    // The HTTP entries declared in config.toml (Positron, an IDE…) are not in the
    // cloud store: reading only that store is why the panel showed none of them
    // while the model could already call their tools.
    const seen = new Set(servers.map((item) => `${item.name}|${item.url || ""}`));
    const http = (mcp.http || [])
      .filter((server) => !seen.has(`${server.name}|${server.url || ""}`))
      .map((server) => ({ ...server, transport: "http", has_token: false }));
    servers = servers.concat(http, stdio);
  } catch (error) {
    /* no tool list: the cloud rows are enough to work */
  }
  view.appendChild(mcpSection(servers, note));
}



// --------------------------------------------------------------------------- //
// self-repair: propose, show the diff, apply on a branch or reject
// --------------------------------------------------------------------------- //
function closeOnEscape(backdrop) {
  document.addEventListener(
    "keydown",
    function escape(event) {
      if (event.key === "Escape") {
        backdrop.remove();
        document.removeEventListener("keydown", escape);
      }
    },
    { once: true }
  );
}

function mountFix(section) {
  const box = el("div", "tool-cloud");
  box.appendChild(el("div", "tool-name", "Réparer UH"));
  box.appendChild(
    el(
      "p",
      "data-note",
      "L'IA propose un correctif sur son propre code. Rien n'est écrit avant ton accord, et le patch doit s'appliquer : il est vérifié avec git apply --check."
    )
  );
  const area = document.createElement("textarea");
  area.className = "modal-area fix-area";
  area.rows = 3;
  area.placeholder = "Qu'est-ce qui est cassé ? (ex: le bouton Rêves ne s'ouvre pas quand aucun rêve n'existe)";
  box.appendChild(area);
  const status = el("p", "data-note", "");
  const button = el("button", "data-button", "Proposer un correctif");
  button.type = "button";
  button.addEventListener("click", async () => {
    const instruction = area.value.trim();
    if (!instruction) {
      status.textContent = "Décris d'abord ce qui est cassé.";
      return;
    }
    button.disabled = true;
    button.textContent = "réflexion…";
    status.textContent = "L'IA lit le code et prépare un diff…";
    try {
      const proposal = await api("/api/fix", {
        method: "POST",
        body: JSON.stringify({ instruction }),
      });
      status.textContent = "";
      openDiffModal(proposal);
    } catch (error) {
      status.textContent = `Refusé : ${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = "Proposer un correctif";
    }
  });
  box.appendChild(button);
  box.appendChild(status);
  section.appendChild(box);
}

function openDiffModal(proposal) {
  const backdrop = el("div", "modal-backdrop");
  const modal = el("div", "modal modal-wide");
  const form = el("form", "modal-form");
  form.appendChild(el("p", "modal-title", "Correctif proposé"));
  form.appendChild(
    el(
      "p",
      "data-note",
      proposal.applies
        ? `Le patch s'applique (git apply --check). Fichiers : ${(proposal.files || []).join(", ") || "?"}`
        : `Le patch ne s'applique pas : ${proposal.error}`
    )
  );
  if (proposal.notes && proposal.notes.length) {
    form.appendChild(el("p", "data-note", proposal.notes.join(" · ")));
  }
  const pre = el("pre", "modal-diff", proposal.diff || "(diff vide)");
  form.appendChild(pre);

  const error = el("p", "modal-error");
  const actions = el("div", "modal-actions");
  const reject = el("button", "data-button", "Rejeter");
  reject.type = "button";
  const applyBtn = el("button", "data-button primary", "Appliquer sur une branche");
  applyBtn.type = "button";
  applyBtn.disabled = !proposal.applies;
  actions.appendChild(reject);
  actions.appendChild(applyBtn);
  form.appendChild(error);
  form.appendChild(actions);
  modal.appendChild(form);
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  modal.addEventListener("click", (event) => event.stopPropagation());
  backdrop.addEventListener("click", () => backdrop.remove());
  closeOnEscape(backdrop);
  reject.addEventListener("click", () => backdrop.remove());

  applyBtn.addEventListener("click", async () => {
    applyBtn.disabled = true;
    applyBtn.textContent = "application…";
    error.textContent = "";
    try {
      const outcome = await api(`/api/fix/${encodeURIComponent(proposal.stamp)}/apply`, {
        method: "POST",
      });
      if (outcome.applied) {
        error.textContent = `Appliqué sur ${outcome.branch}. Rien n'est commité ni poussé : relis le diff, puis commit toi-même.`;
        applyBtn.textContent = "Appliqué";
      } else {
        error.textContent = `Appliqué puis annulé : un contrôle a échoué (${(outcome.failed || []).join(" ; ")})`;
        applyBtn.textContent = "Annulé";
      }
    } catch (err) {
      error.textContent = `Échec : ${err.message}`;
      applyBtn.disabled = false;
      applyBtn.textContent = "Appliquer sur une branche";
    }
  });
}

// --------------------------------------------------------------------------- //
// first run: an invitation, not a wall
// --------------------------------------------------------------------------- //
// The cockpit opens on the chat whatever the state of the machine. A missing
// model is a discreet banner with three ways out (this machine, a key, or "later"),
// never a modal between a person and the interface they just opened.
const SETUP_DISMISSED = "uh.setup.dismissed";

async function showSetupBanner() {
  const banner = $("setup-banner");
  if (!banner) return;
  let setup = null;
  try {
    setup = await api("/api/setup");
  } catch (error) {
    return; // an older backend: no banner, the UI still works
  }
  if (!setup || !setup.needed) {
    banner.hidden = true;
    return;
  }
  if (localStorage.getItem(SETUP_DISMISSED) === "1") return;

  banner.textContent = "";
  const text = el(
    "span",
    "setup-text",
    "Aucun modèle n'est configuré : UH s'ouvre, mais il ne peut pas encore répondre. "
  );
  const link = document.createElement("a");
  link.href = "/?panel=local";
  link.textContent = "Configurer une IA";
  text.appendChild(link);
  text.appendChild(document.createTextNode(" (locale ou clé API)."));
  banner.appendChild(text);

  const configure = el("button", "data-button primary", "Configurer une IA");
  configure.type = "button";
  configure.addEventListener("click", () => {
    openProviders();
    showPanelTab("local");
  });
  banner.appendChild(configure);

  const keyed = el("button", "data-button", "J'ai une clé API");
  keyed.type = "button";
  keyed.addEventListener("click", () => {
    openProviders();
    showPanelTab("keys");
  });
  banner.appendChild(keyed);

  const later = el("button", "ghost", "Plus tard");
  later.type = "button";
  later.addEventListener("click", () => {
    localStorage.setItem(SETUP_DISMISSED, "1");
    banner.hidden = true;
  });
  banner.appendChild(later);
  banner.hidden = false;
}

// --------------------------------------------------------------------------- //
// IA locale: the guide that obliges nothing
// --------------------------------------------------------------------------- //
const OLLAMA_DOWNLOAD = "https://ollama.com/download";
const TGI_DOCS = "https://huggingface.co/docs/text-generation-inference";
const SMALL_LOCAL_MODEL = "qwen2.5:0.5b";

function localLink(href, label, primary = false) {
  const link = document.createElement("a");
  link.href = href;
  link.className = primary ? "data-button primary" : "data-button";
  link.textContent = label;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

async function loadLocalPanel() {
  const box = $("local-panel");
  if (!box) return;
  box.textContent = "";
  let setup = null;
  try {
    setup = await api("/api/setup");
  } catch (error) {
    box.appendChild(el("p", "panel-hint", `statut indisponible : ${error.message}`));
    return;
  }
  const ollama = (setup && setup.ollama) || {};

  const guide = el("section", "local-guide");
  guide.appendChild(el("h3", null, "Faire tourner une IA sur cette machine"));
  guide.appendChild(
    el(
      "p",
      "data-note",
      "UH s'ouvre sans rien configurer. Pour qu'il réponde, il lui faut un modèle : le plus simple est Ollama, gratuit et local. Une clé API marche aussi, sans rien installer."
    )
  );

  const status = el("p", "local-status");
  if (ollama.running) {
    const count = (ollama.models || []).length;
    status.textContent = `Ollama tourne · ${count} modèle(s) disponible(s)`;
  } else if (ollama.installed) {
    status.textContent = "Ollama est installé, mais il ne tourne pas.";
  } else {
    status.textContent = "Ollama n'est pas installé sur cette machine.";
  }
  guide.appendChild(status);

  const actions = el("div", "local-actions");
  const again = el("button", "data-button", "Vérifier à nouveau");
  again.type = "button";
  again.addEventListener("click", () => loadLocalPanel().catch(() => {}));
  actions.appendChild(again);
  if (!ollama.installed) {
    actions.appendChild(localLink(OLLAMA_DOWNLOAD, "Télécharger Ollama", true));
  }
  guide.appendChild(actions);

  if (!ollama.installed) {
    guide.appendChild(
      el(
        "p",
        "data-note",
        "UH n'installe pas Ollama à ta place : c'est un installeur système. Le bouton ouvre le site officiel ; reviens ensuite ici et clique sur « Vérifier à nouveau »."
      )
    );
  } else if (!ollama.running) {
    guide.appendChild(
      el(
        "p",
        "data-note",
        "Ouvre Ollama depuis le menu Démarrer (ou son icône dans la barre), puis clique sur « Vérifier à nouveau »."
      )
    );
  }

  // Running with models: one click picks one for the conversations.
  if (ollama.running) {
    const models = ollama.models || [];
    if (!models.length) {
      guide.appendChild(
        el(
          "p",
          "data-note",
          "Aucun modèle téléchargé : prends le plus petit pour démarrer tout de suite."
        )
      );
      const pull = el("button", "data-button primary", `Télécharger ${SMALL_LOCAL_MODEL} (~400 Mo)`);
      pull.type = "button";
      pull.addEventListener("click", async () => {
        pull.disabled = true;
        pull.textContent = "téléchargement lancé…";
        try {
          await api("/api/models/pull", {
            method: "POST",
            body: JSON.stringify({ name: SMALL_LOCAL_MODEL }),
          });
          pull.textContent = "téléchargé (regarde l'onglet Models)";
        } catch (error) {
          pull.textContent = `refusé : ${error.message}`;
          pull.disabled = false;
        }
      });
      guide.appendChild(pull);
    } else {
      // The row is the button. A "Utiliser" per line duplicated the header menu,
      // and a third way to say the same thing is a third thing to explain.
      const list = el("div", "local-models");
      const note = el("p", "panel-hint", "");
      models.forEach((name) => {
        const row = el("button", "local-model local-model-pick");
        row.type = "button";
        row.title = `Utiliser ${name} pour le chat`;
        row.appendChild(el("span", "local-model-name", name));
        row.appendChild(el("span", "local-model-hint", "cliquer pour utiliser"));
        row.addEventListener("click", async () => {
          if (row.disabled) return;
          row.disabled = true;
          note.textContent = `chargement de ${name}…`;
          note.className = "panel-hint";
          try {
            await api("/api/model", {
              method: "POST",
              body: JSON.stringify({ provider: "ollama", model: name }),
            });
            note.textContent = `${name} est le modèle du chat.`;
            note.className = "panel-hint ok";
            await loadStatus();
            await loadModels();
            await loadLlmPanel().catch(() => {});
          } catch (error) {
            // Ollama answers 500 when it cannot put the model in memory. The raw
            // code tells a person nothing: say what happened and what to do.
            note.textContent = /HTTP\s*5\d\d|\b500\b/.test(error.message)
              ? `${name} : non chargé. Ollama n'a pas pu le mettre en mémoire (VRAM ou délai). Réessaie, ou choisis un modèle plus petit.`
              : `${name} : ${error.message}`;
            note.className = "panel-hint warn";
          } finally {
            row.disabled = false;
          }
        });
        list.appendChild(row);
      });
      guide.appendChild(list);
      guide.appendChild(note);
      // The row that answered keeps its marker across a reload of the tab.
      markLocalModelTab();
    }
  }
  box.appendChild(guide);

  // TGI: UH detects it, it never installs it.
  const tgi = el("section", "tool-cloud");
  tgi.appendChild(el("div", "tool-name", "TGI local (Hugging Face)"));
  tgi.appendChild(
    el(
      "p",
      "data-note",
      "Si un serveur TGI tourne déjà sur 127.0.0.1:8080 avec l'API /v1, UH le reconnaît tout seul — rien à régler ici. Sinon, la procédure d'installation est chez Hugging Face."
    )
  );
  tgi.appendChild(localLink(TGI_DOCS, "Procédure TGI (site Hugging Face)"));
  box.appendChild(tgi);

  const keysHint = el("section", "tool-cloud");
  keysHint.appendChild(el("div", "tool-name", "Passer par une clé API"));
  keysHint.appendChild(
    el(
      "p",
      "data-note",
      "Une clé gratuite (OpenRouter, Google AI Studio, Hugging Face…) suffit aussi, et rien ne s'installe."
    )
  );
  const goKeys = el("button", "data-button", "Ouvrir l'onglet Clé API");
  goKeys.type = "button";
  goKeys.addEventListener("click", () => showPanelTab("keys"));
  keysHint.appendChild(goKeys);
  box.appendChild(keysHint);
}


// --------------------------------------------------------------------------- //
// cloud connections
// --------------------------------------------------------------------------- //
// Cloud template fields are re-created on every template switch, so their ids
// come from a counter: two boxes for two servers must not share an id.
let cloudFieldSeq = 0;

async function mountCloud(section) {
  let catalog = null;
  try {
    catalog = await api("/api/mcp/cloud");
  } catch (error) {
    return; // an older backend: no catalogue, no section, no error banner
  }
  const templates = catalog.templates || [];
  if (!templates.length) return;

  const box = el("div", "tool-cloud");
  box.appendChild(el("div", "tool-name", "Stockage cloud (dossiers)"));
  box.appendChild(
    el(
      "p",
      "data-note",
      "Un compte de stockage (S3, Google Drive) pour trier, sauvegarder et retrouver des fichiers. Ce n'est pas une clé de modèle : celles-ci vivent dans l'onglet « Clé API ». Le serveur tourne chez toi (npx) et parle à ton compte ; les clés vont dans mcp-secrets.toml, jamais dans config.toml."
    )
  );

  const select = el("select", "model-select");
  select.id = "cloud-template";
  select.name = "cloud-template";
  templates.forEach((template) => {
    const option = el("option", null, template.label);
    option.value = template.id;
    select.appendChild(option);
  });
  box.appendChild(select);

  const name = el("input", "cloud-input");
  name.id = "cloud-server-name";
  name.name = "cloud-server-name";
  name.setAttribute("aria-label", "nom du serveur");
  name.placeholder = "nom du serveur (ex: s3-docs)";
  name.value = "cloud";
  box.appendChild(name);

  const fields = el("div", "cloud-fields");
  const inputs = new Map();
  const draw = () => {
    fields.textContent = "";
    inputs.clear();
    const template = templates.find((item) => item.id === select.value) || templates[0];
    (template.fields || []).forEach((field) => {
      const row = el("label", "cloud-field");
      row.appendChild(el("span", "cloud-label", field.label + (field.required ? "" : " (optionnel)")));
      const input = el("input", "cloud-input");
      input.id = `cloud-${field.key}-${++cloudFieldSeq}`;
      input.name = field.key;
      input.type = field.secret ? "password" : "text";
      input.placeholder = field.placeholder || "";
      input.autocomplete = "off";
      if (field.help) input.title = field.help;
      inputs.set(field.key, input);
      row.appendChild(input);
      fields.appendChild(row);
    });
    if (template.upload_note) {
      fields.appendChild(el("p", "data-note", template.upload_note));
    }
  };
  select.addEventListener("change", draw);
  draw();
  box.appendChild(fields);

  const status = el("p", "data-note", "");
  const button = el("button", "data-button", "Connecter");
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "connexion…";
    const payload = { template: select.value, name: name.value, fields: {} };
    inputs.forEach((input, key) => {
      if (input.value.trim()) payload.fields[key] = input.value.trim();
    });
    try {
      const result = await api("/api/mcp/cloud", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      status.textContent = result.parsed
        ? `Connecté : ${result.name} (${result.label}). Clique « Tester ${result.name} » ci-dessus.`
        : `Écrit, mais illisible par UH : ${(result.problems || []).join(" ; ")}`;
      inputs.forEach((input) => {
        if (input.type === "password") input.value = "";
      });
      if (typeof loadData === "function") setTimeout(loadData, 1500);
    } catch (error) {
      status.textContent = `Refusé : ${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = "Connecter";
    }
  });
  box.appendChild(button);
  box.appendChild(status);
  section.appendChild(box);
}

// -- P3-16b : Projets & Git, et le cycle de vie des runs UHB ----------------- //
// Ce panneau affiche et pilote ; il ne decide rien. Un run garde est archive par
// UHB dans ~/.uhb/brain-runs.git, un run non garde est supprime par UHB. Ici on
// n'ecrit qu'une requete et on montre la reponse.

const PROJECTS = { data: null, runs: null };

function projectBadge(project) {
  if (!project.ok) return "hors service";
  const bits = [];
  bits.push(project.dirty ? `${project.dirty} modifie(s)` : "propre");
  if (project.ahead) bits.push(`↑${project.ahead}`);
  if (project.behind) bits.push(`↓${project.behind}`);
  return bits.join(" · ");
}

function renderProjects(data) {
  const list = $("projects-list");
  if (!list) return;
  list.textContent = "";
  const projects = (data && data.projects) || [];
  $("projects-hint").textContent = data && data.root
    ? `${projects.length} projet(s) · racine ${data.root}`
    : `${projects.length} projet(s)`;
  if (!projects.length) {
    const empty = el("div", "projects-empty");
    empty.textContent = "Aucun projet enregistre. Ajoute le chemin d'un depot git ci-dessous.";
    list.append(empty);
    return;
  }
  for (const project of projects) {
    const row = el("div", "project-row");
    row.dataset.path = project.path || "";
    if (project.current) row.classList.add("current");

    const head = el("div", "project-head");
    const name = el("strong", "project-name");
    name.textContent = project.label || project.name || project.path;
    const branch = el("span", "project-branch");
    branch.textContent = project.branch || "—";
    const badge = el("span", "project-badge");
    badge.textContent = projectBadge(project);
    if (!project.ok) badge.classList.add("bad");
    else if (project.dirty) badge.classList.add("dirty");
    head.append(name, branch, badge);

    const meta = el("div", "project-meta");
    meta.textContent = project.path + (project.last_commit ? " · " + project.last_commit : "");
    row.append(head, meta);

    if (!project.current && project.ok) {
      const button = el("button", "ghost project-switch");
      button.type = "button";
      button.textContent = "Basculer ici";
      button.addEventListener("click", () => switchProject(project.path));
      row.append(button);
    }
    if (!project.ok && project.reason) {
      const why = el("div", "project-reason");
      why.textContent = project.reason;
      row.append(why);
    }
    list.append(row);
  }
  // Les modifications affichées sont celles du dépôt ACTIF : c'est le seul où le serveur
  // accepte d'indexer, donc le seul dont les boutons peuvent dire la vérité.
  const current = projects.find((item) => item.current) || null;
  if (current) renderGitChanges(current);
}

async function loadProjects() {
  const query = state.sessionId ? `?session_id=${encodeURIComponent(state.sessionId)}` : "";
  try {
    const data = await api(`/api/projects${query}`);
    PROJECTS.data = data;
    renderProjects(data);
    // Le journal suit la même lecture : un seul geste, deux panneaux cohérents.
    await loadGitLog();
  } catch (error) {
    $("projects-hint").textContent = "lecture impossible : " + (error.message || error);
  }
}

async function switchProject(path) {
  const project = (PROJECTS.data?.projects || []).find((item) => item.path === path);
  // On ne deplace pas une session sous les pieds d'un tour en cours sans le dire.
  if (project && project.dirty) {
    const ok = window.confirm(
      `${project.label || project.name} a ${project.dirty} fichier(s) modifie(s).\n` +
        "Basculer quand meme cette conversation sur ce projet ?"
    );
    if (!ok) return;
  }
  try {
    const result = await api("/api/projects/switch", {
      method: "POST",
      body: { path, session_id: state.sessionId || "" },
    });
    await loadProjects();
    await loadRuns();
    say(`projet actif : ${result.workspace}`, "ok");
  } catch (error) {
    say("bascule refusee : " + (error.message || error), "warn");
  }
}

async function addProject() {
  const input = $("project-path");
  const path = (input?.value || "").trim();
  if (!path) return;
  try {
    await api("/api/projects/add", { method: "POST", body: { path } });
    if (input) input.value = "";
    await loadProjects();
    say("projet ajoute", "ok");
  } catch (error) {
    say("ajout refuse : " + (error.message || error), "warn");
  }
}

function renderRuns(data) {
  const list = $("runs-list");
  if (!list) return;
  list.textContent = "";
  if (!data || data.ok === false) {
    const down = el("div", "runs-empty bad");
    down.textContent = "UHB injoignable : " + ((data && data.reason) || "aucune reponse");
    list.append(down);
    $("runs-hint").textContent = "hors ligne";
    return;
  }
  const runs = data.runs || [];
  const policy = data.policy || {};
  $("runs-hint").textContent =
    `${data.count ?? runs.length} run(s) · ${data.bytes ?? 0} o · ` +
    `TTL ${policy.ttl_days ?? "?"} j / ${policy.max_dirs ?? "?"} dossiers` +
    (policy.archive_exists ? " · Cerveaux ok" : " · Cerveaux vide");
  if (!runs.length) {
    const empty = el("div", "runs-empty");
    empty.textContent = "Aucun run recent.";
    list.append(empty);
    return;
  }
  for (const run of runs) {
    const row = el("div", "run-row");
    const label = el("span", "run-name");
    label.textContent = `${run.run} · ${run.bytes} o · ${run.age_days} j` + (run.mode ? ` · ${run.mode}` : "");
    row.append(label);

    const keep = el("button", "ghost");
    keep.type = "button";
    keep.textContent = "Garder";
    keep.title = "Archiver ce run dans le depot Cerveaux (UHB)";
    keep.addEventListener("click", () => runAction(run.run, true));
    row.append(keep);

    const drop = el("button", "ghost run-drop");
    drop.type = "button";
    drop.textContent = "Supprimer";
    drop.addEventListener("click", () => {
      if (!window.confirm(`Supprimer ${run.run} ? UHB le fera, sans retour.`)) return;
      runAction(run.run, false);
    });
    row.append(drop);
    list.append(row);
  }
}

async function loadRuns() {
  try {
    const data = await api("/api/runs");
    PROJECTS.runs = data;
    renderRuns(data);
  } catch (error) {
    renderRuns({ ok: false, reason: error.message || String(error) });
  }
}

async function runAction(runId, keep) {
  try {
    const result = await api("/api/runs", {
      method: "POST",
      body: { action: "close", run: runId, keep },
    });
    if (result.ok === false) say("UHB a refuse : " + (result.reason || "?"), "warn");
    else say(keep ? `run archive : ${runId}` : `run supprime : ${runId}`, "ok");
  } catch (error) {
    say("action impossible : " + (error.message || error), "warn");
  }
  await loadRuns();
}

async function cleanRuns() {
  // Toujours en deux temps : la liste exacte d'abord, la suppression ensuite.
  let preview;
  try {
    preview = await api("/api/runs", { method: "POST", body: { action: "prune" } });
  } catch (error) {
    say("nettoyage impossible : " + (error.message || error), "warn");
    return;
  }
  if (!preview || preview.ok === false) {
    say("UHB injoignable : " + ((preview && preview.reason) || "?"), "warn");
    return;
  }
  const candidates = [...(preview.expired || []), ...(preview.over_quota || [])];
  if (!candidates.length) {
    say("rien a nettoyer", "ok");
    renderRuns(PROJECTS.runs);
    return;
  }
  const list = candidates.slice(0, 12).join("\n");
  const more = candidates.length > 12 ? `\n… et ${candidates.length - 12} autre(s)` : "";
  const ok = window.confirm(
    `${candidates.length} run(s) seraient supprimes (${preview.freed_bytes} o).\n\n${list}${more}\n\nContinuer ?`
  );
  if (!ok) return;
  try {
    const done = await api("/api/runs", { method: "POST", body: { action: "prune", apply: true } });
    say(`nettoyage : ${done.removed ?? 0} supprime(s)`, "ok");
  } catch (error) {
    say("nettoyage echoue : " + (error.message || error), "warn");
  }
  await loadRuns();
}

// --- explorateur de fichiers (P3-16c lot 1) -------------------------------- //
// Lecture seule, et AUCUNE règle de chemin ici : le serveur décide, avec la même porte
// que les outils (FileToolset). Un refus est affiché tel quel - le masquer ferait croire
// que le dossier n'existe pas, ce qu'un explorateur ne doit jamais laisser croire.
const FS = { path: "", root: "", parent: "", chain: [], entries: [], selected: null, selectedName: "" };

function fsIcon(entry) {
  if (entry.kind === "dir") return "📁";
  const ext = String(entry.ext || "").toLowerCase();
  if ([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"].includes(ext)) return "🖼";
  if (ext === ".pdf") return "📕";
  if ([".md", ".markdown", ".txt", ".log"].includes(ext)) return "📄";
  if ([".py", ".js", ".ts", ".json", ".toml", ".yaml", ".yml", ".sh", ".ps1", ".css", ".html"].includes(ext)) {
    return "⌨";
  }
  return "·";
}

function renderFsCrumbs(data) {
  const bar = $("files-crumbs");
  if (!bar) return;
  bar.textContent = "";
  const chain = data.chain && data.chain.length ? data.chain : [{ name: "workspace", path: data.workspace }];
  chain.forEach((step, index) => {
    if (index) bar.appendChild(el("span", "files-crumb-sep", "›"));
    const button = el("button", "files-crumb", step.name);
    button.type = "button";
    button.dataset.path = step.path;
    if (index === chain.length - 1) button.classList.add("current");
    button.addEventListener("click", () => loadFs(step.path));
    bar.appendChild(button);
  });
}

function renderFs(data) {
  FS.path = data.path || "";
  FS.root = data.workspace || "";
  FS.parent = data.parent || "";
  FS.chain = data.chain || [];
  FS.entries = data.entries || [];
  renderFsCrumbs(data);

  const list = $("files-list");
  if (!list) return;
  list.textContent = "";
  for (const entry of FS.entries) {
    const row = el("li", "files-row");
    const button = el("button", "files-name", `${fsIcon(entry)} ${entry.name}`);
    button.type = "button";
    button.dataset.path = entry.path || entry.name;
    button.dataset.kind = entry.kind;
    button.title = entry.kind === "dir" ? "ouvrir ce dossier" : "aperçu";
    button.addEventListener("click", () => {
      if (entry.kind === "dir") loadFs(button.dataset.path);
      else openFsPreview(button.dataset.path);
    });
    row.appendChild(button);
    if (entry.kind === "file") row.appendChild(el("span", "files-meta", humanSize(entry.size || 0)));
    list.appendChild(row);
  }
  const hint = $("files-hint");
  if (hint) {
    hint.textContent = FS.entries.length
      ? `${FS.entries.length} élément(s)${data.truncated ? ` sur ${data.total} — liste tronquée` : ""}`
      : "dossier vide";
  }
}

async function loadFs(path) {
  const hint = $("files-hint");
  if (hint) hint.textContent = "chargement…";
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  try {
    const data = await api(`/api/fs/list${query}`);
    renderFs(data);
  } catch (error) {
    if (hint) hint.textContent = `refusé : ${error.message || error}`;
    const list = $("files-list");
    if (list) list.textContent = "";
  }
}

async function openFsPreview(path) {
  const panel = $("files-preview");
  const body = $("files-preview-body");
  if (!panel || !body) return;
  panel.hidden = false;
  body.textContent = "chargement…";
  FS.selected = path;
  try {
    const data = await api(`/api/fs/preview?path=${encodeURIComponent(path)}`);
    FS.selectedName = data.name || path;
    $("files-preview-name").textContent = data.name || path;
    $("files-preview-meta").textContent = `${humanSize(data.bytes || 0)} · ${data.kind}`;
    body.textContent = "";
    if (data.kind === "image") {
      const img = el("img", "files-image");
      img.src = data.data_url;
      img.alt = data.name || "aperçu";
      body.appendChild(img);
    } else if (data.kind === "text") {
      const pre = el("pre", "files-text", data.text || "");
      body.appendChild(pre);
      if (data.truncated) body.appendChild(el("p", "files-meta", "aperçu tronqué : le fichier est plus long"));
    } else if (data.kind === "pdf") {
      if (data.readable) {
        body.appendChild(el("pre", "files-text", data.text || "(PDF sans texte extractible)"));
      } else {
        body.appendChild(el("p", "files-meta", `PDF non lu : ${data.reason}`));
      }
    } else {
      body.appendChild(el("p", "files-meta", data.reason || "aperçu non pris en charge"));
    }
  } catch (error) {
    body.textContent = `refusé : ${error.message || error}`;
    $("files-preview-meta").textContent = "";
  }
}

// --- git étendu (P3-16c lot 2) --------------------------------------------- //
// Journal, index et commit, bornés au dépôt actif : le serveur refuse tout autre chemin
// (403). Aucune fonction de push n'existe ici, et rien ne force les hooks.
const GIT = { page: 30, offset: 0, commits: [], selected: "", staged: 0 };

function gitStatusLetter(line) {
  const code = line.slice(0, 2);
  if (code === "??") return { letter: "?", staged: false, label: "non suivi" };
  const staged = code[0] !== " " && code[0] !== "?";
  const letters = { M: "modifié", A: "ajouté", D: "supprimé", R: "renommé", C: "copié", U: "conflit", T: "type" };
  return {
    letter: code.trim() || "?",
    staged,
    label: `${letters[code[0]] || "index"} / ${letters[code[1]] || "travail"}`,
  };
}

function gitChangedFiles(project) {
  const rows = [];
  for (const line of String(project.porcelain || "").split("\n")) {
    if (!line.trim() || line.startsWith("## ")) continue;
    let path = line.slice(3).trim();
    const arrow = path.indexOf(" -> ");
    if (arrow >= 0) path = path.slice(arrow + 4);
    rows.push({ path, ...gitStatusLetter(line) });
  }
  return rows;
}

function renderGitLog() {
  const list = $("git-log");
  if (!list) return;
  list.textContent = "";
  if (!GIT.commits.length) {
    list.appendChild(el("li", "git-empty", "Aucun commit lisible pour ce dépôt."));
  }
  for (const commit of GIT.commits) {
    const row = el("li", "git-entry");
    const sha = el("code", "git-sha", commit.short);
    sha.title = commit.sha;
    const subject = el("span", "git-subject", commit.subject || "(sans message)");
    const meta = el("span", "git-meta", `${commit.author || "?"} · ${String(commit.date || "").slice(0, 10)}`);
    row.append(sha, subject, meta);
    list.appendChild(row);
  }
  const hint = $("git-hint");
  if (hint) hint.textContent = `${GIT.commits.length} commit(s) affiché(s)`;
  const more = $("git-more");
  if (more) more.hidden = false;
}

async function loadGitLog(append = false) {
  const hint = $("git-hint");
  if (hint && !append) hint.textContent = "chargement…";
  const query = new URLSearchParams({ limit: String(GIT.page), offset: String(GIT.offset) });
  if (state.sessionId) query.set("session_id", state.sessionId);
  try {
    const data = await api(`/api/git/log?${query.toString()}`);
    GIT.commits = append ? [...GIT.commits, ...(data.commits || [])] : (data.commits || []);
    renderGitLog();
    const more = $("git-more");
    if (more) more.hidden = !data.has_more;
    if (hint) {
      hint.textContent = data.reason
        ? String(data.reason)
        : `${GIT.commits.length} commit(s) affiché(s)`;
    }
  } catch (error) {
    if (hint) hint.textContent = `journal refusé : ${error.message || error}`;
  }
}

function renderGitChanges(project) {
  const list = $("git-changes");
  const status = $("git-status");
  if (!list) return;
  list.textContent = "";
  const rows = gitChangedFiles(project);
  GIT.staged = rows.filter((row) => row.staged).length;
  if (!rows.length) {
    list.appendChild(el("li", "git-empty", "Rien à indexer : l'arbre de travail est propre."));
  }
  for (const row of rows) {
    const item = el("li", "git-change");
    const badge = el(
      "span",
      `git-flag ${row.staged ? "staged" : "work"}`,
      row.staged ? "index" : row.letter === "?" ? "non suivi" : "travail"
    );
    badge.title = row.label;
    const name = el("button", "git-path", row.path);
    name.type = "button";
    name.title = "voir le diff";
    name.addEventListener("click", () => openGitDiff(row.path, row.staged));
    const action = el("button", "ghost git-act", row.staged ? "Retirer" : "Indexer");
    action.type = "button";
    action.dataset.action = row.staged ? "unstage" : "stage";
    action.dataset.file = row.path;
    action.addEventListener("click", () => indexFile(row.path, row.staged ? "unstage" : "stage"));
    item.append(badge, name, action);
    list.appendChild(item);
  }
  if (status) {
    status.textContent = rows.length
      ? `${GIT.staged} indexé(s) · ${rows.length - GIT.staged} en travail`
      : "propre";
  }
  const button = $("git-commit");
  const message = $("git-message");
  if (button) button.disabled = !(GIT.staged > 0 && message && message.value.trim());
}

async function openGitDiff(file, staged) {
  const pre = $("git-diff");
  if (!pre) return;
  pre.hidden = false;
  pre.textContent = "chargement…";
  GIT.selected = file;
  const query = new URLSearchParams({ file, staged: staged ? "true" : "false" });
  if (state.sessionId) query.set("session_id", state.sessionId);
  try {
    const data = await api(`/api/git/diff?${query.toString()}`);
    pre.textContent = data.diff && data.diff.trim()
      ? data.diff + (data.truncated ? "\n…[diff tronqué]" : "")
      : "(aucune différence) — espaces de fin ignorés (--ignore-space-at-eol)";
  } catch (error) {
    pre.textContent = `diff refusé : ${error.message || error}`;
  }
}

async function indexFile(file, action) {
  const body = { files: [file] };
  if (state.sessionId) body.session_id = state.sessionId;
  try {
    await api(`/api/git/${action}`, { method: "POST", body: JSON.stringify(body) });
    await loadProjects();
    await loadGitLog();
  } catch (error) {
    const hint = $("git-status");
    if (hint) hint.textContent = `refusé : ${error.message || error}`;
  }
}

async function commitWork() {
  const message = $("git-message");
  const hint = $("git-commit-hint");
  const button = $("git-commit");
  if (!message || !hint) return;
  if (!message.value.trim()) {
    hint.textContent = "un message est obligatoire";
    return;
  }
  if (button) button.disabled = true;
  hint.textContent = "commit en cours…";
  const body = { message: message.value };
  if (state.sessionId) body.session_id = state.sessionId;
  try {
    const data = await api("/api/git/commit", { method: "POST", body: JSON.stringify(body) });
    hint.textContent = `commit ${data.short} créé`;
    message.value = "";
    await loadProjects();
    GIT.offset = 0;
    await loadGitLog();
  } catch (error) {
    hint.textContent = `refusé : ${error.message || error}`;
  }
}

// --- gestionnaire d'extensions (P3-16d / D-8) ----------------------------- //
// Trois familles, une vue. Ce que le serveur décide (activé/éteint, cassé, raison) est
// affiché tel quel : l'interface ne devine ni un état ni un diagnostic.
const EXTENSION_LABELS = { mcp: "Serveurs MCP", skills: "Skills", uhb: "Outils du second cerveau" };

function extensionRow(entry) {
  const row = el("div", "extension-row");
  row.dataset.kind = entry.kind;
  row.dataset.name = entry.name;
  row.dataset.state = entry.state || "";
  row.dataset.enabled = entry.enabled ? "1" : "0";

  const head = el("div", "extension-head");
  head.appendChild(el("strong", "extension-name", entry.name));
  head.appendChild(el("span", "task-flag" + (entry.enabled ? " on" : ""), entry.enabled ? "activée" : "éteinte"));
  if (entry.state === "broken" || entry.state === "failed") {
    head.appendChild(el("span", "extension-broken", "cassée"));
  }
  row.appendChild(head);

  const detail = el("div", "extension-detail");
  detail.textContent = entry.detail || "";
  row.appendChild(detail);
  if (entry.reason) {
    const reason = el("div", "extension-reason");
    reason.textContent = entry.reason;
    row.appendChild(reason);
  }

  const actions = el("div", "task-actions");
  const toggle = el("button", "ghost", entry.enabled ? "Désactiver" : "Activer");
  toggle.type = "button";
  toggle.dataset.action = "toggle";
  toggle.addEventListener("click", () => extensionAction(entry.kind, entry.name, "toggle", entry.enabled));
  const probe = el("button", "ghost", "Tester");
  probe.type = "button";
  probe.dataset.action = "test";
  probe.addEventListener("click", () => extensionAction(entry.kind, entry.name, "test", entry.enabled));
  actions.appendChild(toggle);
  actions.appendChild(probe);
  row.appendChild(actions);
  return row;
}

function renderExtensions(data) {
  const mode = $("extensions-mode");
  if (mode) {
    const counts = data.counts || {};
    const total = Object.values(counts).reduce((sum, item) => sum + (item.total || 0), 0);
    const enabled = Object.values(counts).reduce((sum, item) => sum + (item.enabled || 0), 0);
    const broken = Object.values(counts).reduce((sum, item) => sum + (item.broken || 0), 0);
    mode.textContent = `${enabled}/${total} active(s)${broken ? ` · ${broken} cassée(s)` : ""}`;
  }
  const box = $("extensions-groups");
  if (!box) return;
  box.textContent = "";
  for (const kind of data.kinds || ["mcp", "skills", "uhb"]) {
    const entries = (data.groups || {})[kind] || [];
    const group = el("section", "extension-group");
    group.dataset.kind = kind;
    const title = el("div", "extension-group-title");
    title.textContent = `${EXTENSION_LABELS[kind] || kind} (${entries.length})`;
    group.appendChild(title);
    if (!entries.length) {
      group.appendChild(el("div", "tasks-empty", "rien de configuré dans cette famille"));
    }
    for (const entry of entries) group.appendChild(extensionRow(entry));
    box.appendChild(group);
  }
}

async function loadExtensions(options = {}) {
  const hint = $("extensions-hint");
  const quiet = Boolean(options.keepMessage);
  // Un rechargement après action ne doit pas effacer la ligne qui vient de dire ce qui
  // s'est passé : mesuré, le message disparaissait aussitôt affiché.
  if (hint && !quiet) hint.textContent = "chargement…";
  try {
    const data = await api("/api/extensions");
    renderExtensions(data);
    if (hint && !quiet) hint.textContent = "";
  } catch (error) {
    if (hint) hint.textContent = `lecture impossible : ${error.message || error}`;
  }
}

async function extensionAction(kind, name, action, wasEnabled) {
  const hint = $("extensions-hint");
  try {
    if (action === "test") {
      if (hint) hint.textContent = `épreuve de ${name}…`;
      const data = await api(`/api/extensions/${encodeURIComponent(kind)}/${encodeURIComponent(name)}/test`, {
        method: "POST",
      });
      if (hint) hint.textContent = `${name} : ${data.ok ? "ok" : "refusé"} · ${data.detail || ""}`;
      return;
    }
    const data = await api(`/api/extensions/${encodeURIComponent(kind)}/${encodeURIComponent(name)}/enabled`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !wasEnabled }),
    });
    if (hint) {
      hint.textContent = `${name} ${data.enabled ? "activée" : "éteinte"} — effet au tour suivant, sans redémarrage`;
    }
    await loadExtensions({ keepMessage: true });
  } catch (error) {
    if (hint) hint.textContent = `refusé : ${error.message || error}`;
  }
}


// --- tâches planifiées (P3-16c lot 4) ------------------------------------- //
// La commande est une clé du catalogue lu depuis le serveur : le formulaire ne propose
// que ce que UH sait faire, et le nom ne devient jamais une ligne de commande.
let TASK_CATALOG = [];

function taskModeLabel(data) {
  const mode = String(data.mechanism_label || "—");
  return data.system_disabled ? `${mode} · déclencheurs désactivés` : mode;
}

const MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
                     "sept.", "oct.", "nov.", "déc."];

function frenchMoment(iso, horlogeServeur) {
  // « AAAA-MM-JJTHH:MM:SS » (heure locale du serveur) -> « Aujourd'hui 03:25 »,
  // « Hier 22:10 » ou « 21 nov. 03:25 ». L'ISO brut n'apparaît plus dans l'interface :
  // il reste lisible par une machine, pas par un opérateur. Un format inconnu est rendu
  // tel quel plutôt que masqué - on ne cache pas ce qu'on n'a pas su lire.
  const texte = String(iso || "").trim();
  if (!texte) return "";
  const m = texte.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return texte;
  const [, annee, mois, jour, heure, minute] = m;
  const moment = new Date(Number(annee), Number(mois) - 1, Number(jour), Number(heure), Number(minute));
  // La référence est l'horloge du SERVEUR, celle qui a écrit les horodatages : elle arrive
  // dans le même format naïf, donc sur le même calendrier. Comparer au fuseau du poste
  // faisait basculer un passage de 00:30 en « Hier » dès qu'on consultait depuis un autre
  // fuseau horaire. Le navigateur ne sert plus que de repli quand le serveur n'envoie rien.
  const horloge = String(horlogeServeur || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  const maintenant = horloge
    ? new Date(Number(horloge[1]), Number(horloge[2]) - 1, Number(horloge[3]),
               Number(horloge[4]), Number(horloge[5]))
    : new Date();
  const cle = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const hhmm = `${heure}:${minute}`;
  if (cle(moment) === cle(maintenant)) return `Aujourd'hui ${hhmm}`;
  const hier = new Date(maintenant.getFullYear(), maintenant.getMonth(), maintenant.getDate() - 1);
  if (cle(moment) === cle(hier)) return `Hier ${hhmm}`;
  const suffixe = moment.getFullYear() === maintenant.getFullYear() ? "" : ` ${annee}`;
  return `${Number(jour)} ${MOIS_COURTS[Number(mois) - 1]}${suffixe} ${hhmm}`;
}

function renderTasks(data) {
  const mode = $("tasks-mode");
  if (mode) mode.textContent = taskModeLabel(data);

  // L'horloge du serveur arrive avec la liste : « Aujourd'hui » et « Hier » se calculent
  // sur SON calendrier, celui qui a écrit les horodatages, jamais sur le fuseau du poste.
  const horloge = data.now || null;

  const select = $("task-command");
  if (select) {
    const previous = select.value;
    select.textContent = "";
    TASK_CATALOG = data.catalog || [];
    for (const entry of TASK_CATALOG) {
      const option = document.createElement("option");
      option.value = entry.key;
      option.textContent = entry.label;
      select.appendChild(option);
    }
    if (previous) select.value = previous;
  }

  const catalog = $("tasks-catalog");
  if (catalog) {
    catalog.textContent = "";
    for (const entry of TASK_CATALOG) {
      const line = el("div", "tasks-catalog-line");
      line.appendChild(el("code", "tasks-key", entry.key));
      line.appendChild(el("span", "tasks-desc", entry.description));
      if (entry.mutating) line.appendChild(el("span", "tasks-flag", "écrit"));
      catalog.appendChild(line);
    }
  }

  const list = $("tasks-list");
  if (!list) return;
  list.textContent = "";
  const tasks = data.tasks || [];
  if (!tasks.length) {
    list.appendChild(el("li", "tasks-empty", "Aucune tâche. Choisis une commande du catalogue ci-dessus."));
    return;
  }
  for (const task of tasks) {
    const row = el("li", "task-row");
    row.dataset.taskId = task.id;
    row.dataset.enabled = task.enabled ? "1" : "0";

    const head = el("div", "task-head");
    head.appendChild(el("strong", "task-name", task.name));
    head.appendChild(el("span", "task-flag" + (task.enabled ? " on" : ""), task.enabled ? "activée" : "désactivée"));
    head.appendChild(el("span", "task-every", `toutes les ${task.every_minutes} min`));
    row.appendChild(head);

    const meta = el("div", "task-meta");
    const commandLabel = (TASK_CATALOG.find((item) => item.key === task.command) || {}).label || task.command;
    meta.textContent = `${commandLabel} · ${task.mechanism || "aucun déclencheur"}`;
    row.appendChild(meta);

    const last = el("div", "task-last");
    last.dataset.role = "last";
    last.textContent = task.last_run
      ? `dernier passage : ${frenchMoment(task.last_run, horloge)} (${task.last_status || "—"})`
      : "jamais lancée";
    if (task.last_detail) last.title = task.last_detail;
    row.appendChild(last);

    // D-6 : la prochaine exécution est calculée côté serveur (dernier passage + cadence).
    // Une tâche éteinte n'en a pas, et le dit plutôt que d'afficher une heure trompeuse.
    const next = el("div", "task-next");
    next.dataset.role = "next";
    next.textContent = task.next_run
      ? `prochaine : ${frenchMoment(task.next_run, horloge)}`
      : `prochaine : aucune (${task.next_reason || "—"})`;
    row.appendChild(next);

    const actions = el("div", "task-actions");
    const toggle = el("button", "ghost", task.enabled ? "Désactiver" : "Activer");
    toggle.type = "button";
    toggle.dataset.action = "toggle";
    toggle.addEventListener("click", () => taskAction(task.id, "toggle"));
    const run = el("button", "ghost", "Lancer maintenant");
    run.type = "button";
    run.dataset.action = "run";
    run.addEventListener("click", () => taskAction(task.id, "run"));
    const kill = el("button", "ghost danger", "Supprimer");
    kill.type = "button";
    kill.dataset.action = "delete";
    kill.addEventListener("click", () => taskAction(task.id, "delete"));
    actions.append(toggle, run, kill);
    row.appendChild(actions);
    list.appendChild(row);
  }
}

async function loadSchedules(options = {}) {
  const hint = $("tasks-hint");
  const quiet = Boolean(options.keepMessage);
  // Un rechargement silencieux laisse le message de l'action visible : c'est la seule
  // trace de ce qui vient de se passer (mesuré : « ok » disparaissait aussitôt).
  if (hint && !quiet) hint.textContent = "chargement…";
  try {
    const data = await api("/api/schedules");
    renderTasks(data);
    if (hint && !quiet) hint.textContent = "";
  } catch (error) {
    if (hint) hint.textContent = `lecture impossible : ${error.message || error}`;
  }
}

async function createTask(event) {
  if (event) event.preventDefault();
  const hint = $("tasks-hint");
  const name = ($("task-name")?.value || "").trim();
  const command = $("task-command")?.value || "";
  const every = Number($("task-every")?.value || 0);
  if (!name) {
    if (hint) hint.textContent = "un nom est obligatoire";
    return;
  }
  try {
    const data = await api("/api/schedules", {
      method: "POST",
      body: JSON.stringify({ name, command, every_minutes: every }),
    });
    if (hint) {
      hint.textContent = `tâche créée · déclencheur : ${data.trigger?.detail || "aucun"}`;
    }
    if ($("task-name")) $("task-name").value = "";
    await loadSchedules({ keepMessage: true });
  } catch (error) {
    if (hint) hint.textContent = `refusé : ${error.message || error}`;
  }
}

async function taskAction(taskId, action) {
  const hint = $("tasks-hint");
  try {
    if (action === "delete") {
      const row = document.querySelector(`.task-row[data-task-id="${taskId}"]`);
      const name = row ? row.querySelector(".task-name")?.textContent : taskId;
      if (!window.confirm(`Supprimer la tâche « ${name} » et son déclencheur ?`)) return;
      await api(`/api/schedules/${encodeURIComponent(taskId)}`, { method: "DELETE" });
      if (hint) hint.textContent = `tâche supprimée : ${taskId}`;
    } else if (action === "run") {
      if (hint) hint.textContent = "exécution en cours…";
      const data = await api(`/api/schedules/${encodeURIComponent(taskId)}/run`, { method: "POST" });
      if (hint) {
        // Le détail complet reste en infobulle sur la ligne (task-last) : dans l'en-tête
        // il poussait le formulaire sous une sortie de commande de plusieurs lignes.
        hint.textContent = `${taskId} : ${data.ok ? "ok" : "erreur"}`;
      }
    } else {
      const data = await api(`/api/schedules/${encodeURIComponent(taskId)}/toggle`, { method: "POST" });
      if (hint) hint.textContent = `tâche ${data.task?.enabled ? "activée" : "désactivée"}`;
    }
    await loadSchedules({ keepMessage: true });
  } catch (error) {
    if (hint) hint.textContent = `refusé : ${error.message || error}`;
  }
}


// Lecture seule, et l'interface ne propose rien d'autre : pas de bouton d'arrêt, pas de
// redémarrage. Ce que la vue montre vient de /api/infra, qui ne fait que lire.
function infraUptime(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  if (total < 60) return `${total} s`;
  if (total < 3600) return `${Math.floor(total / 60)} min`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${hours} h ${String(minutes).padStart(2, "0")} min`;
}

function renderInfra(data) {
  const alerts = $("infra-alerts");
  if (alerts) {
    alerts.textContent = "";
    for (const alert of data.alerts || []) {
      const row = el("div", `infra-alert ${alert.level}`);
      row.dataset.level = alert.level;
      row.appendChild(el("span", "infra-dot"));
      row.appendChild(el("strong", null, alert.label));
      row.appendChild(el("span", "infra-detail", alert.detail || ""));
      alerts.appendChild(row);
    }
  }

  const services = $("infra-services");
  if (services) {
    services.textContent = "";
    for (const service of data.services || []) {
      const up = service.state === "up";
      const row = el("li", `infra-service ${up ? "up" : "down"}`);
      row.dataset.service = service.name;
      row.dataset.state = service.state;
      row.appendChild(el("span", `infra-dot ${up ? "ok" : "ko"}`));
      row.appendChild(el("strong", null, service.name));
      row.appendChild(el("span", "infra-detail", service.detail || ""));
      if (service.uptime_s) row.appendChild(el("span", "infra-meta", `uptime ${infraUptime(service.uptime_s)}`));
      if (!up && service.reason) row.appendChild(el("span", "infra-reason", service.reason));
      services.appendChild(row);
    }
  }

  const disk = $("infra-disk");
  if (disk) {
    disk.textContent = "";
    disk.appendChild(el("div", "panel-label", "Tailles sur disque"));
    for (const [label, value] of [
      ["~/.uh", data.disk?.home],
      ["~/.uhb/runs", data.disk?.runs],
    ]) {
      if (!value) continue;
      const line = el("div", "infra-line");
      line.appendChild(el("span", "infra-name", label));
      line.appendChild(
        el(
          "span",
          "infra-value",
          value.exists
            ? `${humanBytes(value.bytes)} · ${value.files} fichier(s)${value.complete ? "" : " · minimum"}`
            : "absent"
        )
      );
      if (value.reason) line.appendChild(el("span", "infra-reason", value.reason));
      disk.appendChild(line);
    }
    const free = data.disk?.free || {};
    if (typeof free.free_bytes === "number") {
      const line = el("div", "infra-line");
      line.appendChild(el("span", "infra-name", "disque libre"));
      line.appendChild(
        el("span", "infra-value", `${humanBytes(free.free_bytes)} sur ${humanBytes(free.total_bytes)} (${free.used_percent} % utilisé)`)
      );
      disk.appendChild(line);
    }
    if (data.vram) {
      const line = el("div", "infra-line");
      line.appendChild(el("span", "infra-name", "VRAM"));
      line.appendChild(
        el("span", "infra-value", `${data.vram.used_mb} / ${data.vram.total_mb} Mo (${data.vram.percent} %) · ${data.vram.name || "GPU"}`)
      );
      disk.appendChild(line);
    }
  }

  const install = $("infra-install");
  if (install) {
    install.textContent = "";
    install.appendChild(el("div", "panel-label", "Code servi"));
    const info = data.install || {};
    for (const [label, value] of [
      ["origine", info.origin || "?"],
      ["version", data.version || "?"],
      ["commit", info.commit || "(aucun : paquet installé)"],
      ["modifié", info.dirty ? "oui — les fichiers suivis diffèrent du commit" : "non"],
      ["python", info.python || ""],
    ]) {
      if (!value) continue;
      const line = el("div", "infra-line");
      line.appendChild(el("span", "infra-name", label));
      line.appendChild(el("span", "infra-value", String(value)));
      install.appendChild(line);
    }
  }

  const logs = $("infra-logs");
  if (logs) {
    logs.textContent = "";
    logs.appendChild(el("div", "panel-label", "Fins de journal (masquées)"));
    for (const log of data.logs || []) {
      const details = document.createElement("details");
      details.className = "infra-log";
      details.dataset.name = log.name;
      const summary = document.createElement("summary");
      summary.textContent = log.exists
        ? `${log.name} · ${log.returned} ligne(s) · ${humanBytes(log.bytes)}`
        : `${log.name} · absent`;
      details.appendChild(summary);
      const pre = el("pre", "infra-log-body");
      pre.textContent = log.exists
        ? (log.lines || []).join("\n") || "(fichier vide)"
        : String(log.reason || "absent");
      details.appendChild(pre);
      logs.appendChild(details);
    }
  }

  const hint = $("infra-hint");
  if (hint) {
    const bad = (data.alerts || []).filter((a) => a.level !== "ok").length;
    hint.textContent = bad ? `${bad} alerte(s) · ${data.at}` : `tout va bien · ${data.at}`;
  }
}

async function loadInfra() {
  const hint = $("infra-hint");
  if (hint) hint.textContent = "chargement…";
  try {
    const data = await api("/api/infra?log_lines=20");
    renderInfra(data);
  } catch (error) {
    if (hint) hint.textContent = `lecture impossible : ${error.message || error}`;
  }
}

// Un tour = un message de l'utilisateur, exactement ce que Télémétrie compte. Le numero
// vient du serveur au chargement, et du compteur local pour le tour qu'on vient d'envoyer.
function turnBadge(box, turn) {
  if (!box || !turn) return;
  const badge = el("span", "turn-badge", `Tour #${turn}`);
  badge.title = `tour ${turn} de cette conversation · compté comme « ${turn} tour(s) » dans Télémétrie`;
  box.classList.add("turn-start");
  box.prepend(badge);
  return badge;
}

async function attachSelectedFile() {
  const path = FS.selected;
  if (!path) return;
  const button = $("files-attach");
  if (button) button.disabled = true;
  try {
    // Les octets repartent par l'upload existant : un seul chemin d'écriture, celui de
    // la pièce jointe, avec ses limites et son nettoyage.
    const response = await fetch(`/api/fs/raw?path=${encodeURIComponent(path)}`, {
      credentials: "same-origin",
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || httpErrorText(response.status));
    }
    const blob = await response.blob();
    // Le nom vient du serveur : aucun séparateur de chemin n'est deviné ici.
    const name = FS.selectedName || "fichier";
    await uploadFile(new File([blob], name, { type: blob.type || "application/octet-stream" }));
    const hint = $("files-hint");
    if (hint) hint.textContent = `« ${name} » joint au prochain tour`;
  } catch (error) {
    const hint = $("files-hint");
    if (hint) hint.textContent = `pièce jointe refusée : ${error.message || error}`;
  } finally {
    if (button) button.disabled = false;
  }
}
