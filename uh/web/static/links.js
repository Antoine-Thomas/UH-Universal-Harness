// Cartes d'aperçu de lien (G8 phase 4).
//
// Deux règles, et c'est la page qui les porte ici :
//
//   * une URL écrite par le modèle n'est **pas** récupérée sans clic : la carte
//     affiche le domaine et un bouton « Aperçu », et rien d'autre ne part ;
//   * rien de la page distante n'entre en HTML : titre, description et notes
//     passent par `textContent`, jamais par `innerHTML`. L'image, elle, est
//     demandée à UH (`/api/link/image`), donc la CSP reste `img-src 'self' data:`.
//
// La provenance vient de la trame de réponse (calculée par le serveur). Absente,
// elle vaut « model » : le refus est par défaut, ici comme là-bas.

const PREVIEW = "/api/link/preview";
const IMAGE = "/api/link/image";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function getJSON(path) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const message = String(detail.detail || detail.error || `HTTP ${response.status}`);
    throw new Error(message);
  }
  return response.json();
}

function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function host(text, className, value) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = value === undefined ? text : `${text} ${value}`;
  return span;
}

/**
 * Une carte par lien. Aucun appel réseau au moment du rendu pour une URL de
 * modèle ; `preview` n'est lancé qu'au clic, ou d'office pour une provenance
 * `tool` / `operator` (le résultat d'un outil ou la saisie de l'opérateur).
 */
export function linkCards(box, links, { session = "" } = {}) {
  if (!Array.isArray(links) || !links.length) return;
  const wrap = node("div", "link-cards");
  for (const link of links) {
    if (!link || typeof link.url !== "string" || !/^https?:\/\//i.test(link.url)) continue;
    wrap.appendChild(linkCard(link, session));
  }
  if (wrap.childElementCount) box.appendChild(wrap);
}

function linkCard(link, session) {
  const origin = String(link.origin || "model");
  const card = node("div", "link-card");
  card.dataset.origin = origin;

  const head = node("div", "link-card-head");
  head.appendChild(node("span", "link-card-domain", domainOf(link.url)));
  const open = document.createElement("a");
  open.className = "link-card-open";
  open.href = link.url;
  open.target = "_blank";
  open.rel = "noopener noreferrer";
  open.textContent = "Ouvrir";
  head.appendChild(open);
  card.appendChild(head);

  const body = node("div", "link-card-body");
  card.appendChild(body);

  const run = () => preview(link, body, session);

  if (origin === "model") {
    const button = node("button", "link-card-preview", "Aperçu");
    button.type = "button";
    button.addEventListener("click", () => {
      button.disabled = true;
      button.textContent = "Aperçu…";
      run();
    });
    card.appendChild(button);
  } else {
    // Provenance prouvée côté serveur : la carte peut demander tout de suite.
    run();
  }
  return card;
}

async function preview(link, body, session) {
  const query = new URLSearchParams({ url: link.url, session, origin: link.origin || "click" });
  let data;
  try {
    data = await getJSON(`${PREVIEW}?${query}`);
  } catch (error) {
    body.appendChild(
      node("p", "link-card-error", `Aperçu indisponible : ${error.message || "refus du serveur"}.`)
    );
    return;
  }
  if (data.title) body.appendChild(node("p", "link-card-title", data.title));
  if (data.description) body.appendChild(node("p", "link-card-desc", data.description));

  if (data.image) {
    const figure = node("figure", "link-card-figure");
    const img = document.createElement("img");
    img.className = "link-card-image";
    img.loading = "lazy";
    img.alt = data.title ? `Aperçu : ${data.title}` : "Image de l'aperçu";
    const imageQuery = new URLSearchParams({ url: data.image, session });
    img.src = `${IMAGE}?${imageQuery}`;
    figure.appendChild(img);
    body.appendChild(figure);
  }

  const facts = node("div", "link-card-facts");
  if (data.host) facts.appendChild(host("Domaine", "link-card-fact", data.host));
  if (data.redirects) {
    facts.appendChild(host("Redirigé depuis", "link-card-fact", String(data.redirects)));
  }
  if (data.robots && data.robots !== "allowed") {
    facts.appendChild(host("robots.txt", "link-card-fact", data.robots));
  }
  for (const note of data.notes || []) facts.appendChild(host(note, "link-card-fact"));
  if (facts.childElementCount) body.appendChild(facts);
}
