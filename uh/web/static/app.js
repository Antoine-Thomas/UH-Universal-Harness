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
  keyEntryAllowed: false,
  presets: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 401) {
    document.body.textContent = "Session expired: reopen the URL printed by `uh web`.";
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

// --------------------------------------------------------------- rendering --
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function appendMessage(role, text) {
  const box = el("div", `msg ${role}`);
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
  $("messages").appendChild(box);
  $("messages").scrollTop = $("messages").scrollHeight;
  return box;
}

function toolCard(name, args, result) {
  const card = el("details", "tool-card");
  const summary = el("summary");
  summary.appendChild(document.createTextNode(name || "tool"));
  const stateLabel = el("span", "state", result === undefined ? "running…" : "done");
  summary.appendChild(stateLabel);
  card.appendChild(summary);
  const payload = el("pre", null, JSON.stringify({ args, result }, null, 2));
  card.appendChild(payload);

  const isError = result !== undefined && /"error"\s*:/.test(result || "");
  if (isError) {
    card.classList.add("error");
    stateLabel.textContent = "error";
  }
  $("messages").appendChild(card);
  $("messages").scrollTop = $("messages").scrollHeight;
  return card;
}

function fillToolCard(card, content) {
  card.querySelector("pre").textContent = content;
  const label = card.querySelector(".state");
  const failed = /"error"\s*:/.test(content || "");
  label.textContent = failed ? "error" : "done";
  card.classList.toggle("error", failed);
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
      appendMessage(role, message.content || "");
    }
  }
}

function updateGauge(messages) {
  const characters = messages.reduce((sum, m) => sum + (m.content || "").length, 0);
  const tokens = Math.max(1, Math.round(characters / 4));
  const percent = Math.min(100, (tokens / 8000) * 100);
  $("gauge-fill").style.width = `${percent}%`;
  $("gauge-label").textContent = `ctx ~${tokens} tok`;
}

// ------------------------------------------------------------------ status --
async function loadStatus() {
  const status = await api("/api/status");
  $("status-provider").textContent = `${status.provider || "none"} · v${status.version}`;
  $("terminal-warning").hidden = !status.terminal;
  if (status.provider_error) {
    $("status-provider").textContent += " (unavailable)";
  }
}

async function loadModels() {
  const data = await api("/api/models");
  const select = $("model-select");
  select.textContent = "";
  const names = new Set([...(data.ollama || [])]);
  if (data.model) names.add(data.model);
  for (const name of names) {
    const option = el("option", null, name);
    option.value = name;
    if (name === data.model) option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener("change", async () => {
    const payload = { model: select.value };
    if (state.sessionId) payload.session_id = state.sessionId;
    await api("/api/model", { method: "POST", body: JSON.stringify(payload) });
  });
}

// ---------------------------------------------------------------- sessions --
const LAYOUT_KEY = "uh.layout.v1";
const LAYOUT_DEFAULTS = { sidebar: 260, right: 340, sidebarCollapsed: false };
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
  style.setProperty("--sidebar-w", `${prefs.sidebar}px`);
  style.setProperty("--right-w", `${prefs.right}px`);
  $("layout").classList.toggle("sidebar-collapsed", Boolean(prefs.sidebarCollapsed));
  const toggle = $("sidebar-toggle");
  toggle.textContent = prefs.sidebarCollapsed ? "»" : "«";
  toggle.title = prefs.sidebarCollapsed ? "Show the sessions column" : "Hide the sessions column";
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
  folder.title = "File this session in a folder";
  folder.setAttribute("aria-label", "File session");
  folder.addEventListener("click", (event) => {
    event.stopPropagation();
    fileSession(session);
  });
  const remove = el("button", "danger", "🗑");
  remove.title = "Delete this session";
  remove.setAttribute("aria-label", "Delete session");
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
      wireFolderDrop(head);
      list.appendChild(head);
    }
    for (const session of group.sessions) list.appendChild(sessionRow(session));
  }
  if (!data.sessions.length) {
    list.appendChild(el("li", "meta", "no sessions yet"));
  }
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
  renderHistory(data.messages);
  updateGauge(data.messages);
  await loadSessions();
  $("sidebar").classList.remove("open");
}

async function newSession() {
  const data = await api("/api/sessions", { method: "POST", body: JSON.stringify({}) });
  state.sessionId = data.session.id;
  $("messages").textContent = "";
  $("session-title").textContent = "(untitled)";
  updateGauge([]);
  await loadSessions();
  $("input").focus();
}

// -------------------------------------------------------------------- chat --
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

async function sendMessage(text) {
  if (state.streaming) return;
  if (!state.sessionId) await newSession();

  state.streaming = true;
  $("send").disabled = true;
  $("stop").disabled = false;
  appendMessage("user", text);
  let assistant = appendMessage("assistant", "");
  let assistantText = "";
  let openCard = null;

  const controller = new AbortController();
  state.abort = controller;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(state.sessionId)}/chat`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
      signal: controller.signal,
    });
    if (response.status === 401) {
      document.body.textContent = "Session expired: reopen the URL printed by `uh web`.";
      return;
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = parseFrames(buffer, (event, data) => {
        if (event === "token") {
          assistantText += data.text || "";
          assistant.lastChild.textContent = assistantText;
          $("messages").scrollTop = $("messages").scrollHeight;
        } else if (event === "tool_call") {
          openCard = toolCard(data.name, data.args, undefined);
        } else if (event === "tool_result") {
          if (openCard) {
            fillToolCard(openCard, data.content);
            openCard = null;
          } else {
            toolCard(data.name, null, data.content);
          }
        } else if (event === "error") {
          appendMessage("system", data.message || "error");
        } else if (event === "done") {
          const content = assistant.querySelector(".content");
          if (!assistantText && !content.textContent.trim()) {
            assistant.remove();
          } else {
            // The stream was appended as plain text; swap in the Markdown
            // rendering now that the answer is complete.
            content.classList.add("md");
            content.innerHTML = renderMarkdown(assistantText);
          }
        }
      });
    }
  } catch (error) {
    if (error.name !== "AbortError") appendMessage("system", `error: ${error.message}`);
  } finally {
    state.streaming = false;
    state.abort = null;
    $("send").disabled = false;
    $("stop").disabled = true;
    const data = await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/messages?limit=200`);
    renderHistory(data.messages);
    updateGauge(data.messages);
    loadSessions();
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
async function loadProviders() {
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
  renderRouterSettings(await api("/api/router/settings"));
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
  const save = el("button", null, "Save");
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
        ? `ok (HTTP ${result.status})`
        : `failed: ${result.error_kind}${result.status ? ` (HTTP ${result.status})` : ""}`;
      testResult.className = `test-result ${result.ok ? "ok" : "warn"}`;
    } catch (error) {
      testResult.textContent = `failed: ${error.message}`;
      testResult.className = "test-result warn";
    }
  });
  testRow.appendChild(testButton);
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
    const submit = el("button", null, "Add key");
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
  details.appendChild(el("summary", null, "Add a custom OpenAI-compatible provider"));
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
    const result = keyActions(form, "Save");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const typed = token.value;
      token.value = ""; // cleared before the request: never re-displayed
      if (!typed) return;
      try {
        await api("/api/providers/cloudflare/config", {
          method: "POST",
          body: JSON.stringify({ account_id: account.value.trim(), token: typed }),
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

  if (state.keyEntryAllowed) {
    const form = el("form", "custom-form");
    const key = keyField(form, "key", {
      type: "password",
      placeholder: "API key (never shown again)",
    });
    const result = keyActions(form, "Save");
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
    const button = el("button", null, "Import from OmniRoute");
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
  const result = keyActions(form, "Save");

  let native = null;
  const lockFields = (locked) => {
    for (const input of [inputs.name, inputs.base_url, inputs.model]) {
      input.disabled = locked;
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

function showPanelTab(name) {
  const keys = name !== "providers";
  $("tab-keys").classList.toggle("active", keys);
  $("tab-providers").classList.toggle("active", !keys);
  $("tab-keys").setAttribute("aria-selected", String(keys));
  $("tab-providers").setAttribute("aria-selected", String(!keys));
  $("keys-view").hidden = !keys;
  $("providers-view").hidden = keys;
}

function openProviders() {
  $("layout").classList.remove("right-collapsed");
  $("providers-hint").textContent = "loading…";
  loadProviders().catch((error) => {
    $("providers-list").textContent = "";
    $("providers-list").appendChild(el("div", "panel-hint", `error: ${error.message}`));
  });
}

function closeProviders() {
  $("layout").classList.add("right-collapsed");
}

function providersAreOpen() {
  return !$("layout").classList.contains("right-collapsed");
}

// The right column is inline on wide screens and an overlay on narrow ones
// (matchMedia mirrors the CSS breakpoint). Click-outside only makes sense in
// overlay mode; inline, the column is part of the layout and stays put.
function isNarrow() {
  return window.matchMedia("(max-width: 1100px)").matches;
}

function wireProviders() {
  $("providers-open").addEventListener("click", openProviders);
  $("providers-close").addEventListener("click", closeProviders);
  $("tab-keys").addEventListener("click", () => showPanelTab("keys"));
  $("tab-providers").addEventListener("click", () => showPanelTab("providers"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && providersAreOpen()) closeProviders();
  });
  document.addEventListener("click", (event) => {
    if (!isNarrow() || !providersAreOpen()) return;
    if ($("providers-panel").contains(event.target)) return;
    if ($("providers-open").contains(event.target)) return;
    closeProviders();
  });
}

// -------------------------------------------------------------------- boot --
function wireComposer() {
  const input = $("input");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      input.style.height = "auto";
      sendMessage(text);
    }
  });
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendMessage(text);
  });
  $("stop").addEventListener("click", stopTurn);
  $("new-session").addEventListener("click", newSession);
  $("new-folder").addEventListener("click", newFolder);
  $("menu-toggle").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  $("theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  });
}

async function boot() {
  wireLayout();
  wireComposer();
  wirePalette();
  wireSearch();
  wireProviders();
  await loadStatus();
  await loadModels();
  await loadCommands();
  await loadSessions();
  // Narrow screens: the right column starts collapsed (overlay mode).
  if (isNarrow()) closeProviders();
  // Open the most recent session on first load: an empty pane on arrival looks
  // like a broken app, and the session the user left is the one they want.
  const newest = document.querySelector("#session-list li[data-id]");
  if (!state.sessionId && newest) await openSession(newest.dataset.id);
  if (location.hash === "#providers") openProviders();
}

boot().catch((error) => {
  document.body.textContent = `UH web error: ${error.message}`;
});
