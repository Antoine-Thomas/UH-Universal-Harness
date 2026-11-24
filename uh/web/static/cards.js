// UH web — cartes : résultats web, galerie d'images, vidéo YouTube.
//
// Module séparé pour deux raisons : app.js pèse 11 000 lignes et câble la page
// au chargement, alors que ces cartes sont des fonctions pures sur un conteneur.
// Séparées, elles s'importent seules dans un test de navigateur — c'est ainsi
// que l'interception réseau prouve qu'aucune image ne part vers un tiers.
//
// Rien n'est deviné ici : le serveur envoie la charge utile déjà mesurée (`/web`
// et `/web image` la produisent, `/api/media/video` pour YouTube). Un résultat
// sans URL n'a pas de carte, une image sans licence affiche « licence
// inconnue », et aucune image n'est demandée directement à un tiers : la CSP
// n'autorise que 'self' et data:, donc tout passe par /api/media/thumb.

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

export function thubMedia(url, session = "") {
  const query = new URLSearchParams({ url: String(url || ""), session });
  return `/api/media/thumb?${query.toString()}`;
}

function externalLink(url, className, text) {
  const link = node("a", className, text);
  link.href = String(url || "");
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function cardNote(container, text) {
  container.appendChild(node("p", "card-note", text));
}

function webCards(payload) {
  const wrap = node("div", "cards web-cards");
  const results = Array.isArray(payload.results) ? payload.results : [];
  for (const item of results) {
    if (!item || !item.url) continue;
    const card = node("div", "web-card");
    card.appendChild(externalLink(item.url, "card-title", item.title || item.url));
    const meta = node("p", "card-meta");
    meta.appendChild(node("span", "card-domain", item.domain || ""));
    card.appendChild(meta);
    if (item.snippet) card.appendChild(node("p", "card-snippet", item.snippet));
    wrap.appendChild(card);
  }
  if (payload.engine) cardNote(wrap, `moteur : ${payload.engine}`);
  for (const [engine, reason] of Object.entries(payload.failed || {})) {
    cardNote(wrap, `moteur en échec — ${engine} : ${reason}`);
  }
  return wrap;
}

function imageGallery(payload, session) {
  const wrap = node("div", "gallery");
  const results = Array.isArray(payload.results) ? payload.results : [];
  for (const item of results) {
    if (!item || !item.thumb) continue;
    const figure = node("figure", "gallery-item");
    const img = node("img", "gallery-img");
    img.src = thubMedia(item.thumb, session);
    img.alt = item.title || "image";
    img.loading = "lazy";
    // Une vignette refusée (hôte hors liste, 429, format) n'est jamais une image
    // cassée muette : un état visible, et la raison reste dans le texte.
    img.addEventListener("error", () => figure.classList.add("unavailable"));
    figure.appendChild(img);
    const caption = node("figcaption");
    caption.appendChild(node("p", "card-title", item.title || "sans titre"));
    caption.appendChild(
      node(
        "p",
        "card-licence",
        `licence : ${item.licence || "inconnue"} — auteur : ${item.author || "inconnu"}`
      )
    );
    const meta = node("p", "card-meta");
    if (item.page) {
      meta.appendChild(externalLink(item.page, "card-source", "page source"));
      meta.appendChild(document.createTextNode(" "));
    }
    meta.appendChild(node("span", "card-domain", item.domain || ""));
    if (item.provider) {
      meta.appendChild(document.createTextNode(" · "));
      meta.appendChild(node("span", "card-provider", item.provider));
    }
    caption.appendChild(meta);
    figure.appendChild(caption);
    wrap.appendChild(figure);
  }
  for (const [engine, reason] of Object.entries(payload.failed || {})) {
    cardNote(wrap, `moteur en échec — ${engine} : ${reason}`);
  }
  return wrap;
}

function videoCard(payload, session) {
  const card = node("div", "video-card");
  if (payload.thumb) {
    const img = node("img", "video-thumb");
    img.src = thubMedia(payload.thumb, session);
    img.alt = payload.title || "vidéo";
    img.loading = "lazy";
    card.appendChild(img);
  }
  const body = node("div", "video-body");
  body.appendChild(externalLink(payload.page, "card-title", payload.title || "vidéo"));
  if (payload.channel) body.appendChild(node("p", "card-meta", payload.channel));
  if (payload.description) {
    body.appendChild(node("p", "card-snippet", payload.description));
  } else {
    // La réponse oEmbed n'a pas de description : on le dit, on ne fait pas semblant.
    cardNote(body, "description indisponible sans clé de l'API YouTube Data");
  }
  body.appendChild(externalLink(payload.page, "notice-action continuation", "Ouvrir sur YouTube"));
  card.appendChild(body);
  return card;
}

/** Dessine la charge utile dans `container` ; rend l'élément ajouté, ou null. */
export function drawCards(container, payload, { session = "" } = {}) {
  if (!payload || !container) return null;
  const kind = String(payload.kind || "");
  let element = null;
  if (kind === "web") element = webCards(payload);
  else if (kind === "images") element = imageGallery(payload, session);
  else if (kind === "video") element = videoCard(payload, session);
  if (element) container.appendChild(element);
  return element;
}

// Une adresse YouTube dans une réponse devient une carte, une seule fois, et
// seulement si le serveur sait la décrire (oEmbed sans clé).
const YOUTUBE =
  /https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?[^\s]+|shorts\/[^\s]+|live\/[^\s]+)|youtu\.be\/[^\s]+)/i;

export function maybeVideoCard(box, text, { session = "" } = {}) {
  if (!box || box.dataset.videoAsked === "1") return;
  const match = YOUTUBE.exec(String(text || ""));
  if (!match) return;
  box.dataset.videoAsked = "1";
  const url = match[0].replace(/[.,;:!?)\]]+$/, "");
  const query = new URLSearchParams({ url, session });
  fetch(`/api/media/video?${query.toString()}`, { credentials: "same-origin" })
    .then((response) => (response.ok ? response.json() : null))
    .then((card) => {
      if (card) drawCards(box, card, { session });
    })
    .catch(() => {});
}
