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

// Inline formatting, applied to ALREADY escaped text.
function renderInline(escaped) {
  // Inline code first: its content is protected from further formatting.
  const codes = [];
  let text = escaped.replace(/`([^`\n]+)`/g, (_match, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  // Links: [label](url), http/https/mailto only — anything else stays literal.
  text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
    if (!/^(https?:\/\/|mailto:)/i.test(url)) return match;
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  text = text.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  return text.replace(/\u0000(\d+)\u0000/g, (_match, i) => `<code>${codes[Number(i)]}</code>`);
}

// Block structure: fenced code, headings, lists, quotes, hr, paragraphs.
export function renderMarkdown(source) {
  const lines = String(source ?? "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let list = null; // { ordered: bool, items: string[] }
  let quote = [];
  let inCode = false;
  let codeLines = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      const tag = list.ordered ? "ol" : "ul";
      html.push(`<${tag}>${list.items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</${tag}>`);
      list = null;
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      html.push(`<blockquote><p>${renderInline(quote.join(" "))}</p></blockquote>`);
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

  for (const line of lines) {
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
    if (!line.trim()) {
      flushAll();
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushAll();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInline(escapeHtml(heading[2]))}</h${level}>`);
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
