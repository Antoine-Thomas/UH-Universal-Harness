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

async function deleteMessage(box) {
  const messageId = box.dataset.messageId;
  if (!state.sessionId || !messageId) return;
  if (!window.confirm("Delete this message? The rest of the conversation stays.")) return;
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
function appendMessage(role, text, messageId) {
  const box = el("div", `msg ${role}`);
  if (messageId !== undefined && messageId !== null) {
    box.dataset.messageId = messageId;
    const kill = el("button", "message-delete", "×");
    kill.title = "Delete this message";
    kill.setAttribute("aria-label", "Delete message");
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
      appendMessage(role, message.content || "", message.id);
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

// ------------------------------------------------------------- attachments --
// Files are uploaded as they are dropped, not when Send is pressed: the server
// decides inlined vs referenced at upload time, and the chip shows that decision
// before the user commits the turn.
function attachmentChip(attachment) {
  const chip = el("span", "attachment");
  chip.dataset.path = attachment.path;
  chip.appendChild(el("span", "attachment-name", attachment.name));
  chip.appendChild(
    el("span", "attachment-meta", `${Math.round(attachment.size / 1024)} KB · ${attachment.status}`)
  );
  chip.title = attachment.note || attachment.path;
  const drop = el("button", "attachment-remove", "×");
  drop.setAttribute("aria-label", `Remove ${attachment.name}`);
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
}

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  const data = await api("/api/sessions", { method: "POST", body: JSON.stringify({}) });
  state.sessionId = data.session.id;
  await loadSessions();
  return state.sessionId;
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
    throw new Error(detail.detail || `HTTP ${response.status}`);
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
  $("attach").addEventListener("click", () => $("files").click());
  $("files").addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";  // same file twice must upload twice
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
  renderHistory(data.messages);
  updateGauge(data.messages);
  await loadSessions();
  $("sidebar").classList.remove("open");
}

async function newSession() {
  const data = await api("/api/sessions", { method: "POST", body: JSON.stringify({}) });
  state.sessionId = data.session.id;
  state.attachments = [];
  renderAttachments();
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

  const attachments = state.attachments.map((item) => item.path);
  state.streaming = true;
  $("send").disabled = true;
  $("stop").disabled = false;
  appendMessage("user", attachments.length ? `${text}\n\n[${attachments.length} file(s) attached]` : text);
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
      body: JSON.stringify({ message: text, attachments }),
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
    box.appendChild(
      el(
        "p",
        "uhb-offline",
        `UHB not connected (${data.reason || "unreachable"}). Run 'uh web' (it starts UHB) or set UH_UHB_PATH.`
      )
    );
    return;
  }
  const facts = el("dl", "hardware-facts");
  for (const [label, value] of [
    ["mode", data.mode || "?"],
    ["version", data.version || "?"],
    ["harness", data.harness || "?"],
    ["providers", (data.providers || []).join(", ") || "none"],
  ]) {
    facts.appendChild(el("dt", null, label));
    facts.appendChild(el("dd", null, String(value)));
  }
  box.appendChild(el("p", "hardware-sentence", "UHB connected."));
  box.appendChild(facts);

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
  ["🌐", "/web", "Search the web, or open the UI"],
  ["➕", "/new", "Start a new session"],
  ["🧹", "/clear", "Clear this conversation"],
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
  const search = memorySection("Search");
  const form = el("form", "memory-search");
  const input = el("input", "memory-search-input");
  input.placeholder = "search the wiki, MEMORY.md and the sessions";
  input.setAttribute("aria-label", "Search memory");
  const go = el("button", null, "Search");
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
  const ragState = data.rag || {};
  rag.appendChild(
    stateBlock(
      [
        ["index", ragState.available ? "built" : "not built"],
        ["size", ragState.index_mb ? `${ragState.index_mb} MB` : ""],
        ["chunks", ragState.chunks],
        ["documents", ragState.documents],
        ["modified", ragState.modified],
      ],
      ragState.reason
    )
  );
  if (!ragState.available) {
    rag.appendChild(
      el(
        "p",
        "panel-hint",
        "RAG not built yet - the index is compiled from your own notes; see uhb/services/rag/README.md."
      )
    );
  }
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

function renderDecision(data, reload) {
  const box = $("decision-panel");
  box.textContent = "";

  if (!data.available) {
    box.appendChild(el("p", "panel-hint", data.reason || "nothing recorded yet"));
    return;
  }

  // 1 - the prompt, as classified (or the honest absence)
  const prompt = data.prompt || {};
  const promptBody = el("div", "decision-body");
  if (prompt.complexity) {
    promptBody.appendChild(el("p", "decision-headline", String(prompt.complexity)));
    if (prompt.mode) promptBody.appendChild(el("p", "decision-sub", `mode ${prompt.mode}`));
    if (prompt.reason) promptBody.appendChild(el("p", "decision-note", prompt.reason));
  } else {
    promptBody.appendChild(el("p", "decision-note", prompt.note || "UHB was not consulted"));
  }
  box.appendChild(decisionBlock("Prompt", promptBody));

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
      el("p", "decision-note", `chosen ${model.chosen || model.model} → served ${model.served}`)
    );
  }
  box.appendChild(decisionBlock("Model", modelBody));

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
    chainBody.appendChild(el("p", "decision-note", "no chain: UHB did not plan this turn"));
  }
  box.appendChild(decisionBlock("UHB chain", chainBody));

  // 4 - context: what was sent, against the budget
  const context = data.context || {};
  const usage = data.usage || {};
  const contextBody = el("div", "decision-body");
  const track = el("div", "decision-bar");
  const fill = el("div", "decision-bar-fill");
  // The bar compares the payload the pruner measured with the budget it had to
  // respect. The usage counters below are a different quantity: they sum every
  // call of the turn, so using them here would show a false overflow.
  const payload = Number(context.payload_tokens || 0);
  const budget = Number(context.budget || 0);
  fill.style.width = `${budget > 0 ? Math.min(100, Math.round((payload / budget) * 100)) : 0}%`;
  track.appendChild(fill);
  contextBody.appendChild(track);
  contextBody.appendChild(
    el(
      "p",
      "decision-note",
      `payload ${payload} / ${budget || "?"} tok · ${data.tool_calls || 0} tool calls`
    )
  );
  if (usage.reported) {
    contextBody.appendChild(
      el("p", "decision-note", `in ${usage.prompt} · out ${usage.completion}`)
    );
  } else {
    contextBody.appendChild(el("p", "decision-note", "the provider reported no counters"));
  }
  if (context.mode === "summary" && context.summarised_exchanges) {
    contextBody.appendChild(
      el(
        "p",
        "decision-note",
        `${context.summarised_exchanges} exchanges summarised · ${context.kept_exchanges} kept`
      )
    );
  } else if (context.mode === "trim") {
    // The middle was dropped, not summarised: saying "summarised" would describe
    // text the model never received.
    contextBody.appendChild(
      el("p", "decision-note", "the middle was dropped without a summary (budget too small)")
    );
  }
  box.appendChild(decisionBlock("Context", contextBody));

  // 5 - the summary itself, collapsed, because it replaced messages
  if (context.summary) {
    const summaryBody = el("div", "decision-body");
    const details = el("details", "decision-summary");
    details.appendChild(el("summary", null, `summary (replaces ${context.summarised_exchanges} exchanges)`));
    details.appendChild(el("pre", "memory-reader", context.summary));
    summaryBody.appendChild(details);
    box.appendChild(decisionBlock("Summary", summaryBody));
  }

  // 6 - duration, this turn and the average
  const turn = data.turn || {};
  const durationBody = el("div", "decision-body");
  durationBody.appendChild(el("p", "decision-headline", `${(Number(turn.duration_ms || 0) / 1000).toFixed(2)}s`));
  durationBody.appendChild(
    el(
      "p",
      "decision-note",
      `average of the last ${turn.turns || 0}: ${(Number(turn.average_ms || 0) / 1000).toFixed(2)}s`
    )
  );
  box.appendChild(decisionBlock("Duration", durationBody));

  // A plan UHB would give now, clearly marked as not applied.
  const ask = el("button", "hardware-pull", "Ask UHB about this prompt");
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
    box.appendChild(el("p", "panel-hint", "open a session to see how its last turn was decided"));
    return;
  }
  try {
    const data = await api(`/api/sessions/${state.sessionId}/decision`);
    renderDecision(data);
  } catch (error) {
    box.appendChild(el("p", "panel-hint", `decision: ${error.message}`));
  }
}

function showPanelTab(name) {
  for (const [tab, view] of [
    ["keys", "keys-view"],
    ["providers", "providers-view"],
    ["models", "models-view"],
    ["uhb", "uhb-view"],
    ["memory", "memory-view"],
    ["decision", "decision-view"],
  ]) {
    const active = tab === name;
    $(`tab-${tab}`).classList.toggle("active", active);
    $(`tab-${tab}`).setAttribute("aria-selected", String(active));
    $(view).hidden = !active;
  }
  if (name === "models") loadCapabilities().catch(() => {});
  if (name === "uhb") loadUhb().catch(() => {});
  if (name === "memory") loadMemory().catch(() => {});
  if (name === "decision") loadDecision().catch(() => {});
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
  return window.matchMedia("(max-width: 1200px)").matches;
}

function wireProviders() {
  $("providers-open").addEventListener("click", openProviders);
  $("providers-close").addEventListener("click", closeProviders);
  $("tab-keys").addEventListener("click", () => showPanelTab("keys"));
  $("tab-providers").addEventListener("click", () => showPanelTab("providers"));
  $("tab-models").addEventListener("click", () => showPanelTab("models"));
  $("tab-uhb").addEventListener("click", () => showPanelTab("uhb"));
  $("tab-memory").addEventListener("click", () => showPanelTab("memory"));
  $("tab-decision").addEventListener("click", () => showPanelTab("decision"));
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

async function boot() {
  wireLayout();
  wireAttachments();
  wireChips();
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
  showRemoteBanner().catch(() => {});
  // Open the most recent session on first load: an empty pane on arrival looks
  // like a broken app, and the session the user left is the one they want.
  const newest = document.querySelector("#session-list li[data-id]");
  if (!state.sessionId && newest) await openSession(newest.dataset.id);
  if (location.hash === "#providers") openProviders();
}

boot().catch((error) => {
  document.body.textContent = `UH web error: ${error.message}`;
});
