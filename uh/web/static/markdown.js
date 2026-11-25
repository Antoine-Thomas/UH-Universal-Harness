// UH web — minimal Markdown renderer. No dependencies, no build step.
//
// Safety model: the source text is HTML-escaped BEFORE any markup is
// recognised, so raw HTML in a model answer is displayed, never executed.
// The only tags in the output are the ones emitted here, and link hrefs are
// limited to http/https/mailto — escaping has already neutralised quotes,
// so a URL cannot break out of its attribute.

const ESCAPE = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (ch) => ESCAPE[ch]);
}

// A tab named in a model answer becomes the link that opens it: the answer then
// guides to the interface instead of describing it. Applied to text, never to a
// code span (protected before this runs) nor inside an existing link.
const TAB_LINKS = [
  [/\bConnexions\b/g, "/?panel=conn"],
  [/\bClés? API\b/g, "/?panel=keys"],
  [/\bIA locale\b/g, "/?panel=local"],
  [/\bDonnées\b/g, "/?panel=data"],
  [/\bProviders\b/g, "/?panel=providers"],
  [/\bModèles\b/g, "/?panel=models"],
  [/\bMémoire\b/g, "/?panel=memory"],
  [/\bDécision\b/g, "/?panel=decision"],
  [/\bTélémétrie\b/g, "/?panel=telemetry"],
  [/\bSecond brain\b/g, "/?panel=uhb"],
];

// A short answer, a search result: /web renders its results as plain text, so a
// bare address has to become clickable — an address one cannot open is useless.
const BARE_URL = /https?:\/\/\S+/gi;

// Punctuation that belongs to the sentence, not to the address: in "(see
// https://example.fr/a)" the closing parenthesis closes the sentence, unless the
// address opened one itself (Wikipedia links contain parentheses). Quotes and
// angle brackets arrive escaped, hence the entities in the list.
const TRAILING_PUNCTUATION = /[.,;:!?'"»”’]/;
const TRAILING_ENTITY = /&(?:quot|#39|gt|lt);$/;
const CLOSING = { ")": "(", "]": "[", "}": "{" };

function occurrences(text, character) {
  return text.split(character).length - 1;
}

// Split what belongs to the address from the punctuation that follows it.
function splitTrailing(url) {
  let head = url;
  for (;;) {
    // Entités d'abord, et un caractère à la fois : « "…x". » finit par un point
    // qui cache l'entité, et une entité finit elle-même par un point-virgule.
    const entity = head.match(TRAILING_ENTITY);
    if (entity) {
      head = head.slice(0, -entity[0].length);
      continue;
    }
    const last = head.slice(-1);
    if (TRAILING_PUNCTUATION.test(last)) {
      head = head.slice(0, -1);
      continue;
    }
    if (last in CLOSING && occurrences(head, CLOSING[last]) < occurrences(head, last)) {
      head = head.slice(0, -1);
      continue;
    }
    return [head, url.slice(head.length)];
  }
}

// Fail-closed: only http and https become a link, never javascript: or data: —
// and those cannot even take this shape, since BARE_URL demands the scheme.
function autolink(raw) {
  const [url, tail] = splitTrailing(raw);
  if (!/^https?:\/\/./i.test(url)) return raw;
  return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>${tail}`;
}

function linkTabs(escaped) {
  // Existing anchors are kept whole (nesting one inside another is invalid HTML),
  // and a bare URL is tokenised so that "example.com/Connexions" stays a URL.
  return escaped
    .split(/(<a\s[^>]*>[\s\S]*?<\/a>)/)
    .map((part, index) => {
      if (index % 2 === 1) return part;
      const urls = [];
      let out = part.replace(BARE_URL, (match) => {
        urls.push(match);
        return `\u0001${urls.length - 1}\u0001`;
      });
      for (const [pattern, href] of TAB_LINKS) {
        out = out.replace(pattern, (match) => `<a href="${href}">${match}</a>`);
      }
      return out.replace(/\u0001(\d+)\u0001/g, (_match, i) => autolink(urls[Number(i)]));
    })
    .join("");
}

// Une image Markdown ne devient JAMAIS une balise <img> : la politique de
// sécurité n'autorise que « self » et data:, et une image distante doit passer
// par un proxy. Et si la cible est un artefact que le message porte déjà (même
// nom de fichier), la syntaxe disparaît : c'est le « ![cochon](cochon.png) »
// resté affiché au-dessus de l'image. Sinon elle devient un lien texte.
const MARKDOWN_IMAGE = /!\[([^\]\n]*)\]\(([^)\s]+)\)/g;

function baseName(target) {
  const cleaned = target.split("#")[0].split("?")[0];
  return cleaned.split(/[\\/]/).pop();
}

function imageLink(alt, target, artifacts) {
  if (artifacts.has(baseName(target))) return "";
  const label = alt.trim() || baseName(target);
  if (/^https?:\/\//i.test(target)) {
    return `<a href="${target}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  }
  // Un chemin de la même origine, ou un simple nom de fichier : le lien reste
  // chez UH. Rien d'autre — ni data:, ni javascript:, ni file: — n'est lié : la
  // syntaxe reste alors affichée telle quelle, sans lien.
  if (/^\/(?!\/)/.test(target) || /^[\w.\-]+(\/[\w.\-]+)*$/.test(target)) {
    return `<a href="${target}">${label}</a>`;
  }
  return null;
}

// Inline formatting, applied to ALREADY escaped text.
function renderInline(escaped, artifacts = new Set()) {
  // Inline code first: its content is protected from further formatting.
  const codes = [];
  let text = escaped.replace(/`([^`\n]+)`/g, (_match, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  // Images avant les liens : « ![alt](cible) » contient « [alt](cible) ».
  text = text.replace(
    MARKDOWN_IMAGE,
    (match, alt, target) => imageLink(alt, target, artifacts) ?? match,
  );
  // Links: [label](url) - http/https/mailto open a new tab, a same-origin path
  // (/?panel=conn) opens in place: that is what a deep link from the model is.
  text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
    if (/^(https?:\/\/|mailto:)/i.test(url)) {
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }
    if (/^\/(?!\/)/.test(url)) return `<a href="${url}">${label}</a>`;
    return match;
  });
  text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  text = text.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  text = linkTabs(text);
  return text.replace(/\u0000(\d+)\u0000/g, (_match, i) => `<code>${codes[Number(i)]}</code>`);
}

// Block structure: fenced code, headings, lists, quotes, hr, paragraphs.
export function renderMarkdown(source, options = {}) {
  const lines = String(source ?? "").replace(/\r\n/g, "\n").split("\n");
  // Les artefacts que le message porte déjà : une image Markdown qui les nomme
  // disparaît, puisqu'elle est déjà dessinée juste en dessous.
  const artifacts = new Set((options.artifacts || []).map((name) => String(name)));
  const html = [];
  let paragraph = [];
  let list = null; // { ordered: bool, items: string[] }
  let quote = [];
  let inCode = false;
  let codeLines = [];

  // --- tableaux (G15) ------------------------------------------------------- //
  // Un tableau se reconnaît sur DEUX lignes (en-tête, puis séparation) : la boucle
  // ligne à ligne ne peut donc pas le voir. Une pré-passe le remplace par une
  // ligne-sentinel et garde le HTML de côté. Elle suit l'état des clôtures de code
  // (` et ~), sinon une barre verticale dans un bloc de code deviendrait un tableau.
  // Le sentinel contient un octet nul : aucun texte échappé ne peut le forger, et
  // un sentinel dont la clé est inconnue retombe en texte ordinaire.
  const tableFragments = new Map();
  const scanned = [];
  const alignmentOf = (cell) => {
    const value = String(cell).trim();
    if (value.startsWith(":") && value.endsWith(":")) return "md-align-center";
    if (value.endsWith(":")) return "md-align-right";
    if (value.startsWith(":")) return "md-align-left";
    return "";
  };
  const cellsOf = (row) => {
    let text = String(row).trim();
    if (text.startsWith("|")) text = text.slice(1);
    if (text.endsWith("|")) text = text.slice(0, -1);
    return text.split("|").map((cell) => cell.trim());
  };
  // GFM ne fixe pas de minimum de tirets : `:--` est une séparation valide.
  const isSeparator = (row) =>
    /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(String(row));
  let inFence = false;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      scanned.push(line);
      continue;
    }
    const next = lines[index + 1];
    if (!inFence && line.includes("|") && next !== undefined && isSeparator(next)) {
      const header = cellsOf(line);
      const aligns = cellsOf(next).map(alignmentOf);
      const rows = [];
      let cursor = index + 2;
      while (cursor < lines.length && lines[cursor].includes("|") && lines[cursor].trim()) {
        rows.push(cellsOf(lines[cursor]));
        cursor += 1;
      }
      const cell = (tag, value, position) => {
        const align = aligns[position] ? ` class="${aligns[position]}"` : "";
        return `<${tag}${align}>${renderInline(escapeHtml(value), artifacts)}</${tag}>`;
      };
      const head = `<thead><tr>${header.map((value, i) => cell("th", value, i)).join("")}</tr></thead>`;
      const body = rows.length
        ? `<tbody>${rows
            .map((row) => `<tr>${row.map((value, i) => cell("td", value, i)).join("")}</tr>`)
            .join("")}</tbody>`
        : "";
      const key = `\u0000T${tableFragments.size}\u0000`;
      tableFragments.set(key, `<table>${head}${body}</table>`);
      scanned.push(key);
      index = cursor - 1;
      continue;
    }
    scanned.push(line);
  }

  const flushParagraph = () => {
    if (paragraph.length) {
      const body = renderInline(paragraph.join(" "), artifacts);
      // Une image retirée (artefact déjà dessiné) laisse un paragraphe vide : il
      // ne s'affiche pas.
      if (body) html.push(`<p>${body}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      const tag = list.ordered ? "ol" : "ul";
      html.push(`<${tag}>${list.items.map((item) => `<li>${renderInline(item, artifacts)}</li>`).join("")}</${tag}>`);
      list = null;
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      html.push(`<blockquote><p>${renderInline(quote.join(" "), artifacts)}</p></blockquote>`);
      quote = [];
    }
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
    flushQuote();
  };
  const emitCode = () => {
    html.push(`<pre><code>${codeLines.join("\n")}</code></pre>`);
    codeLines = [];
  };

  for (const line of scanned) {
    if (inCode) {
      if (/^```/.test(line)) {
        inCode = false;
        emitCode();
      } else {
        codeLines.push(escapeHtml(line));
      }
      continue;
    }
    if (/^```/.test(line)) {
      flushAll();
      inCode = true;
      codeLines = [];
      continue;
    }
    if (line.charCodeAt(0) === 0 && tableFragments.has(line)) {
      flushAll();
      html.push(tableFragments.get(line));
      continue;
    }
    if (!line.trim()) {
      flushAll();
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushAll();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInline(escapeHtml(heading[2]), artifacts)}</h${level}>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushAll();
      html.push("<hr>");
      continue;
    }
    const quoted = line.match(/^>\s?(.*)$/);
    if (quoted) {
      flushParagraph();
      flushList();
      quote.push(escapeHtml(quoted[1]));
      continue;
    }
    const unordered = line.match(/^\s*[-*+]\s+(.*)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (unordered || ordered) {
      flushParagraph();
      flushQuote();
      const wantOrdered = Boolean(ordered);
      if (!list || list.ordered !== wantOrdered) {
        flushList();
        list = { ordered: wantOrdered, items: [] };
      }
      list.items.push(escapeHtml((unordered || ordered)[1]));
      continue;
    }
    paragraph.push(escapeHtml(line));
  }
  // Fail-closed: an unclosed fence still renders as a code block.
  if (inCode) emitCode();
  flushAll();
  return html.join("\n");
}
