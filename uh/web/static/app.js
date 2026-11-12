// UH web front-end — original code, ES module, no build step, no third party.
// Served under a strict CSP: no inline script, no inline style, no eval.

const state = {
  sessionId: null,
  streaming: false,
  abort: null,
  commands: [],
  paletteIndex: 0,
  filtered: [],
  keyEntryAllowed: false,
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
  box.appendChild(document.createTextNode(text));
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
async function loadSessions() {
  const data = await api("/api/sessions");
  const list = $("session-list");
  list.textContent = "";
  for (const session of data.sessions) {
    const item = el("li");
    item.dataset.id = session.id;
    if (session.id === state.sessionId) item.classList.add("active");
    const row1 = el("div", "row1");
    row1.appendChild(el("span", "sid", session.short_id));
    row1.appendChild(el("span", "meta", `${session.message_count} msg`));
    item.appendChild(row1);
    item.appendChild(el("div", "title", session.title || "(untitled)"));
    item.appendChild(el("div", "meta", session.provider || "-"));
    item.addEventListener("click", () => openSession(session.id));
    list.appendChild(item);
  }
  if (!data.sessions.length) {
    list.appendChild(el("li", "meta", "no sessions yet"));
  }
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
          if (!assistantText && !assistant.textContent.replace("assistant", "").trim()) {
            assistant.textContent = "";
            assistant.remove();
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
    ? "keys are write-only: stored, never displayed"
    : "read-only: key entry needs a loopback bind";
  renderProviders(data.providers || []);
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
  if (row.in_chain) head.appendChild(el("span", "badge ok", "in chain"));
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

function openProviders() {
  $("providers-panel").hidden = false;
  $("providers-hint").textContent = "loading…";
  loadProviders().catch((error) => {
    $("providers-list").textContent = "";
    $("providers-list").appendChild(el("div", "panel-hint", `error: ${error.message}`));
  });
}

function wireProviders() {
  $("providers-open").addEventListener("click", openProviders);
  $("providers-close").addEventListener("click", () => {
    $("providers-panel").hidden = true;
  });
  $("providers-panel").addEventListener("click", (event) => {
    if (event.target === $("providers-panel")) $("providers-panel").hidden = true;
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
  $("menu-toggle").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  $("theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  });
}

async function boot() {
  wireComposer();
  wirePalette();
  wireSearch();
  wireProviders();
  await loadStatus();
  await loadModels();
  await loadCommands();
  await loadSessions();
  // Open the most recent session on first load: an empty pane on arrival looks
  // like a broken app, and the session the user left is the one they want.
  const newest = document.querySelector("#session-list li[data-id]");
  if (!state.sessionId && newest) await openSession(newest.dataset.id);
  if (location.hash === "#providers") openProviders();
}

boot().catch((error) => {
  document.body.textContent = `UH web error: ${error.message}`;
});
