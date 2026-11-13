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
  skill: null,
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
    document.body.textContent = "Session expired: reopen the URL printed by `uh web`.";
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
      body: JSON.stringify({
        message: text,
        attachments,
        skill: state.skill ? state.skill.name : "",
      }),
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
      reloadState.textContent = body.error || `reload failed (HTTP ${answer.status})`;
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
      note.textContent = body.error || `blocks failed (HTTP ${answer.status})`;
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
  contextBody.appendChild(
    el(
      "p",
      "decision-note",
      payload > 0
        ? `payload ${payload} / ${budget || "?"} tok · ${data.tool_calls || 0} tool calls`
        : `payload not recorded for this turn (before 1.2.0) · provider counted ${counted} / ${
            budget || "?"
          } tok`
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
    ["telemetry", "telemetry-view"],
    ["data", "data-view"],
  ]) {
    const active = tab === name;
    $(`tab-${tab}`).classList.toggle("active", active);
    $(`tab-${tab}`).setAttribute("aria-selected", String(active));
    $(view).hidden = !active;
  }
  if (name === "models") loadCapabilities().catch(() => {});
  if (name === "uhb") {
    loadMemoryGraph().catch(() => {});
    loadUhb().catch(() => {});
  }
  if (name === "memory") loadMemory().catch(() => {});
  if (name === "decision") loadDecision().catch(() => {});
  if (name === "telemetry") {
    loadTelemetry().catch(() => {});
    startTelemetryPolling();
  } else {
    stopTelemetryPolling();
  }
  if (name === "data") loadData().catch(() => {});
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

function wireProviders() {
  $("providers-open").addEventListener("click", openProviders);
  $("providers-close").addEventListener("click", closeProviders);
  $("tab-keys").addEventListener("click", () => showPanelTab("keys"));
  $("tab-providers").addEventListener("click", () => showPanelTab("providers"));
  $("tab-models").addEventListener("click", () => showPanelTab("models"));
  $("tab-uhb").addEventListener("click", () => showPanelTab("uhb"));
  $("tab-memory").addEventListener("click", () => showPanelTab("memory"));
  $("tab-decision").addEventListener("click", () => showPanelTab("decision"));
  $("tab-telemetry").addEventListener("click", () => showPanelTab("telemetry"));
  $("tab-data").addEventListener("click", () => showPanelTab("data"));
  // One button in the topbar, next to the Dreams one: the data panel is a place,
  // not a tab you have to know about.
  $("data-open").addEventListener("click", () => {
    openProviders();
    showPanelTab("data");
  });
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
    "aria-label": "Tokens per day, local and cloud",
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
  head.appendChild(el("span", null, `Tokens · ${usage.window_days || 7} derniers jours`));
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
  try {
    const data = await api("/api/tools/status");
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
    rows.push([
      "MCP",
      data.mcp && data.mcp.supported ? "ok" : "off",
      (data.mcp && data.mcp.reason) || "non pris en charge",
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
  section.appendChild(
    el(
      "p",
      "data-note",
      "Un voyant dit seulement ce qui a répondu. MCP est absent parce qu'UH n'a pas encore de client MCP, pas parce qu'il est en panne."
    )
  );
  return section;
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
    "aria-label": "Memory sources and what reaches a turn",
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
      "Trait plein = UH consulte cette source pour un tour. Pointillés = il ne peut pas encore : pas d'outil pour le wiki, pas de route pour le RAG, SiYuan passe par UHB."
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

// -------------------------------------------------------------------- boot --
function wireComposer() {
  const input = $("input");
  $("skill-open").addEventListener("click", openSkillMenu);
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
  wireDock();
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

// ------------------------------------------------------------------ splash -- //
// The splash is static markup in index.html so the very first paint has
// something on screen; this only ends it. Two ways out: boot() resolved (the
// shell is wired and the first calls landed), or the timer - a hung API must not
// trap the operator behind a pulsing mark. Never a click: a splash you can
// dismiss is a splash that hid a failure.
const SPLASH_FADE_MS = 380;
const SPLASH_LIMIT_MS = 4000;
let splashGone = false;

function dismissSplash() {
  if (splashGone) return;
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
    document.body.textContent = `UH web error: ${error.message}`;
  });
